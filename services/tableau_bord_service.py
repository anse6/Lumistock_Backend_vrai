"""Onglets détaillés du tableau de bord de l'admin / du manager.

Chaque onglet (stock, ventes, clients, finances, équipe) a sa fonction, appelée
seulement quand l'onglet est ouvert. La vue d'ensemble reste dans
services.stats_admin_service.

Conventions (identiques à la page Finance) :
- montants HT, ventes annulées (corbeille) exclues ;
- dates enregistrées en UTC, regroupées à l'heure locale du magasin ;
- le stock est valorisé au prix d'achat actuel des produits.

Reconstitution du stock passé : chaque produit garde ses mouvements datés.
Le stock à une date T = stock actuel − mouvements postérieurs à T. Deux
niveaux sont suivis :
- en magasin (quantité de la fiche produit) ;
- chez les gérants (stock remis aux gérants, pas encore vendu).
Une remise au gérant déplace le stock sans changer le total ; une vente du
gérant diminue son stock sans toucher celui du magasin.
"""

from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

from bson import ObjectId
from dateutil.relativedelta import relativedelta

from extensions import db
from models.magasin_model import MagasinModel
from services import compta_service
from services.rapport_service import _norm, _ventes_du_magasin, famille_mouvement, heure_locale
from services.stats_admin_service import (
    PERIODES, _debut_periode, _granularite, _suivante, _variation,
)

SEUIL_DORMANT = 60  # jours sans vente : produit « qui dort »
SEUIL_INACTIF = 60  # jours sans achat : client « à relancer »
TRANCHES_CREANCES = ((0, 30, "0–30 j"), (31, 60, "31–60 j"), (61, 90, "61–90 j"), (91, None, "+ 90 j"))


# --------------------------------------------------------------------------- outils
def _num(valeur):
    try:
        return float(valeur or 0)
    except (TypeError, ValueError):
        return 0.0


def _arr(valeur, decimales=2):
    return round(float(valeur or 0), decimales)


class _Periode:
    """Fenêtre analysée, fenêtre précédente et découpage en intervalles."""

    def __init__(self, jours):
        jours = int(jours)
        if jours not in PERIODES:
            raise ValueError("Période invalide : 7, 30, 90 ou 365 jours.")
        self.jours = jours
        self.maintenant = datetime.now()
        self.aujourd_hui = self.maintenant.date()
        self.debut = datetime.combine(self.aujourd_hui - timedelta(days=jours - 1), time.min)
        self.p_debut = self.debut - timedelta(days=jours)
        self.p_fin = self.debut - timedelta(microseconds=1)
        self.granularite = _granularite(jours)
        self.intervalles = []
        d = _debut_periode(self.debut.date(), self.granularite)
        while d <= self.aujourd_hui:
            self.intervalles.append(d)
            d = _suivante(d, self.granularite)

    def intervalle(self, quand):
        """Début de l'intervalle contenant la date, ou None hors période."""
        if not quand or quand < self.debut:
            return None
        cle = _debut_periode(quand.date(), self.granularite)
        return cle if cle in self.intervalles else None

    def fin_intervalle(self, debut):
        fin = datetime.combine(_suivante(debut, self.granularite), time.min) - timedelta(microseconds=1)
        return min(fin, self.maintenant)

    def info(self):
        return {"jours": self.jours, "debut": self.debut.date().isoformat(),
                "fin": self.aujourd_hui.isoformat(), "granularite": self.granularite}


def _devise(magasin_id):
    return str((MagasinModel.get_magasin_by_id(str(magasin_id)) or {}).get("devise") or "XAF").upper()


def _oid(valeur):
    return ObjectId(str(valeur)) if valeur and ObjectId.is_valid(str(valeur)) else None


def _date_vente(vente):
    return heure_locale(vente.get("date_livraison") or vente.get("created_at"))


def _par_ids(table, ids):
    oids = [o for o in (_oid(i) for i in set(ids)) if o]
    if not oids:
        return {}
    return {str(d["_id"]): d for d in db.tables[table].find({"_id": {"$in": oids}})}


def _gerants_du_magasin(magasin_id):
    return list(db.tables.users.find({"magasin_id": ObjectId(magasin_id), "role": {"$in": ["gerant", "livreur"]}}))


