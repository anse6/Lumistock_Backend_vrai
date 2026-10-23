"""Caisse : ventes au comptoir par un caissier, sessions de caisse et statistiques.

Règles métier
-------------
- Le caissier vend depuis le stock du MAGASIN, uniquement caisse ouverte.
- Le prix de la fiche produit s'applique toujours ; une remise n'est possible
  que si l'admin l'a autorisée (paramètres de caisse).
- Vente à crédit : seulement pour un client enregistré, jamais pour le client
  de passage.
- Le caissier peut annuler SA vente pendant DELAI_ANNULATION_MINUTES ; ensuite
  seul un admin ou un manager peut l'annuler.
- Fermeture de caisse : espèces attendues = fond de caisse + espèces reçues
  − espèces rendues ; l'écart avec le montant compté est enregistré.

Les montants sont lus sur les ventes (paiements, remboursements, annulations)
avec les mêmes règles que le rapport des encaissements (rapport_service).
Les dates sont enregistrées en UTC ; les périodes affichées sont locales.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from models.caisse_session_model import CaisseSessionModel, FERMEE, OUVERTE
from models.client_model import ClientModel
from models.livraison_model import LivraisonModel
from models.magasin_model import MagasinModel
from models.produit_model import ProduitModel
from models.user_model import UserModel
from services.rapport_service import _date, facture, heure_locale

DELAI_ANNULATION_MINUTES = 15
MODES_PAIEMENT = ("cash", "om", "momo", "bancaire")
LIBELLES_MODES = {"cash": "Espèces", "om": "Orange Money", "momo": "MTN MoMo", "bancaire": "Banque / carte",
                  "credit_client": "Crédit client"}
_CANONIQUE = {"especes": "cash", "espèces": "cash", "cash": "cash", "om": "om", "momo": "momo",
              "bancaire": "bancaire", "virement": "bancaire", "carte": "bancaire", "credit_client": "credit_client"}


class ErreurCaisse(Exception):
    """Erreur métier renvoyée telle quelle au caissier."""

    def __init__(self, message, statut=400):
        super().__init__(message)
        self.message, self.statut = message, statut


# --------------------------------------------------------------------------- utilitaires
def _num(valeur):
    try:
        return float(valeur or 0)
    except (TypeError, ValueError):
        return 0.0


def _arrondi(valeur):
    v = round(float(valeur or 0), 2)
    return int(v) if v == int(v) else v


def _mode(valeur):
    return _CANONIQUE.get(str(valeur or "cash").strip().lower(), "cash")


def _iso_utc(d):
    """Date UTC enregistrée → texte ISO avec « Z » (le navigateur l'affiche en heure locale)."""
    d = _date(d)
    return d.isoformat() + "Z" if d else None


def _vers_utc(locale):
    """Date locale (sans fuseau) → date UTC sans fuseau, comme en base."""
    return locale.astimezone(timezone.utc).replace(tzinfo=None)


def _nom(user):
    if not user:
        return "Utilisateur supprimé"
    return f"{(user.get('first_name') or '').strip()} {(user.get('name') or '').strip()}".strip() or user.get("email", "")


def _oid(valeur):
    return ObjectId(str(valeur)) if valeur and ObjectId.is_valid(str(valeur)) else None


def client_de_passage_id(magasin_id):
    return ClientModel.get_or_create_default_client(str(magasin_id))


# --------------------------------------------------------------------------- paramètres
def parametres_caisse(magasin):
    """Paramètres de caisse du magasin (valeurs par défaut si jamais réglés)."""
    # Réglé dans Paramètres > Accès (droit « Accorder des remises à la caisse »).
    from services.permissions_service import roles_du_droit
    return {
        "remises_autorisees": "caissier" in roles_du_droit(magasin, "ventes.remises_caisse"),
        "delai_annulation_minutes": DELAI_ANNULATION_MINUTES,
    }


def maj_parametres(magasin_id, data):
    if "remises_autorisees" not in (data or {}) or not isinstance(data["remises_autorisees"], bool):
        raise ErreurCaisse("Indiquez « remises_autorisees » : vrai ou faux.")
    from services.permissions_service import enregistrer
    magasin = MagasinModel.get_collection().find_one({"_id": ObjectId(str(magasin_id))})
    enregistrer(magasin, {"ventes.remises_caisse": {"caissier": data["remises_autorisees"]}})
    return parametres_caisse(MagasinModel.get_collection().find_one({"_id": ObjectId(str(magasin_id))}))


# --------------------------------------------------------------------------- mouvements d'argent
def _mouvements(caissier_id, magasin_id, debut, fin, session_id=None):
    """Argent reçu et rendu PAR ce caissier entre debut et fin (UTC).

    - paiement à la vente : compté pour la session de la vente (ou, sans
      session, si la vente a été faite dans la période) ;
    - paiement complémentaire : compté s'il a été reçu par ce caissier dans
      la période ;
    - remboursement ou annulation : compté en sortie s'il a été rendu par ce
      caissier dans la période (sauf crédit sur le compte client : aucun
      argent ne sort de la caisse).
    """
    caissier = str(caissier_id)
    sid = str(session_id) if session_id else None
    entrees, sorties = defaultdict(float), defaultdict(float)
    operations = []
    query = {
        "magasin_id": ObjectId(str(magasin_id)),
        "livreur_id": ObjectId(caissier),
        "$or": [{"created_at": {"$gte": debut}}, {"updated_at": {"$gte": debut}}, {"deleted_at": {"$gte": debut}}],
    }
    if sid:
        query["$or"].append({"session_caisse_id": ObjectId(sid)})

    def dans_periode(quand):
        return quand is not None and debut <= quand <= fin

    def operation(vente, sens, mode, montant, quand, nature):
        operations.append({"vente_id": str(vente["_id"]), "facture": facture(vente["_id"]), "sens": sens,
                           "mode": mode, "mode_libelle": LIBELLES_MODES.get(mode, mode), "montant": _arrondi(montant),
                           "date": _iso_utc(quand), "nature": nature})

    for vente in LivraisonModel.get_collection().find(query):
        cree_le = _date(vente.get("created_at"))
        de_la_periode = (str(vente.get("session_caisse_id")) == sid) if sid else dans_periode(cree_le)
        paiements = vente.get("paiements") or []
        if not paiements and de_la_periode:
            recu = _num(vente.get("montant_paye")) + sum(_num(r.get("montant")) for r in vente.get("remboursements") or [])
            if recu > 0:
                paiements = [{"methode": vente.get("methode_paiement") or "cash", "montant": recu}]
        for p in paiements:
            montant = _num(p.get("montant")) + _num(p.get("surplus_credite"))
            if montant <= 0:
                continue
            if p.get("date"):
                quand = _date(p["date"])
                if str(p.get("par") or caissier) != caissier or not dans_periode(quand):
                    continue
                nature = "Paiement complémentaire"
            else:
                if not de_la_periode:
                    continue
                quand, nature = cree_le, "Paiement à la vente"
            mode = _mode(p.get("methode"))
            entrees[mode] += montant
            operation(vente, "entree", mode, montant, quand, nature)
        for r in vente.get("remboursements") or []:
            quand, montant, mode = _date(r.get("date")), _num(r.get("montant")), _mode(r.get("mode"))
            if str(r.get("par")) == caissier and dans_periode(quand) and montant > 0 and mode != "credit_client":
                sorties[mode] += montant
                operation(vente, "sortie", mode, montant, quand, "Remboursement")
        annulation = vente.get("annulation") or {}
        if vente.get("deleted_at") and str(annulation.get("par")) == caissier:
            quand, montant = _date(annulation.get("date")), _num(annulation.get("montant_rendu"))
            mode = _mode(annulation.get("mode"))
            if dans_periode(quand) and montant > 0 and mode != "credit_client":
                sorties[mode] += montant
                operation(vente, "sortie", mode, montant, quand, "Annulation de vente")

    operations.sort(key=lambda o: o["date"] or "")
    modes = [{
        "mode": m, "libelle": LIBELLES_MODES[m], "entrees": _arrondi(entrees[m]), "sorties": _arrondi(sorties[m]),
        "net": _arrondi(entrees[m] - sorties[m]),
    } for m in MODES_PAIEMENT if entrees[m] or sorties[m]]
    return {
        "modes": modes,
        "total_entrees": _arrondi(sum(entrees.values())),
        "total_sorties": _arrondi(sum(sorties.values())),
        "especes_entrees": _arrondi(entrees["cash"]),
        "especes_sorties": _arrondi(sorties["cash"]),
        "operations": operations,
    }


# --------------------------------------------------------------------------- sessions de caisse
def resume_session(session):
    """Chiffres de la session (en direct si elle est ouverte)."""
    debut = session["ouverte_le"]
    fin = session.get("fermee_le") or datetime.utcnow()
    argent = _mouvements(session["caissier_id"], session["magasin_id"], debut, fin, session["_id"])
    # magasin_id et livreur_id sont des colonnes indexées : seules les ventes du caissier sont lues.
    ventes = list(LivraisonModel.get_collection().find({"magasin_id": session["magasin_id"],
                                                        "livreur_id": session["caissier_id"],
                                                        "session_caisse_id": session["_id"]}))
    valides = [v for v in ventes if not v.get("deleted_at")]
    ca = sum(_num(v.get("montant_produits", v.get("montant_total"))) for v in valides)
    credit = sum(max(_num(v.get("montant_total")) - _num(v.get("montant_paye")), 0) for v in valides)
    attendu = _num(session.get("fond_initial")) + argent["especes_entrees"] - argent["especes_sorties"]
    return {
        "ventes": len(valides),
        "annulations": len(ventes) - len(valides),
        "chiffre_affaires": _arrondi(ca),
        "articles": int(sum(_num(v.get("quantite")) for v in valides)),
        "panier_moyen": _arrondi(ca / len(valides)) if valides else 0,
        "credit_accorde": _arrondi(credit),
        "fond_initial": _arrondi(session.get("fond_initial")),
        "especes_attendues": _arrondi(attendu),
        **{k: argent[k] for k in ("modes", "total_entrees", "total_sorties", "especes_entrees", "especes_sorties")},
        "operations": argent["operations"][-50:],
    }


def serialiser_session(session, avec_resume=True, noms=None):
    noms = noms if noms is not None else {}

    def nom(uid):
        cle = str(uid or "")
        if cle and cle not in noms:
            noms[cle] = _nom(UserModel.get_user_by_id(cle))
        return noms.get(cle)

    donnees = {
        "id": str(session["_id"]),
        "caissier_id": str(session["caissier_id"]),
        "caissier": nom(session["caissier_id"]),
        "statut": session["statut"],
        "fond_initial": _arrondi(session.get("fond_initial")),
        "note_ouverture": session.get("note_ouverture"),
        "ouverte_le": _iso_utc(session.get("ouverte_le")),
        "fermee_le": _iso_utc(session.get("fermee_le")),
        "fermee_par": nom(session.get("fermee_par_id")) if session.get("fermee_par_id") else None,
        "fermee_par_caissier": str(session.get("fermee_par_id") or "") == str(session["caissier_id"]),
        "montant_compte": _arrondi(session["montant_compte"]) if session.get("montant_compte") is not None else None,
        "ecart": _arrondi(session["ecart"]) if session.get("ecart") is not None else None,
        "note_fermeture": session.get("note_fermeture"),
    }
    if avec_resume:
        donnees["resume"] = session.get("resume") if session["statut"] == FERMEE and session.get("resume") else resume_session(session)
    return donnees


def ouvrir_session(user, fond_initial, note=None):
    if CaisseSessionModel.session_ouverte(user["_id"]):
        raise ErreurCaisse("Votre caisse est déjà ouverte.", 409)
    fond = _num(fond_initial)
    if fond < 0:
        raise ErreurCaisse("Le fond de caisse ne peut pas être négatif.")
    return CaisseSessionModel.ouvrir(user["magasin_id"], user["_id"], fond, note)


def fermer_session(session, par_user, montant_compte, note=None):
    if session["statut"] != OUVERTE:
        raise ErreurCaisse("Cette caisse est déjà fermée.", 409)
    try:
        compte = float(montant_compte)
    except (TypeError, ValueError):
        raise ErreurCaisse("Indiquez le montant des espèces comptées dans la caisse.")
    if compte < 0:
        raise ErreurCaisse("Le montant compté ne peut pas être négatif.")
    if str(par_user["_id"]) != str(session["caissier_id"]) and not (note or "").strip():
        raise ErreurCaisse("Indiquez un motif pour fermer la caisse d'un caissier.")
    resume = resume_session(session)
    ecart = round(compte - resume["especes_attendues"], 2)
    ferme = CaisseSessionModel.fermer(session["_id"], par_user["_id"], compte, resume, ecart, note)
    if not ferme:
        raise ErreurCaisse("Cette caisse est déjà fermée.", 409)
    return ferme


def session_requise(user):
    session = CaisseSessionModel.session_ouverte(user["_id"])
    if not session:
        raise ErreurCaisse("Ouvrez votre caisse avant d'encaisser.", 409)
    return session


# --------------------------------------------------------------------------- catalogue et clients
def catalogue(magasin_id):
    """Produits vendables du magasin, par catégorie, SANS prix d'achat ni marge."""
    from models.categorie_model import CategorieModel
    from flask import request

    categories, noms = {}, {}
    for p in ProduitModel.get_all_by_magasin(str(magasin_id)):
        cat_id = str(p.get("categorie_id") or "")
        if cat_id not in noms:
            cat = CategorieModel.get_categorie_by_id(cat_id) if ObjectId.is_valid(cat_id) else None
            noms[cat_id] = (cat or {}).get("nom") or "Sans catégorie"
        produit = {
            "id": str(p["_id"]),
            "nom": p.get("nom") or "Produit",
            "prix": _num(p.get("prix")),
            "stock": _num(p.get("quantite")),
            "unite": p.get("unite") or "",
            "code_barre": p.get("code_barre") or None,
            "image": f"{request.host_url}uploads/images/{p['image']}" if p.get("image") else None,
            "conditionnements": [{
                "id": str(c.get("_id")), "nom": c.get("nom") or "Lot", "facteur": int(_num(c.get("facteur")) or 1),
                "prix": _num(c.get("prix_conditionnement")),
            } for c in p.get("conditionnements") or [] if _num(c.get("facteur")) >= 1 and _num(c.get("prix_conditionnement")) > 0],
        }
        categories.setdefault(cat_id, {"id": cat_id or None, "nom": noms[cat_id], "produits": []})["produits"].append(produit)
    liste = sorted(categories.values(), key=lambda c: (c["id"] is None, c["nom"].lower()))
    for c in liste:
        c["produits"].sort(key=lambda p: p["nom"].lower())
    return liste


def clients(magasin_id, recherche=None, limite=30):
    passage = client_de_passage_id(magasin_id)
    query = {"magasin_id": ObjectId(str(magasin_id)), "deleted_at": {"$exists": False}, "_id": {"$ne": ObjectId(passage)}}
    if recherche:
        import re
        motif = re.escape(recherche.strip())
        query["$or"] = [{"nom": {"$regex": motif, "$options": "i"}}, {"telephone": {"$regex": motif}}]
    resultat = ClientModel.get_collection().find(query).sort("nom", 1).limit(limite)
    return {
        "client_de_passage_id": passage,
        "clients": [{"id": str(c["_id"]), "nom": c.get("nom") or "Client", "telephone": c.get("telephone") or "",
                     "credit": _arrondi(c.get("montant_en_caisse"))} for c in resultat],
    }


# --------------------------------------------------------------------------- vente
def _preparer(data, user, parametres):
    """Données de vente nettoyées pour un caissier + (paiement complet exigé ?)."""
    data = data or {}
    produits = data.get("produits")
    if not isinstance(produits, list) or not produits:
        raise ErreurCaisse("Le panier est vide.")
    # Prix de la fiche produit imposé : un prix saisi est ignoré.
    produits = [{k: v for k, v in ligne.items() if k != "prix"} if isinstance(ligne, dict) else ligne for ligne in produits]

    passage = client_de_passage_id(user["magasin_id"])
    client_id = str(data.get("client_id") or "") or passage
    est_passage = client_id == passage

    reductions = data.get("reductions") or []
    if reductions and not parametres["remises_autorisees"]:
        raise ErreurCaisse("Les remises ne sont pas autorisées à la caisse. Demandez à un manager.", 403)

    statut = data.get("statut_paiement") or "payé"
    if statut not in ("payé", "partiellement payé", "non payé"):
        raise ErreurCaisse("Statut de paiement invalide.")
    paiements = []
    if statut != "non payé":
        for p in data.get("paiements") or []:
            mode = _mode((p or {}).get("methode"))
            if mode not in MODES_PAIEMENT:
                raise ErreurCaisse("Mode de paiement invalide.")
            montant = _num(p.get("montant"))
            if montant < 0:
                raise ErreurCaisse("Un montant encaissé ne peut pas être négatif.")
            if montant > 0:
                paiements.append({"methode": mode, "montant": round(montant, 2)})
        if not paiements:
            raise ErreurCaisse("Indiquez le montant encaissé et son mode de paiement.")
    if est_passage and statut != "payé":
        raise ErreurCaisse("Vente à crédit impossible pour un client de passage : choisissez un client enregistré.")

    propre = {
        "produits": produits,
        "client_id": client_id,
        "statut_paiement": statut,
        "paiements": paiements,
        # Toujours explicite : sans montant, le modèle considère la vente comme entièrement payée.
        "montant_paye": round(sum(p["montant"] for p in paiements), 2),
        "methode_paiement": paiements[0]["methode"] if paiements else "cash",
        "reductions": reductions,
        "appliquer_tva": bool(data.get("appliquer_tva")),
        "magasin_id": str(user["magasin_id"]),
        "manager_id": str(user["_id"]),
    }
    return propre, est_passage


def apercu(data, user):
    """Total calculé par le serveur pour le panier : la caisse affiche exactement ce qui sera facturé."""
    from services.livraison_service import construire_lignes_magasin
    from services.lignes_vente import valider_lignes_vente
    from services.reductions_service import appliquer_reductions
    from services.tva_service import decimales_magasin, tva_vente

    magasin = MagasinModel.get_magasin_by_id(str(user["magasin_id"]))
    parametres = parametres_caisse(magasin)
    data = dict(data or {})
    data.setdefault("statut_paiement", "payé")
    data["paiements"] = [{"methode": "cash", "montant": 1}]  # l'aperçu ne dépend pas de l'encaissement
    propre, _ = _preparer(data, user, parametres)
    erreur = valider_lignes_vente(propre["produits"])
    if erreur:
        raise ErreurCaisse(erreur[0], erreur[1])
    lignes, erreur = construire_lignes_magasin(propre["produits"])
    if erreur:
        raise ErreurCaisse(erreur[0]["error"], erreur[1])
    brut = round(sum(int(l["quantite"]) * float(l["prix"]) for l in lignes), 2)
    try:
        detail, total_reductions = appliquer_reductions(propre["reductions"], brut, decimales_magasin(propre["magasin_id"]))
        net = round(brut - total_reductions, 2)
        appliquer, taux, tva = tva_vente(propre, net)
    except ValueError as e:
        raise ErreurCaisse(str(e))
    total = round(net + tva, 2)
    client = ClientModel.get_client_by_id(propre["client_id"])
    credit = min(_num((client or {}).get("montant_en_caisse")), total)
    return {
        "montant_brut": _arrondi(brut), "reductions": detail, "total_reductions": _arrondi(total_reductions),
        "appliquer_tva": appliquer, "taux_tva": taux, "montant_tva": _arrondi(tva), "montant_total": _arrondi(total),
        "credit_client_utilise": _arrondi(credit), "a_encaisser": _arrondi(total - credit),
        "articles": int(sum(int(l["quantite"]) for l in lignes)),
    }


def vendre(data, user):
    from services.livraison_service import creer_livraison_by_manager

    session = session_requise(user)
    magasin = MagasinModel.get_magasin_by_id(str(user["magasin_id"]))
    propre, est_passage = _preparer(data, user, parametres_caisse(magasin))
    resultat, statut = creer_livraison_by_manager(
        propre, vendeur_role="caissier", session_caisse_id=str(session["_id"]), paiement_complet_requis=est_passage)
    if statut != 201:
        erreur = resultat.get("error") if isinstance(resultat, dict) else None
        if isinstance(erreur, dict):
            erreur = " ".join(str(m) for v in erreur.values() for m in (v if isinstance(v, list) else [v]))
        raise ErreurCaisse(erreur or "La vente n'a pas pu être enregistrée.", statut)
    vente = LivraisonModel.get_livraison_by_id(resultat["livraison_id"])
    return serialiser_vente(vente)


def _vente_du_caissier(user, vente_id, inclure_annulees=False):
    vente = LivraisonModel.get_collection().find_one({"_id": _oid(vente_id)}) if _oid(vente_id) else None
    if (not vente or str(vente.get("livreur_id")) != str(user["_id"])
            or str(vente.get("magasin_id")) != str(user["magasin_id"])):
        raise ErreurCaisse("Vente introuvable.", 404)
    if vente.get("deleted_at") and not inclure_annulees:
        raise ErreurCaisse("Cette vente a été annulée.", 409)
    return vente


def limite_annulation(vente):
    cree_le = _date(vente.get("created_at"))
    return cree_le + timedelta(minutes=DELAI_ANNULATION_MINUTES) if cree_le else None


def serialiser_vente(vente, noms=None):
    from services.livraison_service import etat_vente

    noms = noms if noms is not None else {}
    client_id = str(vente.get("client_id") or "")
    if client_id not in noms:
        c = ClientModel.get_client_by_id(client_id) if ObjectId.is_valid(client_id) else None
        passage = not c or client_id == client_de_passage_id(vente["magasin_id"])
        noms[client_id] = {"id": client_id, "nom": "Client de passage" if passage else (c.get("nom") or "Client"),
                           "telephone": "" if passage else (c.get("telephone") or ""), "passage": passage}
    lignes = []
    for l in vente.get("produits") or []:
        quantite, prix = _num(l.get("quantite")), _num(l.get("prix"))
        ligne = {"produit_id": str(l.get("produit_id")), "nom": l.get("nom") or "Produit", "quantite": _arrondi(quantite),
                 "prix_unitaire": _arrondi(prix), "total": _arrondi(quantite * prix)}
        if l.get("is_conditionne") and _num(l.get("facteur")) > 0:
            facteur = int(_num(l.get("facteur")))
            cond = ProduitModel.get_conditionnement_by_id(str(l.get("produit_id")), l.get("conditionnement_id"))
            ligne.update({"conditionnement": (cond or {}).get("nom") or "Lot", "facteur": facteur,
                          "quantite_conditionnement": _arrondi(quantite / facteur), "prix_conditionnement": _arrondi(prix * facteur)})
        lignes.append(ligne)
    limite = limite_annulation(vente)
    total, paye = _num(vente.get("montant_total")), _num(vente.get("montant_paye"))
    vendeur_id = str(vente.get("livreur_id") or "")
    if vendeur_id not in noms:
        noms[vendeur_id] = _nom(UserModel.get_user_by_id(vendeur_id)) if ObjectId.is_valid(vendeur_id) else ""
    return {
        "id": str(vente["_id"]),
        "numero": facture(vente["_id"]),
        "date": _iso_utc(vente.get("created_at")),
        "client": noms[client_id],
        "vendeur": noms[vendeur_id],
        "lignes": lignes,
        "articles": int(_num(vente.get("quantite"))),
        "montant_brut": _arrondi(vente.get("montant_brut", vente.get("montant_produits"))),
        "reductions": vente.get("reductions") or [],
        "total_reductions": _arrondi(vente.get("total_reductions")),
        "montant_ht": _arrondi(vente.get("montant_produits", total)),
        "appliquer_tva": bool(vente.get("appliquer_tva")),
        "taux_tva": _num(vente.get("taux_tva")),
        "montant_tva": _arrondi(vente.get("montant_tva")),
        "montant_total": _arrondi(total),
        "montant_paye": _arrondi(paye),
        "credit_client_utilise": _arrondi(vente.get("montant_deduit_caisse")),
        "reste": _arrondi(max(total - paye, 0)),
        "statut_paiement": vente.get("statut_paiement"),
        "paiements": [{"mode": _mode(p.get("methode")), "libelle": LIBELLES_MODES.get(_mode(p.get("methode"))),
                       "montant": _arrondi(_num(p.get("montant"))), "date": _iso_utc(p.get("date")) if p.get("date") else None}
                      for p in vente.get("paiements") or [] if _num(p.get("montant")) > 0],
        "annulee": bool(vente.get("deleted_at")),
        "etat": etat_vente(vente),
        "annulation": vente.get("annulation"),
        "annulable_jusqua": _iso_utc(limite) if limite and not vente.get("deleted_at") and limite > datetime.utcnow() else None,
        "session_caisse_id": str(vente["session_caisse_id"]) if vente.get("session_caisse_id") else None,
    }


def annuler_vente(user, vente_id, data):
    from services.livraison_service import supprimer_livraison_service

    vente = _vente_du_caissier(user, vente_id)
    limite = limite_annulation(vente)
    if not limite or datetime.utcnow() > limite:
        raise ErreurCaisse(f"Le délai de {DELAI_ANNULATION_MINUTES} minutes est dépassé : seul un manager ou un "
                           "administrateur peut annuler cette vente.", 403)
    session_requise(user)
    donnees = dict(data or {})
    if not str(donnees.get("motif") or "").strip():
        raise ErreurCaisse("Indiquez le motif de l'annulation.")
    resultat, statut = supprimer_livraison_service(str(vente["_id"]), donnees, str(user["_id"]))
    if statut != 200:
        raise ErreurCaisse(resultat.get("error") or "L'annulation a échoué.", statut)
    return resultat


def ajouter_paiement(user, vente_id, montant, methode):
    from services.livraison_service import ajouter_paiement_service

    vente = _vente_du_caissier(user, vente_id)
    session_requise(user)
    if _mode(methode) not in MODES_PAIEMENT:
        raise ErreurCaisse("Mode de paiement invalide.")
    try:
        montant = float(montant)
    except (TypeError, ValueError):
        raise ErreurCaisse("Montant invalide.")
    reste = max(_num(vente.get("montant_total")) - _num(vente.get("montant_paye")), 0)
    if montant <= 0:
        raise ErreurCaisse("Le montant doit être positif.")
    if montant > reste + 0.01:
        raise ErreurCaisse("Le montant dépasse ce qui reste à payer : inscrivez la somme gardée, pas la somme reçue.")
    resultat, statut = ajouter_paiement_service(str(vente["_id"]), montant, methode=_mode(methode), par=str(user["_id"]))
    if statut != 200:
        raise ErreurCaisse(resultat.get("error") or resultat.get("message") or "Le paiement n'a pas pu être enregistré.", statut)
    return serialiser_vente(LivraisonModel.get_livraison_by_id(str(vente["_id"])))


# --------------------------------------------------------------------------- historique et statistiques
def fenetre(periode="jour", debut=None, fin=None):
    """(début, fin) LOCAUX de la période et de la période précédente de même durée."""
    maintenant = datetime.now()
    if periode == "personnalisee" and debut:
        d = (_date(debut) or maintenant).replace(hour=0, minute=0, second=0, microsecond=0)
        f = (_date(fin) or d).replace(hour=23, minute=59, second=59, microsecond=0)
        if f < d:
            d, f = f.replace(hour=0, minute=0, second=0), d.replace(hour=23, minute=59, second=59)
    elif periode == "semaine":
        d = (maintenant - timedelta(days=maintenant.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        f = d + timedelta(days=7) - timedelta(seconds=1)
    elif periode == "mois":
        d = maintenant.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        f = (d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)) - timedelta(seconds=1)
    else:
        periode = "jour"
        d = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
        f = d + timedelta(days=1) - timedelta(seconds=1)
    duree = f - d + timedelta(seconds=1)
    return periode, d, f, d - duree, d - timedelta(seconds=1)


def _ventes_periode(caissier_id, magasin_id, d_local, f_local, annulees=False):
    query = {"magasin_id": ObjectId(str(magasin_id)), "livreur_id": ObjectId(str(caissier_id)),
             "created_at": {"$gte": _vers_utc(d_local), "$lte": _vers_utc(f_local)}}
    if not annulees:
        query["deleted_at"] = {"$exists": False}
    return list(LivraisonModel.get_collection().find(query).sort("created_at", -1))


def _kpis(ventes):
    ca = sum(_num(v.get("montant_produits", v.get("montant_total"))) for v in ventes)
    return {"chiffre_affaires": _arrondi(ca), "ventes": len(ventes),
            "articles": int(sum(_num(v.get("quantite")) for v in ventes)),
            "panier_moyen": _arrondi(ca / len(ventes)) if ventes else 0}


def _variation(actuel, precedent):
    if not precedent:
        return None
    return round((actuel - precedent) / precedent * 100, 1)


def historique(user, periode="jour", debut=None, fin=None, statut=None, recherche=None):
    periode, d, f, _, _ = fenetre(periode, debut, fin)
    ventes = _ventes_periode(user["_id"], user["magasin_id"], d, f, annulees=True)
    noms = {}
    liste = [serialiser_vente(v, noms) for v in ventes]
    if statut == "annulee":
        liste = [v for v in liste if v["annulee"]]
    elif statut == "credit":
        liste = [v for v in liste if not v["annulee"] and v["reste"] > 0]
    elif statut == "payee":
        liste = [v for v in liste if not v["annulee"] and v["reste"] <= 0]
    if recherche:
        r = recherche.strip().lower()
        liste = [v for v in liste if r in v["numero"].lower() or r in v["client"]["nom"].lower()
                 or any(r in l["nom"].lower() for l in v["lignes"])]
    valides = [v for v in liste if not v["annulee"]]
    return {
        "periode": {"type": periode, "debut": d.isoformat(), "fin": f.isoformat()},
        "totaux": {"ventes": len(valides), "chiffre_affaires": _arrondi(sum(_num(v["montant_ht"]) for v in valides)),
                   "encaisse": _arrondi(sum(_num(v["montant_paye"]) for v in valides)),
                   "reste": _arrondi(sum(_num(v["reste"]) for v in valides)), "annulees": len(liste) - len(valides)},
        "ventes": liste,
    }


def statistiques(caissier, periode="jour", debut=None, fin=None):
    """Statistiques personnelles d'un caissier (aucune marge ni prix d'achat)."""
    periode, d, f, dp, fp = fenetre(periode, debut, fin)
    ventes = _ventes_periode(caissier["_id"], caissier["magasin_id"], d, f)
    precedentes = _ventes_periode(caissier["_id"], caissier["magasin_id"], dp, fp)
    kpis, avant = _kpis(ventes), _kpis(precedentes)
    argent = _mouvements(caissier["_id"], caissier["magasin_id"], _vers_utc(d), _vers_utc(f))
    annulees = LivraisonModel.get_collection().count_documents({
        "livreur_id": ObjectId(str(caissier["_id"])), "deleted_at": {"$exists": True},
        "created_at": {"$gte": _vers_utc(d), "$lte": _vers_utc(f)}})

    # Série : par heure pour une journée, sinon par jour.
    par_heure = (f - d) <= timedelta(days=1)
    serie = defaultdict(lambda: {"chiffre_affaires": 0.0, "ventes": 0})
    produits = defaultdict(lambda: {"nom": "", "quantite": 0.0, "chiffre_affaires": 0.0})
    for v in ventes:
        quand = heure_locale(v.get("created_at"))
        cle = quand.strftime("%H") if par_heure else quand.strftime("%Y-%m-%d")
        serie[cle]["chiffre_affaires"] += _num(v.get("montant_produits", v.get("montant_total")))
        serie[cle]["ventes"] += 1
        for l in v.get("produits") or []:
            p = produits[str(l.get("produit_id"))]
            p["nom"] = l.get("nom") or p["nom"] or "Produit"
            p["quantite"] += _num(l.get("quantite"))
            p["chiffre_affaires"] += _num(l.get("quantite")) * _num(l.get("prix"))
    if par_heure:
        heures = [int(k) for k in serie] or [d.hour]
        points = [{"cle": f"{h:02d}", "libelle": f"{h:02d} h", **serie[f"{h:02d}"]}
                  for h in range(min(heures + [8]), max(heures + [18]) + 1)]
    else:
        points, jour = [], d
        while jour <= f:
            cle = jour.strftime("%Y-%m-%d")
            points.append({"cle": cle, "libelle": jour.strftime("%d/%m"), **serie[cle]})
            jour += timedelta(days=1)
    for p in points:
        p["chiffre_affaires"] = _arrondi(p["chiffre_affaires"])

    # Crédits en cours sur SES ventes (toutes périodes) : clients à relancer.
    dettes = defaultdict(float)
    for v in LivraisonModel.get_collection().find({"livreur_id": ObjectId(str(caissier["_id"])),
                                                   "deleted_at": {"$exists": False},
                                                   "statut_paiement": {"$ne": "payé"}}):
        reste = _num(v.get("montant_total")) - _num(v.get("montant_paye"))
        if reste > 0.009:
            dettes[str(v.get("client_id"))] += reste
    noms_clients = {str(c["_id"]): c.get("nom") or "Client" for c in ClientModel.get_collection().find(
        {"_id": {"$in": [ObjectId(c) for c in dettes if ObjectId.is_valid(c)]}})} if dettes else {}

    return {
        "periode": {"type": periode, "debut": d.isoformat(), "fin": f.isoformat()},
        "kpis": {
            **kpis,
            "encaisse": argent["total_entrees"],
            "rendu": argent["total_sorties"],
            "annulations": annulees,
            "variations": {k: _variation(kpis[k], avant[k]) for k in ("chiffre_affaires", "ventes", "panier_moyen")},
        },
        "modes": argent["modes"],
        "serie": {"granularite": "heure" if par_heure else "jour", "points": points},
        "top_produits": sorted(({"produit_id": k, **{**p, "quantite": _arrondi(p["quantite"]),
                                                       "chiffre_affaires": _arrondi(p["chiffre_affaires"])}}
                                for k, p in produits.items()), key=lambda p: p["chiffre_affaires"], reverse=True)[:8],
        "credits": {
            "total": _arrondi(sum(dettes.values())),
            "clients": sorted(({"client_id": c, "nom": noms_clients.get(c, "Client"), "reste": _arrondi(r)}
                               for c, r in dettes.items()), key=lambda x: x["reste"], reverse=True)[:8],
        },
    }


# --------------------------------------------------------------------------- vue admin / manager
def caissiers_du_magasin(magasin_id):
    """Caissiers du magasin avec l'état de leur caisse et leurs chiffres du jour."""
    _, d, f, _, _ = fenetre("jour")
    resultat = []
    for u in UserModel.get_collection().find({"magasin_id": ObjectId(str(magasin_id)), "role": "caissier"}).sort("first_name", 1):
        session = CaisseSessionModel.session_ouverte(u["_id"])
        jour = _kpis(_ventes_periode(u["_id"], magasin_id, d, f))
        resultat.append({
            "id": str(u["_id"]), "nom": _nom(u), "email": u.get("email"), "actif": bool(u.get("is_active")),
            "session": serialiser_session(session) if session else None,
            "aujourdhui": jour,
        })
    return resultat
