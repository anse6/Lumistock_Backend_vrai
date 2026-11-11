"""Statistiques de la plateforme pour le tableau de bord du staff (super-admin).

Règles communes à tous les indicateurs :
- les dates sont enregistrées en UTC et regroupées à l'heure locale des
  magasins (APP_TIMEZONE) : une vente faite à 0 h 30 compte pour ce jour-là ;
- une vente compte si elle n'est ni à la corbeille ni annulée ;
- le chiffre d'affaires est le montant HT net des produits (après réductions),
  sans la TVA ni la dette ancienne reportée sur la vente ;
- les montants ne sont jamais additionnés entre magasins de devises
  différentes : chaque magasin garde sa devise.
- les lectures filtrent en SQL (dates, suppression) au lieu de charger des
  tables entières en mémoire.
"""

import os
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

from dateutil.relativedelta import relativedelta
from flask import current_app, request
from sqlalchemy import func, select
from werkzeug.utils import secure_filename

from database.codec import from_row
from database.schema import tables
from extensions import db
from models.image_staff_model import ImageStaffModel
from services.tva_service import decimales_devise
from models.magasin_model import MAX_CAISSIERS_DEFAUT

# Nombre de périodes affichées par le graphique d'utilisation.
GRANULARITES = {"jour": 7, "semaine": 8, "mois": 12, "annee": 5}
PERIODES_VUE_ENSEMBLE = (7, 30, 90, 365)
STATUTS_PAIEMENT = ("payé", "partiellement payé", "non payé")
METHODES_PAIEMENT = ("cash", "om", "momo", "bancaire")
DEVISE_PAR_DEFAUT = "XAF"


# --------------------------------------------------------------------------
# Outils
# --------------------------------------------------------------------------

def _lire(nom, *conditions, colonnes=None, ordre=None, limite=None):
    """Lit une table avec des filtres SQL.

    Sans ``colonnes`` : documents complets (même format que les modèles).
    Avec ``colonnes`` : lignes brutes réduites aux colonnes demandées, pour
    les comptages sur de grosses tables (produits, clients, connexions).
    """
    table = tables[nom]
    requete = select(*[table.c[c] for c in colonnes]) if colonnes else select(table)
    for condition in conditions:
        requete = requete.where(condition)
    if ordre is not None:
        requete = requete.order_by(ordre)
    if limite:
        requete = requete.limit(limite)
    with db.connection() as connexion:
        lignes = connexion.execute(requete).mappings().all()
    if colonnes:
        return [dict(ligne) for ligne in lignes]
    return [from_row(table, ligne) for ligne in lignes]


def _c(nom):
    """Colonnes d'une table, pour écrire les filtres SQL."""
    return tables[nom].c


def _num(valeur):
    try:
        return float(valeur or 0)
    except (TypeError, ValueError):
        return 0.0


def _local(valeur):
    """Horodatage UTC (naïf, comme en base) -> heure locale naïve."""
    if not isinstance(valeur, datetime):
        return None
    if valeur.tzinfo is None:
        valeur = valeur.replace(tzinfo=timezone.utc)
    return valeur.astimezone().replace(tzinfo=None)


def _utc(local):
    """Heure locale naïve -> UTC naïve, pour filtrer en base."""
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(valeur):
    local = _local(valeur)
    return local.isoformat() if local else None


def _variation(actuel, precedent):
    """Évolution en % ; None quand la période précédente est vide (pas de base)."""
    if not precedent:
        return None
    return round((actuel - precedent) / precedent * 100, 1)


def _role(role):
    """L'ancien rôle « livreur » est le rôle « gérant » actuel."""
    return "gerant" if role == "livreur" else role


def _vente_valide(livraison):
    return livraison.get("deleted_at") is None and livraison.get("statut_livraison") != "annulée"