# --------------------------------------------------------------------------- stock
def _mouvements(produits):
    """Mouvements datés, avec leur effet sur le stock du magasin et celui des gérants."""
    resultat = []
    for p in produits:
        for m in p.get("mouvements_stock") or []:
            quand = heure_locale(m.get("date"))
            if not quand:
                continue
            q = _num(m.get("quantite"))
            t = _norm(m.get("type"))
            famille, libelle = famille_mouvement(m.get("type"), q)
            if t == "livraison livreur":
                # Vente d'un gérant : la fiche produit n'a pas bougé (déjà retirée à la remise).
                effet_magasin, effet_gerants = 0.0, -abs(q)
            elif famille == "livreurs":
                # Remise au gérant (q < 0) ou retour du gérant (q > 0) : simple déplacement.
                effet_magasin, effet_gerants = q, -q
            else:
                effet_magasin, effet_gerants = q, 0.0
            resultat.append({"date": quand, "produit": str(p["_id"]), "famille": famille, "libelle": libelle,
                             "magasin": effet_magasin, "gerants": effet_gerants})
    return resultat


def onglet_stock(magasin_id, jours=30):
    per = _Periode(jours)
    devise = _devise(magasin_id)
    tous = list(db.tables.produits.find({"magasin_id": ObjectId(magasin_id)}))  # supprimés compris : leurs mouvements comptent
    actifs = [p for p in tous if p.get("deleted_at") is None]
    prix = {str(p["_id"]): _num(p.get("prix_achat")) for p in tous}

    # Stock actuel chez les gérants du magasin
    gerants = _gerants_du_magasin(magasin_id)
    ids_gerants = [g["_id"] for g in gerants]
    chez_gerants = defaultdict(float)
    par_gerant = defaultdict(lambda: {"unites": 0.0, "valeur": 0.0})
    if ids_gerants:
        for s in db.tables.stocks_livreur.find({"livreur_id": {"$in": ids_gerants}}):
            pid, q = str(s.get("produit_id")), _num(s.get("quantite"))
            if pid in prix and q:
                chez_gerants[pid] += q
                par_gerant[str(s["livreur_id"])]["unites"] += q
                par_gerant[str(s["livreur_id"])]["valeur"] += q * prix[pid]

    en_magasin = {str(p["_id"]): max(_num(p.get("quantite")), 0) for p in actifs}
    valeur_magasin = sum(q * prix[pid] for pid, q in en_magasin.items())
    valeur_gerants = sum(q * prix[pid] for pid, q in chez_gerants.items())

    # --- Évolution de la valeur du stock (reconstruite à rebours) -------------------
    mouvements = _mouvements(tous)

    # Quantités de départ par produit. Un produit supprimé garde sa dernière
    # quantité connue : il compte dans le stock passé jusqu'à sa suppression.
    supprime_le = {str(p["_id"]): heure_locale(p.get("deleted_at")) for p in tous if p.get("deleted_at") is not None}
    base_magasin = {str(p["_id"]): max(_num(p.get("quantite")), 0) for p in tous}

    def valeur_a(date):
        """Valeur en magasin et chez les gérants à une date, produit par produit
        (une quantité ne descend jamais sous zéro)."""
        delta_m, delta_g = defaultdict(float), defaultdict(float)
        for m in mouvements:
            if m["date"] > date:
                delta_m[m["produit"]] += m["magasin"]
                delta_g[m["produit"]] += m["gerants"]
        vm = vg = 0.0
        for pid in set(base_magasin) | set(chez_gerants):
            if pid in supprime_le and supprime_le[pid] and date >= supprime_le[pid]:
                continue
            vm += max(base_magasin.get(pid, 0) - delta_m[pid], 0) * prix.get(pid, 0)
            vg += max(chez_gerants.get(pid, 0) - delta_g[pid], 0) * prix.get(pid, 0)
        return vm, vg

    # Valeur en fin de chaque intervalle (le dernier = maintenant), et au début de la période.
    evolution = []
    for d in per.intervalles:
        vm, vg = valeur_a(per.fin_intervalle(d))
        evolution.append({"date": d.isoformat(), "magasin": _arr(vm), "gerants": _arr(vg)})
    vm_debut, vg_debut = valeur_a(per.debut - timedelta(microseconds=1))

    # --- Entrées et sorties par intervalle (hors déplacements vers les gérants) -------
    familles = ("approvisionnements", "retours", "ventes", "pertes", "ajustements")
    flux = {d: {f: {"unites": 0.0, "valeur": 0.0} for f in familles} for d in per.intervalles}
    totaux_flux = {f: {"unites": 0.0, "valeur": 0.0} for f in familles}
    for m in mouvements:
        if m["famille"] == "livreurs":
            continue
        cle = per.intervalle(m["date"])
        if cle is None:
            continue
        q = m["magasin"] + m["gerants"]
        famille = m["famille"] if m["famille"] in familles else "ajustements"
        for cible in (flux[cle][famille], totaux_flux[famille]):
            cible["unites"] += q
            cible["valeur"] += q * prix.get(m["produit"], 0)

    # --- Ventes de la période : couverture et produits qui dorment ---------------------
    vendus = defaultdict(float)
    derniere_vente = {}
    for m in mouvements:
        if m["famille"] == "ventes" and (m["magasin"] + m["gerants"]) < 0:
            if m["date"] >= per.debut:
                vendus[m["produit"]] -= m["magasin"] + m["gerants"]
            if m["date"] > derniere_vente.get(m["produit"], datetime.min):
                derniere_vente[m["produit"]] = m["date"]

    total = {pid: en_magasin.get(pid, 0) + chez_gerants.get(pid, 0) for pid in en_magasin}
    noms = {str(p["_id"]): p.get("nom") or "Article" for p in tous}
    couverture = []
    for pid, qte in vendus.items():
        if pid not in total or qte <= 0:
            continue
        par_jour = qte / per.jours
        couverture.append({"id": pid, "nom": noms[pid], "stock": _arr(total[pid], 3), "vendus": _arr(qte, 3),
                           "par_jour": _arr(par_jour, 3), "jours": _arr(total[pid] / par_jour, 1)})
    couverture.sort(key=lambda x: x["jours"])

    dormants = []
    for p in actifs:
        pid = str(p["_id"])
        if total.get(pid, 0) <= 0:
            continue
        reference = derniere_vente.get(pid) or heure_locale(p.get("created_at"))
        if not reference:
            continue
        age = (per.maintenant - reference).days
        if age >= SEUIL_DORMANT:
            dormants.append({"id": pid, "nom": noms[pid], "stock": _arr(total[pid], 3), "valeur": _arr(total[pid] * prix[pid]),
                             "jours": age, "jamais_vendu": pid not in derniere_vente})
    dormants.sort(key=lambda x: x["valeur"], reverse=True)

    # --- Par catégorie -----------------------------------------------------------------
    categories = {str(c["_id"]): c.get("nom") or "Sans nom" for c in db.tables.categories.find({"magasin_id": ObjectId(magasin_id)})}
    par_categorie = defaultdict(lambda: {"valeur": 0.0, "unites": 0.0, "produits": 0})
    for p in actifs:
        pid = str(p["_id"])
        if total.get(pid, 0) <= 0:
            continue
        nom = categories.get(str(p.get("categorie_id")), "Sans catégorie")
        par_categorie[nom]["valeur"] += total[pid] * prix[pid]
        par_categorie[nom]["unites"] += total[pid]
        par_categorie[nom]["produits"] += 1

    # --- Indicateurs ---------------------------------------------------------------------
    ctx = compta_service._Contexte(magasin_id, per.p_debut, per.maintenant)
    cout_vendu = compta_service._resultat(ctx, per.debut, per.maintenant)["cout"]
    valeur_debut = vm_debut + vg_debut
    valeur_fin = valeur_magasin + valeur_gerants
    stock_moyen = (valeur_debut + valeur_fin) / 2
    # Ruptures et seuils : stock du magasin (ce qui peut être vendu ou remis tout de suite).
    ruptures = [p for p in actifs if en_magasin.get(str(p["_id"]), 0) <= 0]
    sous_seuil = [p for p in actifs if p.get("seuil_alerte") is not None
                  and 0 < en_magasin.get(str(p["_id"]), 0) <= _num(p.get("seuil_alerte"))]
    noms_gerants = {str(g["_id"]): f"{g.get('first_name', '')} {g.get('name', '')}".strip() or g.get("email") for g in gerants}

    return {
        "periode": per.info(),
        "devise": devise,
        "kpis": {
            "valeur_totale": _arr(valeur_fin),
            "valeur_debut": _arr(valeur_debut),
            "variation": _variation(valeur_fin, valeur_debut),
            "valeur_magasin": _arr(valeur_magasin),
            "valeur_gerants": _arr(valeur_gerants),
            "unites": _arr(sum(total.values()), 3),
            "produits": len(actifs),
            "en_stock": sum(1 for q in total.values() if q > 0),
            "ruptures": len(ruptures),
            "sous_seuil": len(sous_seuil),
            "avec_seuil": sum(1 for p in actifs if p.get("seuil_alerte") is not None),
            "dormants": len(dormants),
            "valeur_dormante": _arr(sum(d["valeur"] for d in dormants)),
            "cout_ventes": _arr(cout_vendu),
            # Rotation : combien de fois le stock moyen a été vendu sur la période.
            "rotation": _arr(cout_vendu / stock_moyen, 2) if stock_moyen else None,
            "jours_de_stock": _arr(valeur_fin / (cout_vendu / per.jours), 0) if cout_vendu > 0 else None,
        },
        "evolution": evolution,
        "flux": [{"debut": d.isoformat(), **{f: {k: _arr(v, 3 if k == "unites" else 2) for k, v in flux[d][f].items()} for f in familles}}
                 for d in per.intervalles],
        "totaux_flux": {f: {k: _arr(v, 3 if k == "unites" else 2) for k, v in totaux_flux[f].items()} for f in familles},
        "categories": sorted(({"nom": n, "valeur": _arr(v["valeur"]), "unites": _arr(v["unites"], 3), "produits": v["produits"]}
                              for n, v in par_categorie.items()), key=lambda x: x["valeur"], reverse=True),
        "couverture": couverture[:8],
        "dormants": dormants[:8],
        "gerants": sorted(({"id": gid, "nom": noms_gerants.get(gid, "—"), "unites": _arr(v["unites"], 3), "valeur": _arr(v["valeur"])}
                           for gid, v in par_gerant.items()), key=lambda x: x["valeur"], reverse=True),
    }


