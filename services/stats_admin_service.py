"""Tableau de bord de l'admin et du manager d'un magasin.

Toutes les données en un seul appel, pour une période (7, 30, 90 ou 365
jours) comparée à la période précédente de même durée.

Les chiffres financiers (chiffre d'affaires, marge, résultat, encaissements)
viennent du module comptable (services.compta_service) : le tableau de bord
affiche donc exactement les mêmes montants que la page Finance.
- chiffre d'affaires = ventes HT nettes des retours, sans TVA ni dette
  reportée d'une ancienne vente ;
- les ventes annulées (mises à la corbeille) ne comptent pas, comme dans la page Finance ;
- les dates sont regroupées à l'heure locale du magasin.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from bson import ObjectId
from dateutil.relativedelta import relativedelta

from models.livraison_model import LivraisonModel
from models.magasin_model import MagasinModel
from services import compta_service
from services.rapport_service import _ventes_du_magasin, heure_locale
from extensions import db

PERIODES = (7, 30, 90, 365)
STATUTS_PAIEMENT = ("payé", "partiellement payé", "non payé")
DELAI_CREANCE_EN_RETARD = 30  # jours : une vente impayée plus ancienne est « en retard »
DELAI_PEREMPTION = 30  # jours : produits qui périment bientôt


def _num(valeur):
    try:
        return float(valeur or 0)
    except (TypeError, ValueError):
        return 0.0


def _arrondi(valeur, decimales=2):
    return round(float(valeur or 0), decimales)


def _variation(actuel, precedent):
    """Évolution en % ; None quand la période précédente est vide (pas de base)."""
    if not precedent:
        return None
    return round((actuel - precedent) / abs(precedent) * 100, 1)


def _iso(valeur):
    local = heure_locale(valeur) if isinstance(valeur, datetime) else None
    return local.isoformat() if local else None


def _date_vente(vente):
    return heure_locale(vente.get("date_livraison") or vente.get("created_at"))


def _granularite(jours):
    if jours <= 31:
        return "jour"
    if jours <= 92:
        return "semaine"
    return "mois"


def _debut_periode(jour, granularite):
    if granularite == "jour":
        return jour
    if granularite == "semaine":
        return jour - timedelta(days=jour.weekday())
    return jour.replace(day=1)


def _suivante(debut, granularite):
    if granularite == "jour":
        return debut + timedelta(days=1)
    if granularite == "semaine":
        return debut + timedelta(weeks=1)
    return debut + relativedelta(months=1)


def _noms_utilisateurs(ids):
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}
    users = db.tables.users.find({"_id": {"$in": [ObjectId(i) for i in ids if ObjectId.is_valid(i)]}})
    return {str(u["_id"]): {"nom": f"{u.get('first_name', '')} {u.get('name', '')}".strip() or u.get("email") or "—",
                            "role": "gerant" if u.get("role") == "livreur" else u.get("role")} for u in users}


def _clients(ids):
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}
    clients = db.tables.clients.find({"_id": {"$in": [ObjectId(i) for i in ids if ObjectId.is_valid(i)]}})
    return {str(c["_id"]): c for c in clients}


def get_vue_ensemble_magasin(magasin_id, jours=30):
    jours = int(jours)
    if jours not in PERIODES:
        raise ValueError("Période invalide : 7, 30, 90 ou 365 jours.")

    maintenant = datetime.now()
    aujourd_hui = maintenant.date()
    debut = datetime.combine(aujourd_hui - timedelta(days=jours - 1), time.min)
    p_debut = debut - timedelta(days=jours)
    p_fin = debut - timedelta(microseconds=1)

    magasin = MagasinModel.get_magasin_by_id(str(magasin_id)) or {}
    devise = str(magasin.get("devise") or "XAF").upper()

    # --- Finances : même calcul que la page Finance -----------------------------
    ctx = compta_service._Contexte(magasin_id, p_debut, maintenant)
    r = compta_service._resultat(ctx, debut, maintenant)
    p = compta_service._resultat(ctx, p_debut, p_fin)

    def charges(res):
        return res["pertes"] + res["externes"] + res["impots"] + res["personnel"] + res["dotations"] + res["financieres"]

    def encaisse(ops):
        net = 0.0
        for o in ops:
            if o.get("mode") == "credit_client":
                continue
            net += _num(o["montant"]) if o.get("sens") == "entree" else -_num(o["montant"])
        return net

    ops = compta_service.encaissements(magasin_id, debut, maintenant)
    ops_prec = compta_service.encaissements(magasin_id, p_debut, p_fin)

    # --- Ventes de la période (détail) -------------------------------------------
    ventes = _ventes_du_magasin(magasin_id, p_debut, maintenant)
    courantes = [v for v in ventes if _date_vente(v) and _date_vente(v) >= debut]
    precedentes = [v for v in ventes if _date_vente(v) and _date_vente(v) < debut]

    def articles(liste):
        return int(sum(_num(l.get("quantite")) for v in liste for l in v.get("produits") or []))

    statuts = Counter()
    produits = defaultdict(lambda: {"nom": "", "quantite": 0.0, "montant": 0.0})
    clients_ca = defaultdict(lambda: {"ca": 0.0, "ventes": 0})
    vendeurs = defaultdict(lambda: {"ventes": 0, "ca": 0.0, "articles": 0})
    for v in courantes:
        statut = v.get("statut_paiement")
        statuts[statut if statut in STATUTS_PAIEMENT else "non payé"] += 1
        ht = _num(v.get("montant_produits")) if v.get("montant_produits") is not None else sum(_num(l.get("quantite")) * _num(l.get("prix")) for l in v.get("produits") or [])
        for l in v.get("produits") or []:
            pid = str(l.get("produit_id"))
            produits[pid]["nom"] = l.get("nom") or produits[pid]["nom"] or "Article"
            produits[pid]["quantite"] += _num(l.get("quantite"))
            produits[pid]["montant"] += _num(l.get("quantite")) * _num(l.get("prix"))
        if v.get("client_id"):
            clients_ca[str(v["client_id"])]["ca"] += ht
            clients_ca[str(v["client_id"])]["ventes"] += 1
        vendeur = str(v.get("livreur_id") or v.get("manager_id") or "")
        if vendeur:
            vendeurs[vendeur]["ventes"] += 1
            vendeurs[vendeur]["ca"] += ht
            vendeurs[vendeur]["articles"] += int(sum(_num(l.get("quantite")) for l in v.get("produits") or []))

    # --- Moyens de paiement (montants encaissés, une seule devise par magasin) ----
    methodes = Counter()
    for o in ops:
        if o.get("sens") == "entree" and o.get("mode") != "credit_client":
            methodes[o.get("mode") or "cash"] += _num(o["montant"])

    # --- Série temporelle : CA net et encaissements ---------------------------------
    granularite = _granularite(jours)
    cles, d = [], _debut_periode(debut.date(), granularite)
    while d <= aujourd_hui:
        cles.append(d)
        d = _suivante(d, granularite)
    serie = {c: {"ca": 0.0, "encaisse": 0.0, "ventes": 0} for c in cles}

    def seau(quand):
        if not quand:
            return None
        cle = _debut_periode(quand.date(), granularite)
        return cle if cle in serie else None

    for v in ctx.ventes_entre(debut, maintenant):
        if (s := seau(v["date"])) is not None:
            serie[s]["ca"] += v["ht"]
            serie[s]["ventes"] += 1
    for rt in ctx.retours_entre(debut, maintenant):
        if (s := seau(rt["date"])) is not None:
            serie[s]["ca"] -= rt["ht"]
    for o in ops:
        if o.get("mode") == "credit_client":
            continue
        quand = datetime.fromisoformat(o["date"]) if isinstance(o.get("date"), str) else o.get("date")
        if (s := seau(quand)) is not None:
            serie[s]["encaisse"] += _num(o["montant"]) if o.get("sens") == "entree" else -_num(o["montant"])

    # --- Créances clients (toutes périodes) -------------------------------------------
    toutes = list(LivraisonModel.get_collection().find({"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}}))
    dettes = defaultdict(lambda: {"reste": 0.0, "ventes": 0, "plus_ancienne": None})
    en_retard = 0.0
    for v in toutes:
        reste = max(_num(v.get("montant_total")) - _num(v.get("montant_paye")), 0)
        if reste <= 0.009 or not v.get("client_id"):
            continue
        quand = _date_vente(v)
        cumul = dettes[str(v["client_id"])]
        cumul["reste"] += reste
        cumul["ventes"] += 1
        if quand and (cumul["plus_ancienne"] is None or quand < cumul["plus_ancienne"]):
            cumul["plus_ancienne"] = quand
        if quand and (maintenant - quand).days > DELAI_CREANCE_EN_RETARD:
            en_retard += reste

    # --- Stock ------------------------------------------------------------------------
    stock = list(db.tables.produits.find({"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}}))
    ruptures, bas, perimes, bientot = [], [], 0, 0
    valeur_stock = 0.0
    for prod in stock:
        q = _num(prod.get("quantite"))
        valeur_stock += max(q, 0) * _num(prod.get("prix_achat"))
        seuil = prod.get("seuil_alerte")
        item = {"id": str(prod["_id"]), "nom": prod.get("nom") or "Article", "quantite": q,
                "seuil": seuil, "image": prod.get("image")}
        if q <= 0:
            # Un produit soldé épuisé ne se réapprovisionne pas : ce n'est pas une rupture.
            if not prod.get("est_solde"):
                ruptures.append(item)
        elif seuil is not None and q <= _num(seuil):
            bas.append(item)
        peremption = prod.get("date_peremption")
        if isinstance(peremption, str):
            try:
                peremption = datetime.fromisoformat(peremption[:10])
            except ValueError:
                peremption = None
        if isinstance(peremption, datetime) and q > 0:
            if peremption.date() < aujourd_hui:
                perimes += 1
            elif peremption.date() <= aujourd_hui + timedelta(days=DELAI_PEREMPTION):
                bientot += 1

    # --- Noms ---------------------------------------------------------------------------
    infos_clients = _clients(list(clients_ca) + list(dettes))
    infos_vendeurs = _noms_utilisateurs(vendeurs)

    def nom_client(cid):
        c = infos_clients.get(cid) or {}
        return c.get("nom") or "Client supprimé"

    def est_client_par_defaut(cid):
        return (infos_clients.get(cid) or {}).get("nom") == "Autres"

    recentes = sorted(toutes, key=lambda v: v.get("created_at") or datetime.min, reverse=True)[:6]
    infos_recentes = _clients([v.get("client_id") for v in recentes])

    nb, nb_prec = len(courantes), len(precedentes)
    ca, ca_prec = r["ca"], p["ca"]
    return {
        "periode": {"jours": jours, "debut": debut.date().isoformat(), "fin": aujourd_hui.isoformat(),
                    "granularite": granularite},
        "magasin": {"id": str(magasin_id), "denomination": magasin.get("denomination") or "", "devise": devise},
        "kpis": {
            "chiffre_affaires": {"valeur": _arrondi(ca), "precedent": _arrondi(ca_prec), "variation": _variation(ca, ca_prec)},
            "encaisse": {"valeur": _arrondi(encaisse(ops)), "precedent": _arrondi(encaisse(ops_prec)),
                         "variation": _variation(encaisse(ops), encaisse(ops_prec))},
            "ventes": {"valeur": nb, "precedent": nb_prec, "variation": _variation(nb, nb_prec),
                       "articles": articles(courantes), "panier_moyen": _arrondi(ca / nb) if nb else 0},
            "marge": {"valeur": _arrondi(r["marge"]), "precedent": _arrondi(p["marge"]), "variation": _variation(r["marge"], p["marge"]),
                      "taux": _arrondi(r["marge"] / ca * 100, 1) if ca else None},
            "resultat": {"valeur": _arrondi(r["net"]), "precedent": _arrondi(p["net"]), "variation": _variation(r["net"], p["net"]),
                         "charges": _arrondi(charges(r))},
            "creances": {"valeur": _arrondi(sum(x["reste"] for x in dettes.values())), "clients": len(dettes),
                         "en_retard": _arrondi(en_retard)},
            "retours": {"montant": _arrondi(r["retours"]), "nombre": len(ctx.retours_entre(debut, maintenant))},
            "tva_collectee": _arrondi(r["tva_collectee"]),
        },
        "serie": [{"debut": c.isoformat(), "ca": _arrondi(v["ca"]), "encaisse": _arrondi(v["encaisse"]), "ventes": v["ventes"]}
                  for c, v in serie.items()],
        "paiements": {
            "statuts": [{"statut": s, "nombre": statuts.get(s, 0)} for s in STATUTS_PAIEMENT],
            "methodes": [{"methode": m, "montant": _arrondi(methodes.get(m, 0))}
                         for m in ["cash", "om", "momo", "bancaire"] + sorted(set(methodes) - {"cash", "om", "momo", "bancaire"})],
        },
        "top_produits": [{"id": pid, "nom": x["nom"], "quantite": _arrondi(x["quantite"], 3), "montant": _arrondi(x["montant"])}
                         for pid, x in sorted(produits.items(), key=lambda i: i[1]["montant"], reverse=True)[:6]],
        "top_clients": [{"id": cid, "nom": nom_client(cid), "ca": _arrondi(x["ca"]), "ventes": x["ventes"],
                         "par_defaut": est_client_par_defaut(cid)}
                        for cid, x in sorted(clients_ca.items(), key=lambda i: i[1]["ca"], reverse=True)[:6]],
        "debiteurs": [{"id": cid, "nom": nom_client(cid), "telephone": (infos_clients.get(cid) or {}).get("telephone"),
                       "reste": _arrondi(x["reste"]), "ventes": x["ventes"],
                       "jours": (maintenant - x["plus_ancienne"]).days if x["plus_ancienne"] else None}
                      for cid, x in sorted(dettes.items(), key=lambda i: i[1]["reste"], reverse=True)[:6]],
        "vendeurs": [{"id": uid, "nom": (infos_vendeurs.get(uid) or {}).get("nom", "Utilisateur supprimé"),
                      "role": (infos_vendeurs.get(uid) or {}).get("role"), "ventes": x["ventes"],
                      "ca": _arrondi(x["ca"]), "articles": x["articles"]}
                     for uid, x in sorted(vendeurs.items(), key=lambda i: i[1]["ca"], reverse=True)],
        "stock": {
            "produits": len(stock),
            "valeur": _arrondi(valeur_stock),
            "ruptures": len(ruptures),
            "stock_bas": len(bas),
            "perimes": perimes,
            "bientot_perimes": bientot,
            "a_reapprovisionner": sorted(ruptures + bas, key=lambda x: x["quantite"])[:8],
        },
        "ventes_recentes": [{
            "id": str(v["_id"]),
            "client": (infos_recentes.get(str(v.get("client_id"))) or {}).get("nom") or "—",
            "date": _iso(v.get("date_livraison") or v.get("created_at")),
            "montant": _arrondi(_num(v.get("montant_total"))),
            "reste": _arrondi(max(_num(v.get("montant_total")) - _num(v.get("montant_paye")), 0)),
            "statut_paiement": v.get("statut_paiement"),
            "statut_livraison": v.get("statut_livraison"),
        } for v in recentes],
    }
