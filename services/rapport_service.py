# services/rapport_service.py
"""Rapports de la page /admin/dashboard/reports.

Cinq rapports, tous limités au magasin de l'utilisateur :
  - ventes        : une ligne par vente (HT, TVA, TTC, réductions, payé, reste,
                    retours, remboursements, marge), avec les produits vendus ;
  - encaissements : l'argent reçu (paiements) et rendu (remboursements), par mode ;
  - mouvements    : le journal des entrées et sorties de stock ;
  - stock annuel  : par produit, stock au 1er janvier, entrées et sorties de
                    l'année par nature, stock au 31 décembre, valeurs ;
  - inventaire    : l'état actuel du stock, valorisé.

Le journal des mouvements fait foi pour le stock : chaque vente, modification,
retour, approvisionnement ou avarie y est tracé au moment où il a lieu.
"""
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from models.client_model import ClientModel
from models.livraison_model import LivraisonModel
from models.produit_model import ProduitModel
from models.user_model import UserModel

STATUTS = {"payé": "paye", "partiellement payé": "partiel", "non payé": "non_paye"}
MODES = {
    "cash": "Espèces", "especes": "Espèces", "espèces": "Espèces",
    "om": "Orange Money", "momo": "MTN MoMo",
    "bancaire": "Virement / carte", "virement": "Virement / carte", "carte": "Virement / carte",
    "credit_client": "Crédit sur le compte client",
}
# Remboursements : même clé que les paiements (« especes » = « cash »).
MODE_CANONIQUE = {"especes": "cash", "espèces": "cash", "virement": "bancaire", "carte": "bancaire"}

FAMILLES = {
    "approvisionnements": "Approvisionnements",
    "ventes": "Ventes",
    "retours": "Retours clients",
    "pertes": "Avaries et pertes",
    "livreurs": "Stock des livreurs",
    "ajustements": "Ajustements",
}


# --------------------------------------------------------------------------- outils
def _norm(texte):
    texte = unicodedata.normalize("NFKD", str(texte or ""))
    return "".join(c for c in texte if not unicodedata.combining(c)).lower().replace("_", " ").strip()


def _date(valeur):
    if isinstance(valeur, datetime):
        return valeur.replace(tzinfo=None)
    if isinstance(valeur, str) and valeur:
        try:
            return datetime.fromisoformat(valeur.replace("Z", "")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def heure_locale(valeur):
    """Horodatage enregistré en UTC (ventes, paiements, retours, mouvements de
    stock, créations) ramené à l'heure locale du magasin : une vente faite à
    0 h 30 appartient à ce jour-là, pas à la veille."""
    d = _date(valeur)
    return d.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None) if d else None


UN_JOUR = timedelta(days=1)


def _iso(valeur):
    d = _date(valeur)
    return d.isoformat() if d else None


def _num(valeur):
    try:
        return float(valeur or 0)
    except (TypeError, ValueError):
        return 0.0


def _arrondi(valeur, decimales=2):
    v = round(float(valeur or 0), decimales)
    return int(v) if v == int(v) else v


def facture(livraison_id):
    return f"FAC-{str(livraison_id)[-8:].upper()}"


def periode(debut=None, fin=None, defaut="mois"):
    """(début, fin inclusive à 23:59:59) ; par défaut le mois en cours."""
    aujourd_hui = datetime.now()
    d = _date(debut) if debut else None
    f = _date(fin) if fin else None
    if not d:
        d = aujourd_hui.replace(month=1, day=1) if defaut == "annee" else aujourd_hui.replace(day=1)
    if not f:
        f = aujourd_hui
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    f = f.replace(hour=23, minute=59, second=59, microsecond=0)
    if f < d:
        d, f = f.replace(hour=0, minute=0, second=0), d.replace(hour=23, minute=59, second=59)
    return d, f