def _ca_ht(livraison):
    """Montant HT net des produits. Les anciennes ventes sans ce champ sont
    reconstituées : total - TVA - dette ancienne."""
    if livraison.get("montant_produits") is not None:
        # 0 est une vraie valeur (vente entièrement retournée) : ne pas
        # retomber sur le total, qui contient les frais de retour retenus.
        return _num(livraison.get("montant_produits"))
    return max(_num(livraison.get("montant_total")) - _num(livraison.get("montant_tva"))
               - _num(livraison.get("dette_ancienne")) - _num(livraison.get("frais_retour")), 0.0)


def _articles(livraison):
    return int(sum(_num(p.get("quantite")) for p in livraison.get("produits") or [] if isinstance(p, dict)))


def _ventes(debut_utc, fin_utc=None):
    """Ventes valides créées dans la fenêtre [debut, fin[."""
    c = _c("livraisons")
    conditions = [c.deleted_at.is_(None), c.created_at >= debut_utc]
    if fin_utc is not None:
        conditions.append(c.created_at < fin_utc)
    return [l for l in _lire("livraisons", *conditions) if _vente_valide(l)]


def _devise(magasin):
    return str(magasin.get("devise") or DEVISE_PAR_DEFAUT).upper()


def _statut_magasin(magasin):
    if magasin.get("deleted_at") is not None:
        return "archive"
    return "actif" if magasin.get("is_active") else "desactive"


# --------------------------------------------------------------------------
# Graphique « Utilisation de la plateforme »
# --------------------------------------------------------------------------

def _debut_periode(jour, granularite):
    if granularite == "jour":
        return jour
    if granularite == "semaine":
        return jour - timedelta(days=jour.weekday())
    if granularite == "mois":
        return jour.replace(day=1)
    return jour.replace(month=1, day=1)


def _decaler(debut, granularite, n):
    if granularite == "jour":
        return debut + timedelta(days=n)
    if granularite == "semaine":
        return debut + timedelta(weeks=n)
    if granularite == "mois":
        return debut + relativedelta(months=n)
    return debut + relativedelta(years=n)


def _cle(debut, granularite):
    if granularite == "jour":
        return debut.isoformat()
    if granularite == "semaine":
        annee, semaine, _ = debut.isocalendar()
        return f"{annee}-W{semaine:02d}"
    if granularite == "mois":
        return f"{debut.year}-{debut.month:02d}"
    return str(debut.year)


def _periodes(granularite, page):
    """Les N périodes d'une page. Page 0 se termine par la période en cours,
    page -1 par celle d'avant le bloc, etc. (jamais de périodes futures)."""
    n = GRANULARITES[granularite]
    derniere = _decaler(_debut_periode(date.today(), granularite), granularite, page * n)
    premiere = _decaler(derniere, granularite, -(n - 1))
    periodes = []
    for i in range(n):
        debut = _decaler(premiere, granularite, i)
        fin = _decaler(debut, granularite, 1) - timedelta(days=1)
        periodes.append((_cle(debut, granularite), debut, fin))
    return periodes