# --------------------------------------------------------------------------- ventes
def onglet_ventes(magasin_id, jours=30):
    per = _Periode(jours)
    devise = _devise(magasin_id)
    ventes = _ventes_du_magasin(magasin_id, per.p_debut, per.maintenant)
    courantes = [v for v in ventes if _date_vente(v) and _date_vente(v) >= per.debut]

    produits = list(db.tables.produits.find({"magasin_id": ObjectId(magasin_id)}))
    categorie_produit = {str(p["_id"]): str(p.get("categorie_id") or "") for p in produits}
    categories = {str(c["_id"]): c.get("nom") or "Sans nom" for c in db.tables.categories.find({"magasin_id": ObjectId(magasin_id)})}

    # Carte de chaleur : jour de la semaine × heure
    chaleur = [[{"ventes": 0, "ca": 0.0} for _ in range(24)] for _ in range(7)]
    par_produit = defaultdict(lambda: {"nom": "", "quantite": 0.0, "ca": 0.0, "cout": 0.0})
    par_categorie = defaultdict(lambda: {"quantite": 0.0, "ca": 0.0, "cout": 0.0})
    par_intervalle = {d: {"ventes": 0, "ca": 0.0} for d in per.intervalles}
    remises = brut = 0.0
    for v in courantes:
        quand = _date_vente(v)
        ht = _num(v.get("montant_produits")) if v.get("montant_produits") is not None else sum(_num(l.get("quantite")) * _num(l.get("prix")) for l in v.get("produits") or [])
        chaleur[quand.weekday()][quand.hour]["ventes"] += 1
        chaleur[quand.weekday()][quand.hour]["ca"] += ht
        if (cle := per.intervalle(quand)) is not None:
            par_intervalle[cle]["ventes"] += 1
            par_intervalle[cle]["ca"] += ht
        remises += _num(v.get("total_reductions"))
        # Les remises de la vente sont réparties sur ses lignes : la somme des
        # catégories (et des articles) retombe sur le chiffre d'affaires HT.
        brut_vente = sum(_num(l.get("quantite")) * _num(l.get("prix")) for l in v.get("produits") or [])
        part = ht / brut_vente if brut_vente else 0.0
        for l in v.get("produits") or []:
            pid = str(l.get("produit_id"))
            q, pu, pa = _num(l.get("quantite")), _num(l.get("prix")), _num(l.get("prix_achat"))
            brut += q * pu
            for cible in (par_produit[pid], par_categorie[categories.get(categorie_produit.get(pid, ""), "Sans catégorie")]):
                cible["quantite"] += q
                cible["ca"] += q * pu * part
                cible["cout"] += q * pa
            par_produit[pid]["nom"] = l.get("nom") or par_produit[pid]["nom"] or "Article"

    def avec_marge(d):
        marge = d["ca"] - d["cout"]
        return {"marge": _arr(marge), "taux_marge": _arr(marge / d["ca"] * 100, 1) if d["ca"] else None}

    # Retours de la période
    # Retours de la période (sauf ceux d'une vente annulée, comme dans la page Finance)
    annulees = {str(v["_id"]) for v in db.tables.livraisons.find({"magasin_id": ObjectId(magasin_id)}) if v.get("deleted_at") is not None}
    retours_produits = defaultdict(lambda: {"nom": "", "quantite": 0.0})
    montant_retours = 0.0
    nb_retours = 0
    for r in db.tables.retours_livraison.find({"magasin_id": ObjectId(magasin_id)}):
        quand = heure_locale(r.get("created_at"))
        if not quand or quand < per.debut or str(r.get("livraison_id")) in annulees:
            continue
        nb_retours += 1
        montant = _num(r.get("montant_ht")) or _num(r.get("montant_retour"))
        montant_retours += montant
        for p in r.get("produits") or []:
            pid = str(p.get("produit_id"))
            retours_produits[pid]["nom"] = p.get("nom") or retours_produits[pid]["nom"] or "Article"
            retours_produits[pid]["quantite"] += _num(p.get("quantite"))

    nb = len(courantes)
    ca = sum(x["ca"] for x in par_intervalle.values())
    meilleur = max(((j, h, c) for j, ligne in enumerate(chaleur) for h, c in enumerate(ligne)), key=lambda x: x[2]["ventes"], default=None)
    return {
        "periode": per.info(),
        "devise": devise,
        "kpis": {
            "ventes": nb,
            "ca": _arr(ca),
            "panier_moyen": _arr(ca / nb) if nb else 0,
            "articles_par_vente": _arr(sum(x["quantite"] for x in par_produit.values()) / nb, 1) if nb else 0,
            "remises": _arr(remises),
            "taux_remise": _arr(remises / brut * 100, 1) if brut else 0,
            "retours": nb_retours,
            "montant_retours": _arr(montant_retours),
            "taux_retour": _arr(montant_retours / (ca + montant_retours) * 100, 1) if (ca + montant_retours) else 0,
            "meilleur_creneau": {"jour": meilleur[0], "heure": meilleur[1], "ventes": meilleur[2]["ventes"]}
            if meilleur and meilleur[2]["ventes"] else None,
        },
        "chaleur": [[{"ventes": c["ventes"], "ca": _arr(c["ca"])} for c in ligne] for ligne in chaleur],
        "evolution": [{"debut": d.isoformat(), "ventes": x["ventes"], "ca": _arr(x["ca"]),
                       "panier_moyen": _arr(x["ca"] / x["ventes"]) if x["ventes"] else None}
                      for d, x in par_intervalle.items()],
        "categories": sorted(({"nom": n, "quantite": _arr(x["quantite"], 3), "ca": _arr(x["ca"]), **avec_marge(x)}
                              for n, x in par_categorie.items()), key=lambda x: x["ca"], reverse=True),
        "produits": sorted(({"id": pid, "nom": x["nom"], "quantite": _arr(x["quantite"], 3), "ca": _arr(x["ca"]), **avec_marge(x)}
                            for pid, x in par_produit.items()), key=lambda x: x["ca"], reverse=True)[:40],
        "retours_produits": sorted(({"id": pid, **x} for pid, x in retours_produits.items()), key=lambda x: x["quantite"], reverse=True)[:6],
    }