class _Noms:
    """Noms des clients et utilisateurs, lus une seule fois."""

    def __init__(self):
        self.clients, self.utilisateurs = {}, {}

    def client(self, client_id):
        cle = str(client_id or "")
        if cle not in self.clients:
            c = ClientModel.get_client_by_id(cle) if ObjectId.is_valid(cle) else None
            self.clients[cle] = {
                "id": cle or None,
                "nom": (c or {}).get("nom") or "Client de passage",
                "telephone": (c or {}).get("telephone") or "",
            }
        return self.clients[cle]

    def utilisateur(self, user_id):
        cle = str(user_id or "")
        if cle not in self.utilisateurs:
            u = UserModel.find_by_id(cle) if ObjectId.is_valid(cle) else None
            nom = " ".join(filter(None, [(u or {}).get("first_name"), (u or {}).get("last_name") or (u or {}).get("name")]))
            self.utilisateurs[cle] = (nom or (u or {}).get("username") or (u or {}).get("email") or "").strip() or None
        return self.utilisateurs[cle]


def _ventes_du_magasin(magasin_id, debut, fin, livreur_id=None):
    query = {
        "magasin_id": ObjectId(magasin_id),
        "deleted_at": {"$exists": False},
        # Dates enregistrées en UTC : fenêtre élargie, puis filtre à l'heure locale.
        "date_livraison": {"$gte": debut - UN_JOUR, "$lte": fin + UN_JOUR},
    }
    if livreur_id:
        query["livreur_id"] = ObjectId(livreur_id)
    return [l for l in LivraisonModel.get_collection().find(query)
            if debut <= (heure_locale(l.get("date_livraison") or l.get("created_at")) or debut - UN_JOUR) <= fin]