def get_stats_utilisation_globale(granularite="semaine", page=0):
    """Connexions, ventes et articles vendus par période, comparés au bloc
    de périodes précédent de même longueur."""
    if granularite not in GRANULARITES:
        raise ValueError("Granularité invalide : jour, semaine, mois ou annee.")
    page = min(int(page), 0)

    periodes = _periodes(granularite, page)
    precedentes = _periodes(granularite, page - 1)
    aujourd_hui = date.today()

    debut_utc = _utc(datetime.combine(precedentes[0][1], time.min))
    fin_utc = _utc(datetime.combine(periodes[-1][2] + timedelta(days=1), time.min))
    debut_bloc_utc = _utc(datetime.combine(periodes[0][1], time.min))

    c = _c("connexions")
    connexions = _lire("connexions", c.timestamp >= debut_utc, c.timestamp < fin_utc,
                       colonnes=("user_id", "magasin_id", "timestamp"))
    ventes = _ventes(debut_utc, fin_utc)

    compteurs = {m: defaultdict(int) for m in ("connexions", "livraisons", "produits_livres")}
    utilisateurs_actifs, magasins_actifs = set(), set()

    def cle_de(horodatage):
        local = _local(horodatage)
        return _cle(_debut_periode(local.date(), granularite), granularite) if local else None

    for connexion in connexions:
        cle = cle_de(connexion["timestamp"])
        if cle:
            compteurs["connexions"][cle] += 1
        if connexion["timestamp"] >= debut_bloc_utc:
            if connexion.get("user_id"):
                utilisateurs_actifs.add(str(connexion["user_id"]))
            if connexion.get("magasin_id"):
                magasins_actifs.add(str(connexion["magasin_id"]))

    for vente in ventes:
        cle = cle_de(vente.get("created_at"))
        if cle:
            compteurs["livraisons"][cle] += 1
            compteurs["produits_livres"][cle] += _articles(vente)
        if vente.get("created_at") and vente["created_at"] >= debut_bloc_utc and vente.get("magasin_id"):
            magasins_actifs.add(str(vente["magasin_id"]))

    series, totaux, precedents, croissance = {}, {}, {}, {}
    for metrique, valeurs in compteurs.items():
        series[metrique] = [{
            "date": cle,
            "count": valeurs.get(cle, 0),
            "debut": debut.isoformat(),
            "fin": fin.isoformat(),
            # Période en cours : ses chiffres vont encore augmenter.
            "incomplet": fin >= aujourd_hui,
        } for cle, debut, fin in periodes]
        totaux[metrique] = sum(point["count"] for point in series[metrique])
        precedents[metrique] = sum(valeurs.get(cle, 0) for cle, _, _ in precedentes)
        croissance[metrique] = _variation(totaux[metrique], precedents[metrique])

    totaux["utilisateurs_actifs"] = len(utilisateurs_actifs)
    totaux["magasins_actifs"] = len(magasins_actifs)
    return {
        "periode": granularite,
        "page": page,
        "debut": periodes[0][1].isoformat(),
        "fin": periodes[-1][2].isoformat(),
        "growth_rate": croissance,
        "series": series,
        "totaux": totaux,
        "totaux_precedents": precedents,
    }


# --------------------------------------------------------------------------
# Utilisateurs récemment connectés
# --------------------------------------------------------------------------

def get_utilisateurs_recents_connectes_service(limit=10):
    """Derniers utilisateurs connectés (une ligne par compte, la plus récente)."""
    c = _c("connexions")
    connexions = _lire("connexions", colonnes=("user_id", "email", "role", "magasin_id", "timestamp", "ip_address"),
                       ordre=c.timestamp.desc(), limite=max(limit * 40, 200))
    vues, retenues = set(), []
    for connexion in connexions:
        identifiant = str(connexion.get("user_id") or connexion.get("email") or "")
        if not identifiant or identifiant in vues:
            continue
        vues.add(identifiant)
        retenues.append(connexion)
        if len(retenues) >= limit:
            break

    ids_users = [str(x["user_id"]) for x in retenues if x.get("user_id")]
    ids_magasins = [str(x["magasin_id"]) for x in retenues if x.get("magasin_id")]
    users = {str(u["_id"]): u for u in _lire("users", _c("users")._id.in_(ids_users))} if ids_users else {}
    magasins = {str(m["_id"]): m for m in _lire("magasin", _c("magasin")._id.in_(ids_magasins))} if ids_magasins else {}

    resultats = []
    for connexion in retenues:
        user = users.get(str(connexion.get("user_id")))
        item = {
            "user_id": str(connexion.get("user_id") or ""),
            "email": (user or {}).get("email") or connexion.get("email", ""),
            "role": _role((user or {}).get("role") or connexion.get("role", "")),
            "date_connexion": _iso(connexion.get("timestamp")),
            "ip": connexion.get("ip_address") or "",
            "is_active": bool(user.get("is_active", False)) if user else False,
            "nom_complet": f"{(user or {}).get('first_name', '')} {(user or {}).get('name', '')}".strip(),
        }
        magasin = magasins.get(str(connexion.get("magasin_id")))
        if magasin:
            item["magasin"] = {"id": str(magasin["_id"]), "denomination": magasin.get("denomination", "")}
        resultats.append(item)

    return {"utilisateurs_recents_connectes": resultats, "total": len(resultats)}