# --------------------------------------------------------------------------- clients
def onglet_clients(magasin_id, jours=30):
    per = _Periode(jours)
    devise = _devise(magasin_id)
    clients = {str(c["_id"]): c for c in db.tables.clients.find({"magasin_id": ObjectId(magasin_id)}) if c.get("deleted_at") is None}
    passage = {cid for cid, c in clients.items() if c.get("nom") == "Autres"}
    toutes = [v for v in db.tables.livraisons.find({"magasin_id": ObjectId(magasin_id)}) if v.get("deleted_at") is None]

    premiere, derniere = {}, {}
    achats = defaultdict(lambda: {"ventes": 0, "ca": 0.0})
    for v in toutes:
        cid, quand = str(v.get("client_id") or ""), _date_vente(v)
        if not cid or not quand or cid in passage:
            continue
        premiere[cid] = min(premiere.get(cid, quand), quand)
        derniere[cid] = max(derniere.get(cid, quand), quand)
        if quand >= per.debut:
            achats[cid]["ventes"] += 1
            achats[cid]["ca"] += _num(v.get("montant_produits"))

    # Acheteurs nouveaux / fidèles par intervalle
    evolution = {d: {"nouveaux": set(), "fideles": set()} for d in per.intervalles}
    for v in toutes:
        cid, quand = str(v.get("client_id") or ""), _date_vente(v)
        cle = per.intervalle(quand)
        if cle is None or not cid or cid in passage:
            continue
        nouveau = _debut_periode(premiere[cid].date(), per.granularite) == cle
        evolution[cle]["nouveaux" if nouveau else "fideles"].add(cid)
    ventes_passage = sum(1 for v in toutes if str(v.get("client_id")) in passage and (_date_vente(v) or datetime.min) >= per.debut)
    ventes_periode = sum(1 for v in toutes if (_date_vente(v) or datetime.min) >= per.debut)

    # Ancienneté des créances
    tranches = [{"tranche": lib, "montant": 0.0, "ventes": 0} for _, _, lib in TRANCHES_CREANCES]
    for v in toutes:
        reste = max(_num(v.get("montant_total")) - _num(v.get("montant_paye")), 0)
        quand = _date_vente(v)
        if reste <= 0.009 or not quand:
            continue
        age = (per.maintenant - quand).days
        for i, (mini, maxi, _) in enumerate(TRANCHES_CREANCES):
            if age >= mini and (maxi is None or age <= maxi):
                tranches[i]["montant"] += reste
                tranches[i]["ventes"] += 1
                break

    # Clients à relancer : ont déjà acheté, plus rien depuis SEUIL_INACTIF jours
    a_relancer = sorted(({"id": cid, "nom": clients[cid].get("nom") or "—", "telephone": clients[cid].get("telephone"),
                          "jours": (per.maintenant - d).days}
                         for cid, d in derniere.items() if cid in clients and (per.maintenant - d).days >= SEUIL_INACTIF),
                        key=lambda x: x["jours"])

    villes = Counter((c.get("ville") or "").strip() or "Non renseignée" for cid, c in clients.items() if cid not in passage)
    villes = [{"ville": "Non renseignée" if v in ("N/A", "n/a") else v, "clients": n} for v, n in villes.most_common(8)]

    nouveaux_fiches = sum(1 for cid, c in clients.items() if cid not in passage and heure_locale(c.get("created_at"))
                          and heure_locale(c.get("created_at")) >= per.debut)
    acheteurs = len(achats)
    fideles = sum(1 for cid in achats if premiere[cid] < per.debut)
    return {
        "periode": per.info(),
        "devise": devise,
        "kpis": {
            "clients": len(clients) - len(passage),
            "nouveaux": nouveaux_fiches,
            "acheteurs": acheteurs,
            "fideles": fideles,
            "taux_fidelite": _arr(fideles / acheteurs * 100, 1) if acheteurs else None,
            "part_passage": _arr(ventes_passage / ventes_periode * 100, 1) if ventes_periode else 0,
            "a_relancer": len(a_relancer),
            "creances": _arr(sum(t["montant"] for t in tranches)),
        },
        "evolution": [{"debut": d.isoformat(), "nouveaux": len(x["nouveaux"]), "fideles": len(x["fideles"])} for d, x in evolution.items()],
        "creances": [{**t, "montant": _arr(t["montant"])} for t in tranches],
        "a_relancer": a_relancer[:8],
        "villes": villes,
        "meilleurs": sorted(({"id": cid, "nom": clients[cid].get("nom") or "—", "ventes": x["ventes"], "ca": _arr(x["ca"]),
                              "derniere": derniere[cid].isoformat()} for cid, x in achats.items() if cid in clients),
                            key=lambda x: x["ca"], reverse=True)[:8],
    }