# --------------------------------------------------------------------------- ventes
def rapport_ventes(magasin_id, debut, fin, filtres=None, livreur_id=None):
    filtres = filtres or {}
    noms = _Noms()
    ventes, options_clients, options_vendeurs, options_produits = [], {}, {}, {}

    for l in _ventes_du_magasin(magasin_id, debut, fin, livreur_id):
        client = noms.client(l.get("client_id"))
        vendeur_id = str(l.get("livreur_id") or l.get("manager_id") or "")
        vendeur = noms.utilisateur(vendeur_id) or "—"
        lignes, cout = [], 0.0
        for p in l.get("produits", []) or []:
            qte = _num(p.get("quantite"))
            prix = _num(p.get("prix"))
            cout_ligne = qte * _num(p.get("prix_achat"))
            cout += cout_ligne
            lignes.append({
                "produit_id": str(p.get("produit_id") or ""),
                "nom": p.get("nom") or "Produit",
                "quantite": _arrondi(qte, 3),
                "prix": _arrondi(prix),
                "total": _arrondi(qte * prix),
                "marge": _arrondi(qte * prix - cout_ligne),
            })
            if p.get("produit_id"):
                options_produits[str(p["produit_id"])] = p.get("nom") or "Produit"

        brut = _num(l.get("montant_brut")) or sum(x["total"] for x in lignes)
        reductions = _num(l.get("total_reductions"))
        ht = _num(l.get("montant_produits")) or max(brut - reductions, 0)
        tva = _num(l.get("montant_tva"))
        dette_ancienne = _num(l.get("dette_ancienne"))
        total = _num(l.get("montant_total"))
        paye = _num(l.get("montant_paye"))
        statut = STATUTS.get(_norm(l.get("statut_paiement")).replace("paye", "payé"), None)
        if not statut:
            statut = "paye" if paye >= total > 0 else "partiel" if paye > 0 else "non_paye"
        vente = {
            "id": str(l["_id"]),
            "facture": facture(l["_id"]),
            "date": _iso(heure_locale(l.get("date_livraison") or l.get("created_at"))),
            "client": client,
            "vendeur": {"id": vendeur_id or None, "nom": vendeur},
            "articles": _arrondi(sum(_num(x["quantite"]) for x in lignes), 3),
            "lignes": lignes,
            "montant_brut": _arrondi(brut),
            "reductions": _arrondi(reductions),
            "montant_ht": _arrondi(ht),
            "taux_tva": _arrondi(_num(l.get("taux_tva"))) if l.get("appliquer_tva") else 0,
            "montant_tva": _arrondi(tva),
            "montant_ttc": _arrondi(ht + tva),
            "dette_ancienne": _arrondi(dette_ancienne),
            "montant_total": _arrondi(total),
            "montant_paye": _arrondi(paye),
            "reste": _arrondi(max(total - paye, 0)),
            "montant_retourne": _arrondi(_num(l.get("montant_retourne"))),
            "montant_rembourse": _arrondi(_num(l.get("montant_rembourse"))),
            "cout_achat": _arrondi(cout),
            "marge": _arrondi(ht - cout),
            "statut": statut,
            "methode_paiement": l.get("methode_paiement") or "cash",
        }
        options_clients[client["id"] or ""] = client["nom"]
        options_vendeurs[vendeur_id] = vendeur

        # Filtres
        if filtres.get("client_id") and client["id"] != filtres["client_id"]:
            continue
        if filtres.get("vendeur_id") and vendeur_id != filtres["vendeur_id"]:
            continue
        if filtres.get("statut") and statut != filtres["statut"]:
            continue
        if filtres.get("produit_id") and not any(x["produit_id"] == filtres["produit_id"] for x in lignes):
            continue
        ventes.append(vente)

    ventes.sort(key=lambda v: v["date"] or "")
    somme = lambda champ: _arrondi(sum(_num(v[champ]) for v in ventes))
    totaux = {
        "ventes": len(ventes),
        **{c: somme(c) for c in (
            "articles", "montant_brut", "reductions", "montant_ht", "montant_tva", "montant_ttc",
            "montant_total", "montant_paye", "reste", "montant_retourne", "montant_rembourse",
            "cout_achat", "marge")},
    }
    totaux["panier_moyen"] = _arrondi(totaux["montant_ttc"] / len(ventes)) if ventes else 0
    totaux["taux_marge"] = _arrondi(totaux["marge"] / totaux["montant_ht"] * 100, 1) if totaux["montant_ht"] else 0

    par_statut = {s: {"nombre": 0, "montant": 0.0} for s in ("paye", "partiel", "non_paye")}
    produits, par_jour = defaultdict(lambda: {"quantite": 0.0, "montant": 0.0, "marge": 0.0}), defaultdict(float)
    for v in ventes:
        par_statut[v["statut"]]["nombre"] += 1
        par_statut[v["statut"]]["montant"] += v["montant_ttc"]
        par_jour[(v["date"] or "")[:10]] += v["montant_ttc"]
        for x in v["lignes"]:
            p = produits[x["nom"]]
            p["quantite"] += _num(x["quantite"])
            p["montant"] += _num(x["total"])
            p["marge"] += _num(x["marge"])
    top = sorted(({"nom": n, **{k: _arrondi(val) for k, val in d.items()}} for n, d in produits.items()),
                 key=lambda p: -p["montant"])[:10]

    return {
        "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()},
        "totaux": totaux,
        "par_statut": {s: {"nombre": d["nombre"], "montant": _arrondi(d["montant"])} for s, d in par_statut.items()},
        "par_jour": [{"date": j, "montant": _arrondi(m)} for j, m in sorted(par_jour.items()) if j],
        "top_produits": top,
        "ventes": ventes,
        "options": {
            "clients": sorted(({"id": k, "nom": v} for k, v in options_clients.items() if k), key=lambda o: _norm(o["nom"])),
            "vendeurs": sorted(({"id": k, "nom": v} for k, v in options_vendeurs.items() if k), key=lambda o: _norm(o["nom"])),
            "produits": sorted(({"id": k, "nom": v} for k, v in options_produits.items()), key=lambda o: _norm(o["nom"])),
        },
    }


