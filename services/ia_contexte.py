"""Socle commun de l'assistant IA : appel au fournisseur et données du magasin.

Deux responsabilités :

1. ``appeler_ia`` : un seul point d'appel au modèle (Groq), avec délai maximal,
   modèle configurable (GROQ_MODEL) et erreurs claires :
   - ``IANonConfiguree`` : pas de clé GROQ_API_KEY → l'IA est indisponible ;
   - ``IAErreur`` : le fournisseur n'a pas répondu ou a refusé la demande.
   Le détail technique est écrit dans le journal du serveur, jamais renvoyé
   à l'utilisateur.

2. ``faits_magasin`` / ``faits_gerant`` : les chiffres envoyés à l'IA. Ils
   viennent des mêmes calculs que le tableau de bord (même chiffre d'affaires,
   même marge, mêmes ruptures), sont limités en taille (quelques dizaines de
   lignes au lieu du catalogue entier) et mis en cache quelques minutes.
"""

import logging
import os
import time as horloge
from datetime import datetime, timedelta

from bson import ObjectId

from extensions import db
from services.rapport_service import heure_locale

journal = logging.getLogger(__name__)

# Modèles essayés dans l'ordre (le premier est celui de GROQ_MODEL s'il est défini).
# Groq retire régulièrement des modèles (llama-3.3-70b-versatile n'existe plus) :
# si un modèle est introuvable, le suivant est utilisé et mémorisé.
MODELE_PAR_DEFAUT = "openai/gpt-oss-120b"
MODELES_SECOURS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b")
_modele_actif = None
DELAI_REPONSE = 45  # secondes
DUREE_CACHE_FAITS = 5 * 60  # secondes
_cache_faits = {}


class IANonConfiguree(Exception):
    """La clé GROQ_API_KEY n'est pas définie."""

    message = ("L'assistant IA n'est pas configuré sur ce serveur. "
               "L'administrateur technique doit ajouter la clé GROQ_API_KEY dans le fichier backend/.env, puis redémarrer le serveur.")


class IAErreur(Exception):
    """Le fournisseur d'IA n'a pas pu répondre."""

    message = "L'assistant IA n'a pas pu répondre pour le moment. Réessayez dans quelques instants."


def _cle():
    cle = (os.getenv("GROQ_API_KEY") or "").strip()
    return None if not cle or cle == "your_groq_api_key_here" else cle


def modele():
    return _modele_actif or os.getenv("GROQ_MODEL") or MODELE_PAR_DEFAUT


def _modeles_a_essayer():
    vus, ordre = set(), []
    for m in (modele(), *MODELES_SECOURS):
        if m and m not in vus:
            vus.add(m)
            ordre.append(m)
    return ordre


def _options(nom_modele, max_tokens):
    """Les modèles « gpt-oss » raisonnent avant de répondre : effort réduit (plus rapide)
    et marge de jetons pour ce raisonnement, sinon la réponse serait coupée."""
    if nom_modele.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "max_tokens": max_tokens + 600}
    return {"max_tokens": max_tokens}


def ia_configuree():
    return _cle() is not None


def client_ia():
    """Client Groq prêt à l'emploi, ou None si la clé est absente."""
    cle = _cle()
    if not cle:
        return None
    from groq import Groq
    return Groq(api_key=cle, timeout=DELAI_REPONSE, max_retries=1)


def appeler_ia(messages, temperature=0.4, max_tokens=1200):
    """Envoie la conversation au modèle et renvoie le texte de la réponse."""
    global _modele_actif
    client = client_ia()
    if client is None:
        raise IANonConfiguree()
    derniere_erreur = None
    for nom_modele in _modeles_a_essayer():
        try:
            reponse = client.chat.completions.create(
                model=nom_modele, messages=messages, temperature=temperature, **_options(nom_modele, max_tokens),
            )
        except Exception as exc:  # erreurs réseau, quota, clé refusée, demande trop longue…
            derniere_erreur = exc
            if getattr(exc, "status_code", None) == 404 or "model_not_found" in str(exc):
                journal.warning("Modèle IA « %s » indisponible, essai du suivant.", nom_modele)
                continue
            journal.exception("Appel à l'IA impossible : %s", exc)
            raise IAErreur() from exc
        texte = (reponse.choices[0].message.content or "").strip()
        if not texte:
            journal.error("Réponse vide du modèle « %s » (fin : %s).", nom_modele, reponse.choices[0].finish_reason)
            raise IAErreur()
        if nom_modele != _modele_actif:
            _modele_actif = nom_modele
            journal.info("Modèle IA utilisé : %s", nom_modele)
        return texte
    journal.error("Aucun modèle IA disponible : %s", derniere_erreur)
    raise IAErreur() from derniere_erreur