# --------------------------------------------------------------------------
# Meilleurs magasins
# --------------------------------------------------------------------------

def get_top_magasin_par_chiffre_affaire(limit=5, jours=None):
    """Magasins classés par chiffre d'affaires HT (toutes périodes ou N jours)."""
    if jours:
        debut = datetime.combine(date.today() - timedelta(days=int(jours) - 1), time.min)
        ventes = _ventes(_utc(debut))
    else:
        ventes = _ventes(datetime(1970, 1, 1))

    par_magasin = defaultdict(lambda: {"ca": 0.0, "ventes": 0})
    for vente in ventes:
        if vente.get("magasin_id"):
            cumul = par_magasin[str(vente["magasin_id"])]
            cumul["ca"] += _ca_ht(vente)
            cumul["ventes"] += 1

    ids = list(par_magasin)
    magasins = {str(m["_id"]): m for m in _lire("magasin", _c("magasin")._id.in_(ids))} if ids else {}
    classement = sorted(par_magasin.items(), key=lambda x: x[1]["ca"], reverse=True)

    top = []
    for magasin_id, cumul in classement:
        magasin = magasins.get(magasin_id)
        if not magasin:
            continue
        devise = _devise(magasin)
        top.append({
            "magasin_id": magasin_id,
            "denomination": magasin.get("denomination") or "Magasin sans nom",
            "chiffre_affaire": round(cumul["ca"], decimales_devise(devise)),
            "devise": devise,
            "nombre_ventes": cumul["ventes"],
        })
        if len(top) >= limit:
            break
    return {"meilleurs_magasins": top, "granularite": f"{jours} jours" if jours else "totale"}


# --------------------------------------------------------------------------
# Cartes « totaux » (anciens endpoints, conservés)
# --------------------------------------------------------------------------

def _total_et_variation_hebdo(nom, filtre):
    """Total des fiches actives + évolution des créations : semaine en cours
    (lundi -> maintenant) contre la même durée de la semaine précédente."""
    maintenant = datetime.now()
    lundi = datetime.combine(maintenant.date() - timedelta(days=maintenant.weekday()), time.min)
    lundi_prec = lundi - timedelta(weeks=1)
    ecoule = maintenant - lundi

    documents = [d for d in _lire(nom, _c(nom).deleted_at.is_(None)) if filtre(d)]
    actuel = precedent = 0
    for document in documents:
        cree = _local(document.get("created_at"))
        if not cree:
            continue
        if lundi <= cree < maintenant:
            actuel += 1
        elif lundi_prec <= cree < lundi_prec + ecoule:
            precedent += 1
    variation = _variation(actuel, precedent)
    return {
        "valeur": len(documents),
        "variation_percent": variation if variation is not None else (100.0 if actuel else 0.0),
        "periode": {"debut": lundi.isoformat(), "fin": maintenant.isoformat()},
    }


def get_nombre_magasins_total():
    return _total_et_variation_hebdo("magasin", lambda m: True)


def get_nombre_admins_managers_total():
    return _total_et_variation_hebdo("users", lambda u: u.get("role") in ("admin", "manager"))


def get_nombre_livreurs_total():
    return _total_et_variation_hebdo("users", lambda u: u.get("role") in ("livreur", "gerant"))


def get_nombre_clients_total():
    return _total_et_variation_hebdo("clients", lambda c: True)


# --------------------------------------------------------------------------
# Vue d'ensemble du tableau de bord super-admin
# --------------------------------------------------------------------------

def _max_par_magasin(nom, colonne, *conditions):
    """Date la plus récente de ``colonne`` pour chaque magasin (agrégat SQL)."""
    table = tables[nom]
    requete = select(table.c.magasin_id, func.max(table.c[colonne])).group_by(table.c.magasin_id)
    for condition in conditions:
        requete = requete.where(condition)
    with db.connection() as connexion:
        return {str(m): d for m, d in connexion.execute(requete).all() if m and d}