# --------------------------------------------------------------------------- encaissements
def rapport_encaissements(magasin_id, debut, fin, filtres=None, livreur_id=None):
    """Argent reçu (paiements) et rendu (remboursements) dans la période.

    Un paiement sans date est daté du jour de la vente ; un remboursement porte
    toujours sa date. Les ventes plus anciennes sont lues aussi : un client
    peut régler ou être remboursé longtemps après la vente.
    """
    filtres = filtres or {}
    noms = _Noms()
    query = {"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}}
    if livreur_id:
        query["livreur_id"] = ObjectId(livreur_id)
    operations = []
    for l in LivraisonModel.get_collection().find(query):
        date_vente = heure_locale(l.get("date_livraison") or l.get("created_at"))
        client = noms.client(l.get("client_id"))
        base = {"vente_id": str(l["_id"]), "facture": facture(l["_id"]), "client": client["nom"]}
        paiements = l.get("paiements") or []
        if not paiements:
            # Vente sans détail des paiements : l'argent reçu à la vente est le
            # montant payé actuel plus ce qui a déjà été rendu (un retour
            # remboursé réduit le montant payé).
            # La part payée avec le compte du client n'est pas de l'argent reçu ce jour-là.
            recu = (_num(l.get("montant_paye")) - _num(l.get("montant_deduit_caisse"))
                    + sum(_num(r.get("montant")) for r in l.get("remboursements") or []))
            if recu > 0:
                paiements = [{"methode": l.get("methode_paiement") or "cash", "montant": recu}]
        for p in paiements:
            montant = _num(p.get("montant")) + _num(p.get("surplus_credite"))
            quand = heure_locale(p.get("date")) or date_vente
            if montant <= 0 or not quand or not (debut <= quand <= fin):
                continue
            mode = MODE_CANONIQUE.get(_norm(p.get("methode")), _norm(p.get("methode")) or "cash")
            operations.append({
                **base, "date": quand.isoformat(), "sens": "entree", "mode": mode,
                "mode_libelle": MODES.get(mode, mode.capitalize()), "montant": _arrondi(montant),
                "surplus": _arrondi(_num(p.get("surplus_credite"))),
                "nature": "Paiement complémentaire" if p.get("date") else "Paiement à la vente",
                "detail": f"dont {_arrondi(_num(p.get('surplus_credite')))} crédités au compte client"
                if _num(p.get("surplus_credite")) > 0 else "",
                "par": noms.utilisateur(p.get("par")) if p.get("par") else None,
            })
        for r in l.get("remboursements") or []:
            quand = heure_locale(r.get("date"))
            montant = _num(r.get("montant"))
            if montant <= 0 or not quand or not (debut <= quand <= fin):
                continue
            mode = MODE_CANONIQUE.get(_norm(r.get("mode")), _norm(r.get("mode")).replace(" ", "_") or "cash")
            detail = " · ".join(filter(None, [
                f"Réf. {r['reference']}" if r.get("reference") else "",
                r.get("telephone") or "", r.get("motif") or "",
            ]))
            operations.append({
                **base, "date": quand.isoformat(), "sens": "sortie", "mode": mode,
                "mode_libelle": r.get("mode_libelle") or MODES.get(mode, mode.capitalize()),
                "montant": _arrondi(montant),
                "nature": "Annulation de vente" if r.get("type") == "annulation" else "Remboursement",
                "detail": detail, "par": r.get("par_nom") or noms.utilisateur(r.get("par")),
            })

    # Règlements des dettes d'ouverture (dettes d'avant Lumistock).
    from services.compte_client_service import reglements_ouverture
    for r in reglements_ouverture(magasin_id, par=livreur_id):
        quand = heure_locale(r["date"])
        if r["montant"] <= 0 or not quand or not (debut <= quand <= fin):
            continue
        mode = MODE_CANONIQUE.get(_norm(r["methode"]), _norm(r["methode"]) or "cash")
        operations.append({
            "vente_id": None, "facture": "Dette d'ouverture", "client": noms.client(r["client_id"])["nom"],
            "date": quand.isoformat(), "sens": "entree", "mode": mode,
            "mode_libelle": MODES.get(mode, mode.capitalize()), "montant": _arrondi(r["montant"]), "surplus": 0,
            "nature": "Règlement de dette d'ouverture", "detail": "",
            "par": noms.utilisateur(r["par"]) if r.get("par") else None,
        })

    if filtres.get("mode"):
        operations = [o for o in operations if o["mode"] == filtres["mode"]]
    if filtres.get("sens"):
        operations = [o for o in operations if o["sens"] == filtres["sens"]]
    operations.sort(key=lambda o: o["date"])

    par_mode = {}
    for o in operations:
        m = par_mode.setdefault(o["mode"], {"mode": o["mode"], "libelle": MODES.get(o["mode"], o["mode_libelle"]),
                                            "entrees": 0.0, "sorties": 0.0, "operations": 0})
        m["entrees" if o["sens"] == "entree" else "sorties"] += o["montant"]
        m["operations"] += 1
    modes = []
    for m in par_mode.values():
        # Un crédit au compte client ne sort pas d'argent de la caisse.
        m["net"] = m["entrees"] - (0 if m["mode"] == "credit_client" else m["sorties"])
        modes.append({k: _arrondi(v) if isinstance(v, float) else v for k, v in m.items()})
    modes.sort(key=lambda m: -m["entrees"])
    entrees = sum(o["montant"] for o in operations if o["sens"] == "entree")
    sorties = sum(o["montant"] for o in operations if o["sens"] == "sortie" and o["mode"] != "credit_client")
    credits = sum(o["montant"] for o in operations if o["sens"] == "sortie" and o["mode"] == "credit_client")
    return {
        "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()},
        "totaux": {
            "entrees": _arrondi(entrees), "sorties": _arrondi(sorties),
            "credits_clients": _arrondi(credits), "net": _arrondi(entrees - sorties),
            "operations": len(operations),
        },
        "par_mode": modes,
        "operations": operations,
    }


# --------------------------------------------------------------------------- mouvements
def famille_mouvement(type_mouvement, quantite=0):
    """(famille, libellé lisible) d'un type de mouvement enregistré."""
    t = _norm(type_mouvement)
    if t == "mise en solde":
        # Unités avariées remises en vente (coût 0) : ni achat ni perte.
        return "ajustements", "Mise en solde d'unités avariées"
    if "perime" in t or "avarie" in t:
        return "pertes", "Avarie / produit périmé"
    if t == "approvisionnement livreur":
        return "livreurs", "Remise de stock au livreur"
    if "stock livreur" in t:
        return "livreurs", "Retour de stock du livreur"
    if t.startswith("approvisionnement") or t in ("entree", "approvisionnement magasin") or "approvisionnement" in t:
        if "annulation" in t:
            return "approvisionnements", "Annulation d'approvisionnement"
        if "restauration" in t:
            return "approvisionnements", "Restauration d'approvisionnement"
        if "modification" in t:
            return "approvisionnements", "Correction d'approvisionnement"
        return "approvisionnements", "Approvisionnement"
    if "retour" in t and ("magasin" in t or "client" in t):
        return "retours", "Retour client"
    if "livraison" in t or "vente" in t:
        if "annulation" in t or "suppression" in t:
            return "ventes", "Annulation de vente"
        if "restauration" in t:
            return "ventes", "Restauration de vente" if quantite < 0 else "Reprise avant modification"
        if "modification" in t:
            return "ventes", "Modification de vente"
        return "ventes", "Vente"
    if t == "modification produit":
        return "ajustements", "Correction du stock"
    if t == "sortie":
        return "ajustements", "Sortie de stock"
    return "ajustements", (str(type_mouvement or "Mouvement").replace("_", " ").capitalize())


def _produits_du_magasin(magasin_id):
    """Tous les produits, supprimés compris : leurs mouvements comptent."""
    return list(ProduitModel.get_collection().find({"magasin_id": ObjectId(magasin_id)}))


def rapport_mouvements(magasin_id, debut, fin, filtres=None):
    filtres = filtres or {}
    noms = _Noms()
    lignes = []
    for p in _produits_du_magasin(magasin_id):
        if filtres.get("produit_id") and str(p["_id"]) != filtres["produit_id"]:
            continue
        for m in p.get("mouvements_stock") or []:
            quand = heure_locale(m.get("date"))
            if not quand or not (debut <= quand <= fin):
                continue
            qte = _num(m.get("quantite"))
            famille, libelle = famille_mouvement(m.get("type"), qte)
            if filtres.get("famille") and famille != filtres["famille"]:
                continue
            if filtres.get("sens") == "entree" and qte <= 0 or filtres.get("sens") == "sortie" and qte >= 0:
                continue
            acteur = m.get("acteur") or {}
            if not isinstance(acteur, dict):
                acteur = {"admin_id": acteur}
            par = acteur.get("admin_nom") or noms.utilisateur(acteur.get("admin_id")) or acteur.get("first_name")
            detail = " · ".join(filter(None, [
                f"Client : {acteur['client_nom']}" if acteur.get("client_nom") else "",
                f"Livreur : {acteur['livreur_nom']}" if acteur.get("livreur_nom") else "",
                m.get("description") or "", m.get("type_avarie") or "",
            ]))
            lignes.append({
                "date": quand.isoformat(),
                "produit_id": str(p["_id"]),
                "produit": p.get("nom") or "Produit",
                "unite": p.get("unite") or "",
                "produit_supprime": bool(p.get("deleted_at")),
                "type": m.get("type"),
                "famille": famille,
                "libelle": libelle,
                "quantite": _arrondi(qte, 3),
                "valeur": _arrondi(qte * _num(p.get("prix_achat"))),
                "par": par,
                "detail": detail,
            })
    lignes.sort(key=lambda x: x["date"], reverse=True)

    par_famille = {f: {"famille": f, "libelle": l, "entrees": 0.0, "sorties": 0.0, "operations": 0}
                   for f, l in FAMILLES.items()}
    for x in lignes:
        f = par_famille[x["famille"]]
        f["entrees" if x["quantite"] > 0 else "sorties"] += abs(_num(x["quantite"]))
        f["operations"] += 1
    entrees = sum(_num(x["quantite"]) for x in lignes if _num(x["quantite"]) > 0)
    sorties = sum(-_num(x["quantite"]) for x in lignes if _num(x["quantite"]) < 0)
    return {
        "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()},
        "totaux": {
            "operations": len(lignes), "entrees": _arrondi(entrees, 3), "sorties": _arrondi(sorties, 3),
            "solde": _arrondi(entrees - sorties, 3),
            "produits": len({x["produit_id"] for x in lignes}),
        },
        "par_famille": [{k: _arrondi(v, 3) if isinstance(v, float) else v for k, v in f.items()}
                        for f in par_famille.values() if f["operations"]],
        "mouvements": lignes,
    }


# --------------------------------------------------------------------------- stock annuel
COLONNES_ANNUELLES = ("approvisionnements", "ventes", "retours", "pertes", "livreurs", "ajustements")


def rapport_stock_annuel(magasin_id, annee, filtres=None):
    """Mouvement de stock de l'année, produit par produit.

    Le stock au 31 décembre se déduit du stock actuel moins les mouvements
    postérieurs ; le stock au 1er janvier, du stock au 31 décembre moins les
    mouvements de l'année. Un produit créé dans l'année démarre à zéro : son
    stock de création apparaît dans sa propre colonne.
    """
    filtres = filtres or {}
    debut, fin = datetime(annee, 1, 1), datetime(annee, 12, 31, 23, 59, 59)
    maintenant = datetime.now()
    categories = {}
    try:
        from models.categorie_model import CategorieModel
        for c in CategorieModel.get_collection().find({"magasin_id": ObjectId(magasin_id)}):
            categories[str(c["_id"])] = c.get("nom") or c.get("name") or "Sans catégorie"
    except Exception:
        pass

    mois = [{"mois": i, "entrees": 0.0, "sorties": 0.0, "ventes": 0.0, "approvisionnements": 0.0} for i in range(1, 13)]
    lignes, anomalies, annees = [], [], {maintenant.year}
    for p in _produits_du_magasin(magasin_id):
        cree = heure_locale(p.get("created_at"))
        if cree:
            annees.add(cree.year)
        if filtres.get("produit_id") and str(p["_id"]) != filtres["produit_id"]:
            continue
        categorie_id = str(p.get("categorie_id") or "")
        if filtres.get("categorie_id") and categorie_id != filtres["categorie_id"]:
            continue
        if cree and cree > fin:
            continue  # produit pas encore créé cette année-là
        supprime = heure_locale(p.get("deleted_at"))
        if supprime and supprime < debut:
            continue  # supprimé avant l'année

        colonnes = {c: 0.0 for c in COLONNES_ANNUELLES}
        apres, nombre = 0.0, 0
        for m in p.get("mouvements_stock") or []:
            quand = heure_locale(m.get("date"))
            if quand:
                annees.add(quand.year)
            qte = _num(m.get("quantite"))
            if not quand or quand > fin:
                apres += qte if quand else 0
                continue
            if quand < debut:
                continue
            famille, _ = famille_mouvement(m.get("type"), qte)
            colonnes[famille] += qte
            nombre += 1
            mm = mois[quand.month - 1]
            mm["entrees" if qte > 0 else "sorties"] += abs(qte)
            if famille in ("ventes", "approvisionnements"):
                mm[famille] += -qte if famille == "ventes" else qte
        if annee > maintenant.year:
            continue
        stock_final = _num(p.get("quantite")) - apres
        variation = sum(colonnes.values())
        stock_initial = stock_final - variation
        creation = 0.0
        if cree and cree >= debut and stock_initial > 0:
            creation, stock_initial = stock_initial, 0.0
        if filtres.get("avec_mouvements") and not nombre:
            continue  # seulement les produits qui ont bougé dans l'année
        if not nombre and not creation and not stock_initial and not stock_final:
            continue
        prix_achat = _num(p.get("prix_achat"))
        ligne = {
            "produit_id": str(p["_id"]),
            "produit": p.get("nom") or "Produit",
            "categorie": categories.get(categorie_id, "Sans catégorie"),
            "unite": p.get("unite") or "",
            "supprime": bool(supprime),
            "stock_initial": _arrondi(stock_initial, 3),
            "creation": _arrondi(creation, 3),
            "approvisionnements": _arrondi(colonnes["approvisionnements"], 3),
            "retours": _arrondi(colonnes["retours"], 3),
            "ventes": _arrondi(-colonnes["ventes"], 3),
            "pertes": _arrondi(-colonnes["pertes"], 3),
            "livreurs": _arrondi(colonnes["livreurs"], 3),
            "ajustements": _arrondi(colonnes["ajustements"], 3),
            "entrees": _arrondi(creation + sum(v for v in colonnes.values() if v > 0), 3),
            "sorties": _arrondi(-sum(v for v in colonnes.values() if v < 0), 3),
            "stock_final": _arrondi(stock_final, 3),
            "prix_achat": _arrondi(prix_achat),
            "valeur_initiale": _arrondi(stock_initial * prix_achat),
            "valeur_finale": _arrondi(stock_final * prix_achat),
            "valeur_ventes_cout": _arrondi(-colonnes["ventes"] * prix_achat),
            "mouvements": nombre,
        }
        if stock_initial < 0 or stock_final < 0:
            anomalies.append({"produit": ligne["produit"],
                              "message": "Stock négatif reconstitué : des mouvements anciens n'ont pas été tracés."})
        lignes.append(ligne)

    lignes.sort(key=lambda l: (_norm(l["categorie"]), _norm(l["produit"])))
    cles = ("stock_initial", "creation", "approvisionnements", "retours", "ventes", "pertes", "livreurs",
            "ajustements", "entrees", "sorties", "stock_final", "valeur_initiale", "valeur_finale",
            "valeur_ventes_cout", "mouvements")
    totaux = {c: _arrondi(sum(_num(l[c]) for l in lignes), 3) for c in cles}
    totaux["produits"] = len(lignes)
    totaux["produits_mouvementes"] = sum(1 for l in lignes if l["mouvements"])
    # Taux de rotation : quantité vendue / stock moyen.
    stock_moyen = (totaux["stock_initial"] + totaux["creation"] + totaux["stock_final"]) / 2
    totaux["rotation"] = _arrondi(totaux["ventes"] / stock_moyen, 2) if stock_moyen > 0 else 0
    return {
        "annee": annee,
        "annees": sorted(annees, reverse=True),
        "categories": sorted(({"id": k, "nom": v} for k, v in categories.items()), key=lambda c: _norm(c["nom"])),
        "totaux": totaux,
        "mois": [{k: _arrondi(v, 3) for k, v in m.items()} for m in mois],
        "produits": lignes,
        "anomalies": anomalies,
    }


# --------------------------------------------------------------------------- inventaire
def etat_stock(produit):
    qte = _num(produit.get("quantite"))
    seuil = produit.get("seuil_alerte")
    if qte <= 0:
        return "rupture"
    if seuil not in (None, "") and qte <= _num(seuil):
        return "critique"
    initiale = _num(produit.get("quantite_initiale"))
    if seuil in (None, "") and initiale > 0 and qte / initiale < 0.2:
        return "critique"
    return "en_stock"


def rapport_inventaire(magasin_id, filtres=None):
    filtres = filtres or {}
    categories = {}
    try:
        from models.categorie_model import CategorieModel
        for c in CategorieModel.get_collection().find({"magasin_id": ObjectId(magasin_id)}):
            categories[str(c["_id"])] = c.get("nom") or c.get("name") or "Sans catégorie"
    except Exception:
        pass
    lignes = []
    for p in ProduitModel.get_all_by_magasin(magasin_id):
        categorie_id = str(p.get("categorie_id") or "")
        if filtres.get("categorie_id") and categorie_id != filtres["categorie_id"]:
            continue
        etat = etat_stock(p)
        if filtres.get("etat") and etat != filtres["etat"]:
            continue
        qte = _num(p.get("quantite"))
        achat, vente = _num(p.get("prix_achat")), _num(p.get("prix"))
        lignes.append({
            "produit_id": str(p["_id"]),
            "produit": p.get("nom") or "Produit",
            "reference": p.get("code_barre") or "",
            "categorie": categories.get(categorie_id, "Sans catégorie"),
            "unite": p.get("unite") or "",
            "quantite": _arrondi(qte, 3),
            "seuil_alerte": p.get("seuil_alerte"),
            "prix_achat": _arrondi(achat),
            "prix_vente": _arrondi(vente),
            "valeur_achat": _arrondi(qte * achat),
            "valeur_vente": _arrondi(qte * vente),
            "marge_potentielle": _arrondi(qte * (vente - achat)),
            "etat": etat,
            "date_peremption": _iso(p.get("date_peremption")),
        })
    lignes.sort(key=lambda l: (_norm(l["categorie"]), _norm(l["produit"])))
    compte = defaultdict(int)
    noms = defaultdict(int)
    for l in lignes:
        compte[l["etat"]] += 1
        noms[_norm(l["produit"])] += 1
    # Même nom plusieurs fois (import répété ?) : signalé, jamais fusionné ici.
    for l in lignes:
        l["doublon"] = noms[_norm(l["produit"])] > 1
    doublons = sorted({l["produit"] for l in lignes if l["doublon"]}, key=_norm)
    return {
        "date": datetime.now().isoformat(),
        "categories": sorted(({"id": k, "nom": v} for k, v in categories.items()), key=lambda c: _norm(c["nom"])),
        "totaux": {
            "produits": len(lignes),
            "quantite": _arrondi(sum(_num(l["quantite"]) for l in lignes), 3),
            "valeur_achat": _arrondi(sum(_num(l["valeur_achat"]) for l in lignes)),
            "valeur_vente": _arrondi(sum(_num(l["valeur_vente"]) for l in lignes)),
            "marge_potentielle": _arrondi(sum(_num(l["marge_potentielle"]) for l in lignes)),
            "en_stock": compte["en_stock"], "critique": compte["critique"], "rupture": compte["rupture"],
            "doublons": len(doublons),
        },
        "doublons": doublons,
        "produits": lignes,
    }