# --------------------------------------------------------------------------- données du magasin
def _num(valeur):
    try:
        return float(valeur or 0)
    except (TypeError, ValueError):
        return 0.0


def _cache(cle, calcul):
    maintenant = horloge.monotonic()
    entree = _cache_faits.get(cle)
    if entree and maintenant - entree[0] < DUREE_CACHE_FAITS:
        return entree[1]
    valeur = calcul()
    _cache_faits[cle] = (maintenant, valeur)
    return valeur


def vider_cache():
    _cache_faits.clear()


def faits_magasin(magasin_id, jours=30):
    """Chiffres clés d'un magasin pour l'admin ou le manager (30 derniers jours)."""
    return _cache(("magasin", str(magasin_id), jours), lambda: _calculer_faits_magasin(str(magasin_id), jours))


def _calculer_faits_magasin(magasin_id, jours):
    from services.stats_admin_service import get_vue_ensemble_magasin
    from services.tableau_bord_service import onglet_stock, onglet_ventes

    vue = get_vue_ensemble_magasin(magasin_id, jours)
    stock = onglet_stock(magasin_id, jours)
    ventes = onglet_ventes(magasin_id, jours)
    k = vue["kpis"]

    # Prix de vente du catalogue inférieur au prix d'achat (le prix de vente est le champ « prix »).
    catalogue_a_perte = []
    for p in db.tables.produits.find({"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}}):
        prix, achat = _num(p.get("prix")), _num(p.get("prix_achat"))
        if prix > 0 and achat > 0 and prix < achat:
            catalogue_a_perte.append({"nom": p.get("nom") or "Article", "prix_vente": prix, "prix_achat": achat})
    catalogue_a_perte.sort(key=lambda x: x["prix_achat"] - x["prix_vente"], reverse=True)

    produits_vendus = ventes["produits"]
    return {
        "magasin": vue["magasin"]["denomination"],
        "devise": vue["magasin"]["devise"],
        "periode": f"{jours} derniers jours (du {vue['periode']['debut']} au {vue['periode']['fin']})",
        "ventes": {
            "chiffre_affaires_ht": k["chiffre_affaires"]["valeur"],
            "evolution_pct": k["chiffre_affaires"]["variation"],
            "nombre_ventes": k["ventes"]["valeur"],
            "panier_moyen": k["ventes"]["panier_moyen"],
            "encaisse": k["encaisse"]["valeur"],
            "marge_brute": k["marge"]["valeur"],
            "taux_marge_pct": k["marge"]["taux"],
            "resultat_net": k["resultat"]["valeur"],
            "charges": k["resultat"]["charges"],
            "retours": k["retours"]["montant"],
            "taux_retour_pct": ventes["kpis"]["taux_retour"],
            "meilleur_creneau": ventes["kpis"]["meilleur_creneau"],
        },
        "creances": {
            "total": k["creances"]["valeur"],
            "en_retard_plus_30j": k["creances"]["en_retard"],
            "principaux_debiteurs": [{"nom": d["nom"], "reste": d["reste"], "depuis_jours": d["jours"]} for d in vue["debiteurs"][:5]],
        },
        "stock": {
            "valeur": stock["kpis"]["valeur_totale"],
            "produits": stock["kpis"]["produits"],
            "jours_de_stock": stock["kpis"]["jours_de_stock"],
            "ruptures_en_magasin": stock["kpis"]["ruptures"],
            "sous_seuil_alerte": stock["kpis"]["sous_seuil"],
            "produits_avec_seuil": stock["kpis"]["avec_seuil"],
            "produits_qui_dorment": stock["kpis"]["dormants"],
            "valeur_qui_dort": stock["kpis"]["valeur_dormante"],
            "vont_manquer": [{"nom": p["nom"], "jours_restants": p["jours"], "stock": p["stock"]} for p in stock["couverture"][:6]],
            "a_reapprovisionner": [{"nom": p["nom"], "quantite": p["quantite"]} for p in vue["stock"]["a_reapprovisionner"][:6]],
            "dorment": [{"nom": p["nom"], "valeur": p["valeur"], "jours_sans_vente": p["jours"]} for p in stock["dormants"][:5]],
            "categories_principales": [{"nom": c["nom"], "valeur": c["valeur"]} for c in stock["categories"][:5]],
        },
        "produits": {
            "meilleures_ventes": [{"nom": p["nom"], "quantite": p["quantite"], "ca": p["ca"], "taux_marge_pct": p["taux_marge"]}
                                  for p in produits_vendus[:6]],
            "vendus_sans_marge": [{"nom": p["nom"], "ca": p["ca"], "taux_marge_pct": p["taux_marge"]}
                                  for p in produits_vendus if p["taux_marge"] is not None and p["taux_marge"] <= 0][:6],
            "prix_catalogue_sous_achat": catalogue_a_perte[:6],
            "plus_retournes": [{"nom": p["nom"], "quantite": p["quantite"]} for p in ventes["retours_produits"][:4]],
        },
        "equipe": [{"nom": v["nom"], "role": v["role"], "ventes": v["ventes"], "ca": v["ca"]} for v in vue["vendeurs"][:6]],
    }


def faits_gerant(user_id, magasin_id):
    """Données d'un gérant : ses ventes, son stock en main, ses clients qui doivent de l'argent."""
    return _cache(("gerant", str(user_id)), lambda: _calculer_faits_gerant(str(user_id), str(magasin_id)))


def _calculer_faits_gerant(user_id, magasin_id):
    depuis = datetime.utcnow() - timedelta(days=30)
    oid = ObjectId(user_id)
    ventes = [v for v in db.tables.livraisons.find({"livreur_id": oid}) if v.get("deleted_at") is None]
    recentes = [v for v in ventes if v.get("created_at") and v["created_at"] >= depuis]
    prix = {str(p["_id"]): (p.get("nom") or "Article", _num(p.get("prix")))
            for p in db.tables.produits.find({"magasin_id": ObjectId(magasin_id)})}
    stock = []
    for s in db.tables.stocks_livreur.find({"livreur_id": oid}):
        q = _num(s.get("quantite"))
        if q > 0:
            nom, pu = prix.get(str(s.get("produit_id")), ("Article", 0))
            stock.append({"nom": nom, "quantite": q, "valeur_vente": q * pu})
    dettes = {}
    for v in ventes:
        reste = max(_num(v.get("montant_total")) - _num(v.get("montant_paye")), 0)
        if reste > 0.009 and v.get("client_id"):
            dettes[str(v["client_id"])] = dettes.get(str(v["client_id"]), 0) + reste
    clients = {str(c["_id"]): c.get("nom") or "Client"
               for c in db.tables.clients.find({"_id": {"$in": [ObjectId(c) for c in dettes]}})} if dettes else {}
    magasin = db.tables.magasin.find_one({"_id": ObjectId(magasin_id)}) or {}
    return {
        "magasin": magasin.get("denomination") or "",
        "devise": str(magasin.get("devise") or "XAF").upper(),
        "periode": "30 derniers jours",
        "mes_ventes": {
            "nombre": len(recentes),
            "chiffre_affaires_ht": round(sum(_num(v.get("montant_produits")) for v in recentes), 2),
            "encaisse": round(sum(_num(v.get("montant_paye")) for v in recentes), 2),
        },
        "mon_stock": sorted(stock, key=lambda x: x["valeur_vente"], reverse=True)[:10],
        "mes_clients_debiteurs": sorted(({"nom": clients.get(c, "Client"), "reste": round(r, 2)} for c, r in dettes.items()),
                                        key=lambda x: x["reste"], reverse=True)[:8],
    }


def faits_caissier(user):
    """Données d'un caissier : SES ventes et encaissements (aujourd'hui et 30 jours), sa caisse, ses crédits."""
    return _cache(("caissier", str(user["_id"])), lambda: _calculer_faits_caissier(user))


def _calculer_faits_caissier(user):
    from models.caisse_session_model import CaisseSessionModel
    from services import caisse_service

    magasin = db.tables.magasin.find_one({"_id": ObjectId(str(user["magasin_id"]))}) or {}
    jour = caisse_service.statistiques(user, "jour")
    mois = caisse_service.statistiques(user, "personnalisee",
                                       (datetime.now() - timedelta(days=29)).isoformat(), datetime.now().isoformat())
    session = CaisseSessionModel.session_ouverte(user["_id"])
    resume = caisse_service.resume_session(session) if session else None
    return {
        "magasin": magasin.get("denomination") or "",
        "devise": str(magasin.get("devise") or "XAF").upper(),
        "periode": "aujourd'hui et 30 derniers jours",
        "caissier": {
            "aujourdhui": {**jour["kpis"], "modes": jour["modes"]},
            "trente_jours": {**mois["kpis"], "modes": mois["modes"], "top_produits": mois["top_produits"][:6]},
            "caisse": {"ouverte": bool(session), "fond_initial": resume["fond_initial"] if resume else None,
                       "especes_attendues": resume["especes_attendues"] if resume else None},
            "credits": mois["credits"],
        },
    }


def faits_utilisateur(user):
    """Faits adaptés au rôle : magasin entier pour admin/manager, données personnelles sinon."""
    magasin_id = str(user["magasin_id"])
    if user.get("role") in ("admin", "manager"):
        return faits_magasin(magasin_id)
    if user.get("role") == "caissier":
        return faits_caissier(user)
    return faits_gerant(str(user["_id"]), magasin_id)


def _n(valeur, decimales=1):
    """Nombre à la française : 1 737 ; 7,5 ; 0 (pas de « .0 »)."""
    if valeur is None:
        return "—"
    v = round(float(valeur), decimales)
    texte = f"{v:,.{decimales}f}".replace(",", " ").replace(".", ",")
    return texte.rstrip("0").rstrip(",") if "," in texte else texte


def _montant(valeur, devise):
    if valeur is None:
        return "—"
    texte = f"{valeur:,.0f}".replace(",", " ") if float(valeur).is_integer() or devise in ("XAF", "XOF") else f"{valeur:,.2f}".replace(",", " ")
    return f"{texte} {'FCFA' if devise in ('XAF', 'XOF') else devise}"


def faits_en_texte(faits):
    """Version lisible et compacte des faits, à placer dans le message envoyé à l'IA."""
    d = faits.get("devise", "XAF")
    m = lambda v: _montant(v, d)  # noqa: E731
    lignes = [f"Magasin : {faits.get('magasin')} — période : {faits.get('periode')} — devise : {d}"]

    def liste(titre, elements, format_ligne):
        if elements:
            lignes.append(f"{titre} :")
            lignes.extend(f"  - {format_ligne(e)}" for e in elements)
        else:
            lignes.append(f"{titre} : aucun")

    if "ventes" in faits:
        v, c, s, p = faits["ventes"], faits["creances"], faits["stock"], faits["produits"]
        evo = f" ({'+' if v['evolution_pct'] > 0 else ''}{_n(v['evolution_pct'])} % vs période précédente)" if v["evolution_pct"] is not None else ""
        lignes += [
            f"Chiffre d'affaires HT : {m(v['chiffre_affaires_ht'])}{evo} sur {v['nombre_ventes']} vente(s), panier moyen {m(v['panier_moyen'])}",
            f"Encaissé : {m(v['encaisse'])}",
            f"Marge brute : {m(v['marge_brute'])} (taux {_n(v['taux_marge_pct'])} %)",
            f"Charges : {m(v['charges'])} — résultat net : {m(v['resultat_net'])}",
            f"Retours clients : {m(v['retours'])} ({_n(v['taux_retour_pct'])} % des ventes)",
            f"Créances clients : {m(c['total'])}, dont {m(c['en_retard_plus_30j'])} impayées depuis plus de 30 jours",
            f"Stock : {m(s['valeur'])} au prix d'achat pour {s['produits']} articles ; "
            f"{_n(s['jours_de_stock'], 0)} jours de ventes en stock",
            f"Ruptures en magasin : {s['ruptures_en_magasin']} ; sous seuil d'alerte : {s['sous_seuil_alerte']} "
            f"(seuil défini sur {s['produits_avec_seuil']} article(s)) ; stock qui dort (60 j sans vente) : "
            f"{s['produits_qui_dorment']} produit(s), {m(s['valeur_qui_dort'])}",
        ]
        if v.get("meilleur_creneau"):
            jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
            mc = v["meilleur_creneau"]
            lignes.append(f"Meilleur moment de vente : {jours[mc['jour']]} vers {mc['heure']} h")
        liste("Principaux clients débiteurs", c["principaux_debiteurs"],
              lambda e: f"{e['nom']} : {m(e['reste'])} (depuis {e['depuis_jours']} j)")
        liste("Articles qui vont manquer (au rythme des ventes)", s["vont_manquer"],
              lambda e: f"{e['nom']} : environ {_n(e['jours_restants'])} jours de stock ({_n(e['stock'])} en stock)")
        liste("Articles en rupture ou sous seuil", s["a_reapprovisionner"], lambda e: f"{e['nom']} : {_n(e['quantite'])} en stock")
        liste("Articles qui dorment", s["dorment"], lambda e: f"{e['nom']} : {m(e['valeur'])} immobilisés, {e['jours_sans_vente']} j sans vente")
        liste("Catégories qui immobilisent le plus de stock", s["categories_principales"], lambda e: f"{e['nom']} : {m(e['valeur'])}")
        liste("Meilleures ventes", p["meilleures_ventes"],
              lambda e: f"{e['nom']} : {_n(e['quantite'])} unité(s), {m(e['ca'])}, marge {_n(e['taux_marge_pct'])} %")
        liste("Articles vendus sans marge (prix de vente ≤ coût d'achat)", p["vendus_sans_marge"],
              lambda e: f"{e['nom']} : {m(e['ca'])} vendus, marge {_n(e['taux_marge_pct'])} %")
        liste("Prix de vente du catalogue inférieurs au prix d'achat", p["prix_catalogue_sous_achat"],
              lambda e: f"{e['nom']} : vendu {m(e['prix_vente'])}, acheté {m(e['prix_achat'])}")
        liste("Articles les plus retournés", p["plus_retournes"], lambda e: f"{e['nom']} : {_n(e['quantite'])} unité(s)")
        liste("Ventes par membre de l'équipe", faits["equipe"], lambda e: f"{e['nom']} ({e['role']}) : {e['ventes']} vente(s), {m(e['ca'])}")
    elif "caissier" in faits:
        c = faits["caissier"]

        def bloc(titre, k):
            modes = ", ".join(f"{x['libelle']} {m(x['net'])}" for x in k["modes"]) or "aucun encaissement"
            lignes.append(f"{titre} : {k['ventes']} vente(s), chiffre d'affaires {m(k['chiffre_affaires'])}, "
                          f"{k['articles']} article(s), panier moyen {m(k['panier_moyen'])}, encaissé {m(k['encaisse'])} "
                          f"({modes}), {k['annulations']} annulation(s)")

        bloc("Mes ventes aujourd'hui", c["aujourdhui"])
        bloc("Mes ventes des 30 derniers jours", c["trente_jours"])
        caisse = c["caisse"]
        lignes.append("Ma caisse : " + (f"ouverte, fond de caisse {m(caisse['fond_initial'])}, espèces attendues "
                                        f"{m(caisse['especes_attendues'])}" if caisse["ouverte"] else "fermée"))
        liste("Mes articles les plus vendus (30 jours)", c["trente_jours"]["top_produits"],
              lambda e: f"{e['nom']} : {_n(e['quantite'])} unité(s), {m(e['chiffre_affaires'])}")
        lignes.append(f"Crédits en cours sur mes ventes : {m(c['credits']['total'])}")
        liste("Mes clients qui doivent encore de l'argent", c["credits"]["clients"], lambda e: f"{e['nom']} : {m(e['reste'])}")
    else:
        v = faits["mes_ventes"]
        lignes.append(f"Mes ventes : {v['nombre']} vente(s), {m(v['chiffre_affaires_ht'])} HT, {m(v['encaisse'])} encaissés")
        liste("Mon stock en main", faits["mon_stock"], lambda e: f"{e['nom']} : {_n(e['quantite'])} unité(s)")
        liste("Mes clients qui doivent de l'argent", faits["mes_clients_debiteurs"], lambda e: f"{e['nom']} : {m(e['reste'])}")
    return "\n".join(lignes)


def date_locale_texte():
    maintenant = heure_locale(datetime.utcnow())
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    return f"{jours[maintenant.weekday()]} {maintenant.strftime('%d/%m/%Y à %H:%M')}"