def get_vue_ensemble_plateforme(jours=30):
    """Toutes les données du tableau de bord super-admin, en un seul appel.

    :param jours: longueur de la période analysée (7, 30, 90 ou 365), comparée
                  à la période de même longueur juste avant.
    """
    jours = int(jours)
    if jours not in PERIODES_VUE_ENSEMBLE:
        raise ValueError("Période invalide : 7, 30, 90 ou 365 jours.")

    maintenant = datetime.now()
    aujourd_hui = maintenant.date()
    debut = datetime.combine(aujourd_hui - timedelta(days=jours - 1), time.min)
    debut_prec = debut - timedelta(days=jours)
    debut_12_mois = datetime.combine(aujourd_hui.replace(day=1) - relativedelta(months=11), time.min)
    debut_utc, debut_prec_utc = _utc(debut), _utc(debut_prec)

    def periode(horodatage):
        """'courante', 'precedente' ou None selon la date (locale)."""
        local = _local(horodatage)
        if not local:
            return None
        if local >= debut:
            return "courante"
        if local >= debut_prec:
            return "precedente"
        return None

    # --- Lectures (filtrées en SQL) ---------------------------------------
    magasins = _lire("magasin")
    users = [u for u in _lire("users") if u.get("role") != "staff"]
    clients = _lire("clients", _c("clients").deleted_at.is_(None), colonnes=("magasin_id", "created_at"))
    produits = _lire("produits", _c("produits").deleted_at.is_(None), colonnes=("magasin_id", "quantite"))
    ventes = _ventes(min(debut_prec_utc, _utc(debut_12_mois)))
    c = _c("connexions")
    connexions = _lire("connexions", c.timestamp >= debut_prec_utc, colonnes=("user_id", "magasin_id", "timestamp"))
    derniere_vente = _max_par_magasin("livraisons", "created_at", _c("livraisons").deleted_at.is_(None))
    derniere_connexion = _max_par_magasin("connexions", "timestamp")

    magasins_par_id = {str(m["_id"]): m for m in magasins}

    # --- Magasins -----------------------------------------------------------
    statuts_magasins = Counter(_statut_magasin(m) for m in magasins)
    creations_magasins = Counter(periode(m.get("created_at")) for m in magasins)

    # --- Utilisateurs -------------------------------------------------------
    def user_actif(user):
        if not user.get("is_active"):
            return False
        magasin = magasins_par_id.get(str(user.get("magasin_id") or ""))
        return magasin is None or _statut_magasin(magasin) == "actif"

    roles = Counter(_role(u.get("role")) for u in users)
    roles_actifs = Counter(_role(u.get("role")) for u in users if user_actif(u))
    creations_users = Counter(periode(u.get("created_at")) for u in users)

    # --- Clients et produits --------------------------------------------------
    creations_clients = Counter(periode(x.get("created_at")) for x in clients)
    clients_par_magasin = Counter(str(x.get("magasin_id")) for x in clients if x.get("magasin_id"))
    produits_par_magasin = Counter(str(p.get("magasin_id")) for p in produits if p.get("magasin_id"))
    ruptures_par_magasin = Counter(str(p.get("magasin_id")) for p in produits
                                   if p.get("magasin_id") and p.get("quantite") is not None and p["quantite"] <= 0)

    # --- Ventes ---------------------------------------------------------------
    ventes_kpi = {"courante": {"nombre": 0, "articles": 0}, "precedente": {"nombre": 0, "articles": 0}}
    par_magasin = defaultdict(lambda: {"ventes": 0, "ventes_prec": 0, "ca": 0.0, "ca_prec": 0.0, "articles": 0})
    statuts = Counter()
    methodes = Counter()
    for vente in ventes:
        p = periode(vente.get("created_at"))
        if not p:
            continue
        ventes_kpi[p]["nombre"] += 1
        ventes_kpi[p]["articles"] += _articles(vente)
        cumul = par_magasin[str(vente.get("magasin_id"))]
        if p == "courante":
            cumul["ventes"] += 1
            cumul["ca"] += _ca_ht(vente)
            cumul["articles"] += _articles(vente)
            statut = vente.get("statut_paiement")
            statuts[statut if statut in STATUTS_PAIEMENT else "non payé"] += 1
            paiements = [x for x in vente.get("paiements") or [] if isinstance(x, dict) and _num(x.get("montant")) > 0]
            if paiements:
                for paiement in paiements:
                    methodes[paiement.get("methode") or "cash"] += 1
            elif _num(vente.get("montant_paye")) > 0:
                methodes[vente.get("methode_paiement") or "cash"] += 1
        else:
            cumul["ventes_prec"] += 1
            cumul["ca_prec"] += _ca_ht(vente)

    # --- Connexions -------------------------------------------------------------
    connexions_kpi = {"courante": 0, "precedente": 0}
    actifs = {"courante": set(), "precedente": set()}
    for connexion in connexions:
        p = periode(connexion.get("timestamp"))
        if p:
            connexions_kpi[p] += 1
            if connexion.get("user_id"):
                actifs[p].add(str(connexion["user_id"]))

    # --- Croissance sur 12 mois (créations par mois) ---------------------------
    mois = [(debut_12_mois.date() + relativedelta(months=i)) for i in range(12)]
    cles_mois = {f"{m.year}-{m.month:02d}": i for i, m in enumerate(mois)}
    croissance = [{"mois": f"{m.year}-{m.month:02d}", "magasins": 0, "utilisateurs": 0, "clients": 0, "ventes": 0}
                  for m in mois]

    def ajouter_mois(horodatage, champ):
        local = _local(horodatage)
        if local:
            i = cles_mois.get(f"{local.year}-{local.month:02d}")
            if i is not None:
                croissance[i][champ] += 1

    for m in magasins:
        ajouter_mois(m.get("created_at"), "magasins")
    for u in users:
        ajouter_mois(u.get("created_at"), "utilisateurs")
    for x in clients:
        ajouter_mois(x.get("created_at"), "clients")
    for v in ventes:
        ajouter_mois(v.get("created_at"), "ventes")

    # --- Détail par magasin -----------------------------------------------------
    users_par_magasin = defaultdict(Counter)
    for u in users:
        if u.get("magasin_id") and user_actif(u):
            users_par_magasin[str(u["magasin_id"])][_role(u.get("role"))] += 1

    detail_magasins = []
    for magasin in magasins:
        statut = _statut_magasin(magasin)
        if statut == "archive":
            continue
        mid = str(magasin["_id"])
        devise = _devise(magasin)
        decimales = decimales_devise(devise)
        cumul = par_magasin.get(mid, {"ventes": 0, "ventes_prec": 0, "ca": 0.0, "ca_prec": 0.0, "articles": 0})
        dates = [d for d in (derniere_vente.get(mid), derniere_connexion.get(mid)) if d]
        derniere_activite = _local(max(dates)) if dates else None
        licence = magasin.get("licence") if isinstance(magasin.get("licence"), dict) else {}
        detail_magasins.append({
            "id": mid,
            "denomination": magasin.get("denomination") or "Magasin sans nom",
            "ville": magasin.get("ville") or "",
            "pays": magasin.get("pays") or "",
            "devise": devise,
            "statut": statut,
            "cree_le": _iso(magasin.get("created_at")),
            "ventes": cumul["ventes"],
            "ventes_prec": cumul["ventes_prec"],
            "chiffre_affaire": round(cumul["ca"], decimales),
            "chiffre_affaire_prec": round(cumul["ca_prec"], decimales),
            "articles": cumul["articles"],
            "clients": clients_par_magasin.get(mid, 0),
            "produits": produits_par_magasin.get(mid, 0),
            "produits_rupture": ruptures_par_magasin.get(mid, 0),
            "managers": users_par_magasin[mid].get("manager", 0),
            "gerants": users_par_magasin[mid].get("gerant", 0),
            "max_managers": licence.get("max_managers"),
            "max_gerants": licence.get("max_livreurs"),
            "caissiers": users_par_magasin[mid].get("caissier", 0),
            "max_caissiers": licence.get("max_caissiers", MAX_CAISSIERS_DEFAUT),
            "derniere_vente": _iso(derniere_vente.get(mid)),
            "derniere_activite": derniere_activite.isoformat() if derniere_activite else None,
            "jours_sans_activite": (aujourd_hui - derniere_activite.date()).days if derniere_activite else None,
        })
    detail_magasins.sort(key=lambda m: (m["statut"] != "actif", -m["ventes"], m["denomination"].lower()))

    return {
        "periode": {
            "jours": jours,
            "debut": debut.date().isoformat(),
            "fin": aujourd_hui.isoformat(),
            "debut_precedente": debut_prec.date().isoformat(),
        },
        "kpis": {
            "magasins": {
                "total": statuts_magasins["actif"] + statuts_magasins["desactive"],
                "actifs": statuts_magasins["actif"],
                "desactives": statuts_magasins["desactive"],
                "archives": statuts_magasins["archive"],
                "nouveaux": creations_magasins["courante"],
                "nouveaux_prec": creations_magasins["precedente"],
            },
            "utilisateurs": {
                "total": len(users),
                "actifs": sum(roles_actifs.values()),
                "inactifs": len(users) - sum(roles_actifs.values()),
                "nouveaux": creations_users["courante"],
                "nouveaux_prec": creations_users["precedente"],
            },
            "clients": {
                "total": len(clients),
                "nouveaux": creations_clients["courante"],
                "nouveaux_prec": creations_clients["precedente"],
            },
            "produits": {
                "total": len(produits),
                "en_rupture": sum(ruptures_par_magasin.values()),
            },
            "ventes": {
                "nombre": ventes_kpi["courante"]["nombre"],
                "nombre_prec": ventes_kpi["precedente"]["nombre"],
                "articles": ventes_kpi["courante"]["articles"],
                "articles_prec": ventes_kpi["precedente"]["articles"],
            },
            "connexions": {
                "nombre": connexions_kpi["courante"],
                "nombre_prec": connexions_kpi["precedente"],
                "utilisateurs_actifs": len(actifs["courante"]),
                "utilisateurs_actifs_prec": len(actifs["precedente"]),
            },
        },
        "roles": [
            {"role": role, "total": roles.get(role, 0), "actifs": roles_actifs.get(role, 0)}
            for role in ("admin", "manager", "gerant")
        ],
        "paiements": {
            "statuts": [{"statut": s, "nombre": statuts.get(s, 0)} for s in STATUTS_PAIEMENT],
            "methodes": [{"methode": m, "nombre": methodes.get(m, 0)}
                         for m in list(METHODES_PAIEMENT) + sorted(set(methodes) - set(METHODES_PAIEMENT))],
        },
        "croissance": croissance,
        "magasins": detail_magasins,
        "devises": sorted({m["devise"] for m in detail_magasins}),
    }