# --------------------------------------------------------------------------- finances
def onglet_finances(magasin_id, jours=30):
    per = _Periode(jours)
    devise = _devise(magasin_id)
    debut_12 = datetime.combine(per.aujourd_hui.replace(day=1) - relativedelta(months=11), time.min)
    ctx = compta_service._Contexte(magasin_id, min(debut_12, per.p_debut), per.maintenant)
    r = compta_service._resultat(ctx, per.debut, per.maintenant)
    p = compta_service._resultat(ctx, per.p_debut, per.p_fin)

    # Cascade : des ventes au résultat net
    etapes = [
        ("ventes_brutes", "Ventes HT", r["ventes_brutes"], "total"),
        ("retours", "Retours clients", -r["retours"], "delta"),
        ("ca", "Ventes nettes de marchandises", r["ca"], "total"),
        ("cout", "Coût des marchandises vendues", -r["cout"], "delta"),
        ("marge", "Marge brute", r["marge"], "total"),
        ("frais_retour", "Frais de retour retenus (HT)", r["frais_retour"], "delta"),
        ("pertes", "Pertes et avaries", -r["pertes"], "delta"),
        ("externes", "Charges externes", -r["externes"], "delta"),
        ("personnel", "Personnel", -r["personnel"], "delta"),
        ("impots", "Impôts et taxes", -r["impots"], "delta"),
        ("dotations", "Amortissements", -r["dotations"], "delta"),
        ("financieres", "Charges financières", -r["financieres"], "delta"),
        ("net", "Résultat net", r["net"], "total"),
    ]
    cascade = [{"cle": c, "libelle": lib, "montant": _arr(m), "type": t} for c, lib, m, t in etapes
               if t == "total" or abs(m) > 0.004]

    # 12 derniers mois
    mois = []
    for i in range(12):
        d = debut_12 + relativedelta(months=i)
        f = min(d + relativedelta(months=1) - timedelta(microseconds=1), per.maintenant)
        m = compta_service._resultat(ctx, d, f)
        charges = m["pertes"] + m["externes"] + m["impots"] + m["personnel"] + m["dotations"] + m["financieres"]
        mois.append({"mois": d.strftime("%Y-%m"), "ca": _arr(m["ca"]), "marge": _arr(m["marge"]),
                     "charges": _arr(charges), "resultat": _arr(m["net"])})

    charges_cat = sorted(({"categorie": k, "libelle": compta_service.CATEGORIES_CHARGES.get(k, (k.replace("_", " ").capitalize(),))[0],
                           "montant": _arr(v)} for k, v in r["par_categorie"].items() if v), key=lambda x: x["montant"], reverse=True)
    flux = compta_service._flux(ctx, per.debut, per.maintenant)

    charges_fixes = r["externes"] + r["impots"] + r["personnel"] + r["dotations"] + r["financieres"]
    taux_marge = r["marge"] / r["ca"] if r["ca"] else 0
    tva_due = r["tva_collectee"] - r["tva_deductible"]
    return {
        "periode": per.info(),
        "devise": devise,
        "kpis": {
            "ca": _arr(r["ca"]), "ca_variation": _variation(r["ca"], p["ca"]),
            "marge": _arr(r["marge"]), "taux_marge": _arr(taux_marge * 100, 1) if r["ca"] else None,
            "resultat": _arr(r["net"]), "resultat_variation": _variation(r["net"], p["net"]),
            "charges": _arr(charges_fixes + r["pertes"]),
            "seuil_rentabilite": _arr(charges_fixes / taux_marge) if taux_marge > 0 else None,
            "tva_collectee": _arr(r["tva_collectee"]), "tva_deductible": _arr(r["tva_deductible"]), "tva_a_payer": _arr(tva_due),
            "dettes_fournisseurs": _arr(ctx.dettes_factures(per.maintenant)),
        },
        "cascade": cascade,
        "mois": mois,
        "charges": charges_cat,
        "tresorerie": {
            "entrees": _arr(flux["entrees_total"]),
            "sorties": [{"cle": k, "montant": _arr(flux[k])} for k in (
                "remboursements", "charges", "achats", "fournisseurs", "investissements", "retraits_clients",
                "retraits_exploitant", "remboursements_emprunts", "impots_verses") if flux[k]],
            "total_sorties": _arr(flux["sorties"]),
            "net": _arr(flux["net"]),
        },
    }