def ajouter_image_service(data, file):
    filename = secure_filename(file.filename)
    upload_path = current_app.config["STAFF_IMAGE_FOLDER"]
    os.makedirs(upload_path, exist_ok=True)

    path = os.path.join(upload_path, filename)
    file.save(path)

    # Enregistrement en base de données (optionnel mais recommandé)
    image_data = {
        "nom": data.get("nom", filename),
        "description": data.get("description", ""),
        "categorie": data.get("categorie", ""),
        "filename": filename,
        "tags": data.get("tags", []),
        "created_at": datetime.utcnow()
    }

    result = ImageStaffModel.ajouter_image(image_data)

    return {
        "message": "Image ajoutée avec succès.",
        "image_id": result,
        "filename": filename
    }, 201

    


def get_suggestions_service(nom=None, categorie=None, description=None, tags=None):
    images = ImageStaffModel.get_image_by_critere(nom, categorie, description, tags)
    
    # Retourner les images selon les dates de création les plus récentes    
    images.sort(key=lambda x: x.get("created_at", datetime.min), reverse=True) 
    
    return [{
        "_id": str(img["_id"]),
        "filename": f"{request.host_url}uploads/staff_images/{img['filename']}",
        "nom": img["nom"],
        "description": img.get("description", ""),
        "categorie": img.get("categorie", ""),
        "tags": img.get("tags", [])
    } for img in images]