# --------------------------------------------------------------------------- équipe
def onglet_equipe(magasin_id, jours=30):
    per = _Periode(jours)
    devise = _devise(magasin_id)
    membres = [u for u in db.tables.users.find({"magasin_id": ObjectId(magasin_id)}) if u.get("role") != "staff"]
    ventes = _ventes_du_magasin(magasin_id, per.p_debut, per.maintenant)
    stats = defaultdict(lambda: {"ventes": 0, "ca": 0.0, "articles": 0.0, "encaisse": 0.0, "reste": 0.0,
                                 "ventes_prec": 0, "ca_prec": 0.0, "par_intervalle": defaultdict(float)})
    for v in ventes:
        uid = str(v.get("livreur_id") or v.get("manager_id") or "")
        quand = _date_vente(v)
        if not uid or not quand:
            continue
        ht = _num(v.get("montant_produits"))
        s = stats[uid]
        if quand >= per.debut:
            s["ventes"] += 1
            s["ca"] += ht
            s["articles"] += sum(_num(l.get("quantite")) for l in v.get("produits") or [])
            s["encaisse"] += _num(v.get("montant_paye"))
            s["reste"] += max(_num(v.get("montant_total")) - _num(v.get("montant_paye")), 0)
            if (cle := per.intervalle(quand)) is not None:
                s["par_intervalle"][cle] += ht
        else:
            s["ventes_prec"] += 1
            s["ca_prec"] += ht

    gerants_stock = {}
    ids = [u["_id"] for u in membres]
    prix = {str(p["_id"]): _num(p.get("prix_achat")) for p in db.tables.produits.find({"magasin_id": ObjectId(magasin_id)})}
    if ids:
        for s in db.tables.stocks_livreur.find({"livreur_id": {"$in": ids}}):
            g = gerants_stock.setdefault(str(s["livreur_id"]), {"unites": 0.0, "valeur": 0.0})
            q = _num(s.get("quantite"))
            g["unites"] += q
            g["valeur"] += q * prix.get(str(s.get("produit_id")), 0)

    connexions = defaultdict(lambda: {"nombre": 0, "derniere": None})
    for c in db.tables.connexions.find({"magasin_id": ObjectId(magasin_id)}):
        quand = heure_locale(c.get("timestamp"))
        uid = str(c.get("user_id") or "")
        if not quand or not uid:
            continue
        if quand >= per.debut:
            connexions[uid]["nombre"] += 1
        if connexions[uid]["derniere"] is None or quand > connexions[uid]["derniere"]:
            connexions[uid]["derniere"] = quand

    resultat = []
    for u in membres:
        uid = str(u["_id"])
        s = stats.get(uid, stats.default_factory())
        role = "gerant" if u.get("role") == "livreur" else u.get("role")
        resultat.append({
            "id": uid,
            "nom": f"{u.get('first_name', '')} {u.get('name', '')}".strip() or u.get("email") or "—",
            "email": u.get("email") or None,
            "role": role,
            "actif": bool(u.get("is_active")),
            "ventes": s["ventes"], "ca": _arr(s["ca"]), "articles": _arr(s["articles"], 3),
            "encaisse": _arr(s["encaisse"]), "reste": _arr(s["reste"]),
            "variation": _variation(s["ca"], s["ca_prec"]),
            "panier_moyen": _arr(s["ca"] / s["ventes"]) if s["ventes"] else 0,
            "stock_unites": _arr(gerants_stock.get(uid, {}).get("unites", 0), 3),
            "stock_valeur": _arr(gerants_stock.get(uid, {}).get("valeur", 0)),
            "connexions": connexions[uid]["nombre"],
            "derniere_connexion": connexions[uid]["derniere"].isoformat() if connexions[uid]["derniere"] else None,
            "serie": [{"debut": d.isoformat(), "ca": _arr(s["par_intervalle"].get(d, 0))} for d in per.intervalles],
        })
    resultat.sort(key=lambda x: (-x["ca"], x["nom"].lower()))
    total_ca = sum(m["ca"] for m in resultat)
    for m in resultat:
        m["part"] = _arr(m["ca"] / total_ca * 100, 1) if total_ca else 0
    return {"periode": per.info(), "devise": devise, "membres": resultat, "ca_total": _arr(total_ca)}


ONGLETS = {
    "stock": onglet_stock,
    "ventes": onglet_ventes,
    "clients": onglet_clients,
    "finances": onglet_finances,
    "equipe": onglet_equipe,
}
