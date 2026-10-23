from models.client_model import ClientModel 
from models.livraison_model import LivraisonModel
from datetime import datetime, timedelta, date # Added date
from bson.objectid import ObjectId
from collections import defaultdict
from dateutil.relativedelta import relativedelta
from models.user_model import UserModel # Corrected import
from models.produit_model import ProduitModel # Needed for Product name
from models.charge_model import ChargeModel # Nouveau


def get_villes_livraison_total():
    """
    Retourne les zones (quartiers) de vente des 4 meilleures villes pour un livreur.
    Pour chaque ville : les quartiers avec montant total, quantité, nombre livraisons et pourcentages.
    """

    livraisons = LivraisonModel.get_all_livraisons()

    # Structure : villes → quartiers → stats
    villes_stats = defaultdict(lambda: defaultdict(lambda: {
        "quartier": "",
        "nombre_livraisons": 0,
        "quantite_totale": 0,
        "montant_total": 0
    }))

    villes_totaux = defaultdict(float)  # montant total par ville

    for livraison in livraisons:
        client = ClientModel.get_client_by_id(str(livraison["client_id"]))
        if not client:
            continue

        ville = client.get("ville", "Inconnue")
        quartier = client.get("quartier", "Inconnu")

        montant = livraison.get("montant_total", 0)
        quantite = livraison.get("quantite", 0)

        stats = villes_stats[ville][quartier]
        stats["quartier"] = quartier
        stats["nombre_livraisons"] += 1
        stats["quantite_totale"] += quantite
        stats["montant_total"] += montant

        villes_totaux[ville] += montant

    # Sélection des 4 meilleures villes en fonction du montant
    top_villes = sorted(villes_totaux.items(), key=lambda x: x[1], reverse=True)[:4]

    resultat = []

    for ville, montant_total_ville in top_villes:
        quartiers = list(villes_stats[ville].values())
        total_livraisons = sum(q["nombre_livraisons"] for q in quartiers)
        total_quantite = sum(q["quantite_totale"] for q in quartiers)

        for q in quartiers:
            q["pourcentage_livraisons"] = round((q["nombre_livraisons"] / total_livraisons) * 100, 2) if total_livraisons else 0
            q["pourcentage_quantite"] = round((q["quantite_totale"] / total_quantite) * 100, 2) if total_quantite else 0
            q["pourcentage_montant"] = round((q["montant_total"] / montant_total_ville) * 100, 2) if montant_total_ville else 0

        resultat.append({
            "ville": ville,
            "montant_total_ville": round(montant_total_ville, 2),
            "quartiers": sorted(quartiers, key=lambda x: x["montant_total"], reverse=True)
        })

    return resultat




"""
    Calcule le chiffre d’affaires total et l'évolution sur une periode.
    Le compare à la même période précédente.
    Retourne la valeur actuelle et la variation en pourcentage.
"""


def get_chiffre_affaires_total():
    """
    Retourne :
    - Le chiffre d'affaires total du livreur (toutes périodes),
    - La variation du chiffre d'affaires de cette semaine par rapport à la précédente.
    """
    today = datetime.now()

    # Semaine actuelle
    start_of_week = today - timedelta(days=today.weekday())  # Lundi
    start_date = datetime(start_of_week.year, start_of_week.month, start_of_week.day)
    end_date = today # exemple :  jeudi 9 mai 2025
    periode_duree = end_date - start_date # => 9 mai - 5 mai = 4 jours

    # Semaine précédente
    previous_start = start_date - periode_duree # exemple : # => 5 mai - 4 jours = 1er mai 2025
    previous_end = start_date # => 5 mai 2025

    # Chiffre d'affaires de la semaine actuelle
    current_livraisons = LivraisonModel.get_livraisons_between_dates(start_date, end_date)
    current_total = sum(l.get("montant_total", 0) for l in current_livraisons)

    # Chiffre d'affaires de la semaine précédente
    previous_livraisons = LivraisonModel.get_livraisons_between_dates(previous_start, previous_end)
    previous_total = sum(l.get("montant_total", 0) for l in previous_livraisons)

    # Calcul de la variation
    if previous_total == 0:
        variation = 100.0 if current_total > 0 else 0.0
    else:
        variation = ((current_total - previous_total) / previous_total) * 100

    # Chiffre d'affaires global (toutes périodes)
    all_livraisons = LivraisonModel.get_all_livraisons()
    total = sum(l.get("montant_total", 0) for l in all_livraisons)

    return {
        "valeur": round(total, 2),  # Chiffre d'affaires total toutes périodes
        "variation_percent": round(variation, 2),
        "periode": {
            "debut": start_date.isoformat(),
            "fin": end_date.isoformat()
        }
    }



def get_week_date_range(year, week):
    """Retourne le lundi et le dimanche de la semaine ISO donnée."""
    first_day = date.fromisocalendar(year, week, 1)
    last_day = first_day + timedelta(days=6)
    return first_day, last_day

def get_revenus_par_periode_total(livreur_id=None, granularity='jour', page=0):
    """
    Retourne le revenu total (tous les livreurs ou un seul),
    avec variation (%) entre la dernière période et la précédente,
    selon la granularité et une pagination par périodes.
    """
    if livreur_id is not None:
        livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)
    else:
        livraisons = LivraisonModel.get_all_livraisons()

    revenus_par_periode = defaultdict(float)

    for l in livraisons:
        date_obj = l.get("created_at")
        if not date_obj:
            continue
        montant = l.get("montant_total", 0)

        if granularity == "jour":
            key = date_obj.strftime("%Y-%m-%d")
        elif granularity == "semaine":
            iso_year, iso_week, _ = date_obj.isocalendar()
            key = f"{iso_year}-W{iso_week:02d}"
        elif granularity == "mois":
            key = f"{date_obj.year}-{date_obj.month:02d}"
        elif granularity == "annee":
            key = str(date_obj.year)
        else:
            raise ValueError("Granularité invalide : 'jour', 'semaine', 'mois', 'annee' attendue")

        revenus_par_periode[key] += montant

    now = date.today()
    periodes = []

    if granularity == "jour":
        current_monday = now - timedelta(days=now.weekday())
        start = current_monday + timedelta(weeks=page)
        for i in range(7):
            day = start + timedelta(days=i)
            key = day.strftime("%Y-%m-%d")
            periodes.append((key, day, day))

    elif granularity == "semaine":
        current_year, current_week, _ = now.isocalendar()
        start_week = current_week + (page * 4)
        start_year = current_year
        for i in range(4):
            week = start_week + i
            year = start_year
            while week > 52:
                week -= 52
                year += 1
            while week < 1:
                week += 52
                year -= 1
            start, end = get_week_date_range(year, week)
            key = f"{year}-W{week:02d}"
            periodes.append((key, start, end))

    elif granularity == "mois":
        base = now.replace(day=1) + relativedelta(months=page * 4)
        for i in range(4):
            d = base + relativedelta(months=i)
            key = f"{d.year}-{d.month:02d}"
            start = d
            end = (d + relativedelta(months=1)) - timedelta(days=1)
            periodes.append((key, start, end))

    elif granularity == "annee":
        start_year = now.year + (page * 4)
        for i in range(4):
            y = start_year + i
            key = str(y)
            start = date(y, 1, 1)
            end = date(y, 12, 31)
            periodes.append((key, start, end))

    # Génération des résultats
    detail = []
    revenu_total = 0.0
    for key, start, end in periodes:
        montant = revenus_par_periode.get(key, 0.0)
        revenu_total += montant
        entry = {
            "periode": key,
            "montant_total": round(montant, 2),
            "debut": str(start),
            "fin": str(end)
        }
        if montant == 0 and end > now:
            entry["incomplet"] = True
        if granularity == "semaine":
            entry["plage_dates"] = f"{start} au {end}"
        detail.append(entry)

    variation = 0.0
    if len(detail) >= 2:
        montant_precedent = detail[-2]["montant_total"]
        montant_courant = detail[-1]["montant_total"]
        if montant_precedent == 0:
            variation = 100.0 if montant_courant > 0 else 0.0
        else:
            variation = ((montant_courant - montant_precedent) / montant_precedent) * 100

    return {
        "valeur": round(revenu_total, 2),
        "variation_percent": round(variation, 2),
        "granularite": granularity,
        "page": page,
        "detail": detail
    }



# Statistiques de paiement par livreur

def get_statistiques_paiement_total():
    """
    Retourne la répartition des paiements faits par un livreur,
    sous forme de pourcentages.
    """
    livraisons = LivraisonModel.get_all_livraisons()
    repartition = defaultdict(int)

    for l in livraisons:
        statut = l.get("statut_paiement", "inconnu").lower()
        repartition[statut] += 1

    total = sum(repartition.values())
    if total == 0:
        return []

    return [
        {
            "mode_paiement": k,
            "pourcentage": round((v / total) * 100, 2),
            "nombre": v
        }
        for k, v in repartition.items()
    ]



# Nombre de livraison total 
def get_nombre_livraisons_total():
    """
    Retourne :
    - Le nombre total de livraisons effectuées (toutes périodes),
    - La variation par rapport à la semaine précédente.
    """
    today = datetime.now()

    # Semaine actuelle
    # today.weekday() = 0  retourne le jour de la semaine (0 pour lundi à 6 pour dimanche).
    start_of_week = today - timedelta(days=today.weekday())  # donne le lundi de la semaine actuelle.
    start_date = datetime(start_of_week.year, start_of_week.month, start_of_week.day) # Formalise ce lundi à minuit.
    end_date = today # est maintenant (l'instant courant).
    periode_duree = end_date - start_date # est la durée de la période actuelle (typiquement du lundi jusqu’à maintenant).

    # Semaine précédente
    previous_start = start_date - periode_duree
    previous_end = start_date

    # Livraisons de la semaine actuelle et précédente
    current_livraisons = LivraisonModel.get_livraisons_between_dates(start_date, end_date)
    previous_livraisons = LivraisonModel.get_livraisons_between_dates(previous_start, previous_end)

    current_total = len(current_livraisons)
    previous_total = len(previous_livraisons)

    if previous_total == 0:
        variation = 100.0 if current_total > 0 else 0.0
    else:
        variation = ((current_total - previous_total) / previous_total) * 100

    # Total global (toutes périodes)
    all_livraisons = LivraisonModel.get_all_livraisons()
    total = len(all_livraisons)

    return {
        "valeur": total,  # Livraisons totales
        "variation_percent": round(variation, 2),
        "periode": {
            "debut": start_date.isoformat(),
            "fin": end_date.isoformat()
        }
    }


def get_nombre_clients_total_livre():
    """
    Retourne :
    - Le nombre total de clients uniques servis (toutes périodes),
    - Le nombre total de livraisons effectuées,
    - La variation du nombre de clients uniques cette semaine par rapport à la précédente.
    """
    today = datetime.now()
    start_of_week = today - timedelta(days=today.weekday())
    start_date = datetime(start_of_week.year, start_of_week.month, start_of_week.day)
    end_date = today
    periode_duree = end_date - start_date
    previous_start = start_date - periode_duree
    previous_end = start_date

    # Livraisons sur la semaine actuelle et précédente
    current_livraisons = LivraisonModel.get_livraisons_between_dates(start_date, end_date)
    previous_livraisons = LivraisonModel.get_livraisons_between_dates(previous_start, previous_end)

    current_clients = {str(l["client_id"]) for l in current_livraisons}
    previous_clients = {str(l["client_id"]) for l in previous_livraisons}

    current_count = len(current_clients)
    previous_count = len(previous_clients)

    if previous_count == 0:
        variation = 100.0 if current_count > 0 else 0.0
    else:
        variation = ((current_count - previous_count) / previous_count) * 100

    # Clients uniques et livraisons totales sur toutes les périodes
    all_livraisons = LivraisonModel.get_all_livraisons()
    all_clients = {str(l["client_id"]) for l in all_livraisons}
    total_livraisons = len(all_livraisons)

    return {
        "valeur": len(all_clients),  # clients uniques (historique)
        "nombre_livraisons_total": total_livraisons,
        "variation_percent": round(variation, 2),
        "periode": {
            "debut": start_date.isoformat(),
            "fin": end_date.isoformat()
        }
    }


def get_nombre_produits_livres():
    """
    Retourne :
    - Le nombre total de produits uniques livrés dans tout le magasin (historique complet),
    - La quantité totale de produits livrés (historique),
    - Et la variation sur les produits uniques par rapport à la semaine précédente.
    """
    today = datetime.now()
    start_of_week = today - timedelta(days=today.weekday())
    start_date = datetime(start_of_week.year, start_of_week.month, start_of_week.day)
    end_date = today
    periode_duree = end_date - start_date
    previous_start = start_date - periode_duree
    previous_end = start_date

    # Toutes les livraisons du livreur (historique complet)
    all_livraisons = LivraisonModel.get_all_livraisons()

    # Calcul des produits uniques et quantité totale (historique)
    produits_uniques = set()
    quantite_totale = 0

    for l in all_livraisons:
        for p in l.get("produits", []):
            produits_uniques.add(str(p["produit_id"]))
            quantite_totale += p.get("quantite", 0)

    # Livraisons de la semaine actuelle
    current_livraisons = LivraisonModel.get_livraisons_between_dates(start_date, end_date)
    current_produits = set()
    for l in current_livraisons:
        current_produits.update(str(p["produit_id"]) for p in l.get("produits", []))

    # Livraisons de la semaine précédente
    previous_livraisons = LivraisonModel.get_livraisons_between_dates(previous_start, previous_end)
    previous_produits = set()
    for l in previous_livraisons:
        previous_produits.update(str(p["produit_id"]) for p in l.get("produits", []))

    current_count = len(current_produits)
    previous_count = len(previous_produits)

    if previous_count == 0:
        variation = 100.0 if current_count > 0 else 0.0
    else:
        variation = ((current_count - previous_count) / previous_count) * 100

    return {
        "valeur": len(produits_uniques),
        "quantite_totale": quantite_totale,
        "variation_percent": round(variation, 2),
        "periode": {
            "debut": start_date.isoformat(),
            "fin": end_date.isoformat()
        }
    }


def get_rapport_livraisons_dtailles(granularite="jour", date_ref=None, livreur_id=None):
    """
    Génère un rapport détaillé des livraisons :
    - Client, Livreur, Produit, Prix Unitaire, Quantité, Prix Total.
    - Filtré par jour, semaine, mois ou année.
    - Optionnellement filtré par livreur.
    """
    if date_ref is None:
        today = date.today()
    else:
        try:
            today = datetime.strptime(date_ref, "%Y-%m-%d").date()
        except ValueError:
             today = date.today()

    start_date = None
    end_date = None

    # 1. Détermination de la plage de dates
    if granularite == "jour":
        start_date = datetime(today.year, today.month, today.day)
        end_date = start_date + timedelta(days=1) - timedelta(seconds=1) # Fin de journée 23:59:59
    
    elif granularite == "semaine":
        start_of_week = today - timedelta(days=today.weekday())
        start_date = datetime(start_of_week.year, start_of_week.month, start_of_week.day)
        end_date = start_date + timedelta(days=7) - timedelta(seconds=1)

    elif granularite == "mois":
        start_date = datetime(today.year, today.month, 1)
        next_month = start_date + relativedelta(months=1)
        end_date = next_month - timedelta(seconds=1)

    elif granularite == "annee":
        start_date = datetime(today.year, 1, 1)
        end_date = datetime(today.year, 12, 31, 23, 59, 59)
    
    else:
        # Par défaut jour
        start_date = datetime(today.year, today.month, today.day)
        end_date = start_date + timedelta(days=1) - timedelta(seconds=1)

    # 2. Récupération des livraisons
    livraisons = LivraisonModel.get_livraisons_between_dates(start_date, end_date, livreur_id=livreur_id)
    
    # 3. Collecte des IDs pour fetch en masse (Optimisation)
    client_ids = set()
    livreur_ids = set()
    manager_ids = set()
    produit_ids = set()

    for l in livraisons:
        if l.get("client_id"): client_ids.add(l["client_id"])
        if l.get("livreur_id"): livreur_ids.add(l["livreur_id"])
        if l.get("manager_id"): manager_ids.add(l["manager_id"])
        for p in l.get("produits", []):
            if p.get("produit_id"): produit_ids.add(ObjectId(p["produit_id"]))

    # 4. Fetch des entités
    # Note: On suppose que les models ont des méthodes pour get_many ou on boucle (si pas optimisé dans le model)
    # Pour l'instant on boucle car pas de get_many_by_ids explicite visible, mais on pourrait l'ajouter.
    # Pour faire simple et robuste sans toucher trop de fichiers :
    
    clients_map = {}
    for cid in client_ids:
        c = ClientModel.get_client_by_id(str(cid))
        if c:
            # Robust resolution for client
            name = c.get('nom') or c.get('name') or c.get('username') or "Inconnu"
            clients_map[str(cid)] = str(name).strip()

    users_map = {}
    from models.user_model import UserModel # Import local si besoin pour éviter cycles
    # On combine les IDs de livreurs et managers
    all_user_ids = livreur_ids.union(manager_ids)
    for uid in all_user_ids:
        u = UserModel.find_by_id(str(uid))
        if u:
            # Robust resolution for users (admin, manager, livreur)
            nom = u.get('name') or u.get('nom') or u.get('last_name') or ""
            prenom = u.get('first_name') or u.get('prenom') or ""
            username = u.get('username') or u.get('email') or ""
            
            fullname = f"{nom} {prenom}".strip()
            if not fullname:
                fullname = username
            
            # Final fallback to ID if everything else is empty
            if not fullname:
                fullname = str(uid)
                
            users_map[str(uid)] = str(fullname).strip()
        
    produits_map = {}
    for pid in produit_ids:
        p = ProduitModel.get_by_id(str(pid))
        if p: produits_map[str(pid)] = p.get("nom", "Inconnu")

    # 5. Construction du rapport plat
    rapport = []
    
    for l in livraisons:
        client_nom = clients_map.get(str(l.get("client_id")), "Inconnu")
        
        # Le livreur peut être l'ID livreur ou l'ID manager (si créé par un manager)
        livreur_id = l.get("livreur_id")
        manager_id = l.get("manager_id")
        
        # Résolution avec fallbacks successifs
        livreur_nom = users_map.get(str(livreur_id)) if livreur_id else None
        
        if not livreur_nom and manager_id:
            livreur_nom = users_map.get(str(manager_id))
            
        if not livreur_nom:
            # Si on a un ID mais pas de nom résolu, on met l'ID ou Inconnu
            if livreur_id: livreur_nom = f"ID: {str(livreur_id)}"
            elif manager_id: livreur_nom = f"ID: {str(manager_id)}"
            else: livreur_nom = "Inconnu"
        statut_pay = l.get("statut_paiement", "inconnu")
        date_liv = l.get("date_livraison").strftime("%Y-%m-%d %H:%M") if l.get("date_livraison") else ""
        
        for p in l.get("produits", []):
            pid = str(p.get("produit_id"))
            p_nom = produits_map.get(pid, "Produit supprimé")
            qte = int(p.get("quantite", 0))
            prix_u = float(p.get("prix", 0))
            prix_tot = qte * prix_u
            
            rapport.append({
                "date": date_liv,
                "client": client_nom,
                "livreur": livreur_nom,
                "produit": p_nom,
                "quantite": qte,
                "prix_unitaire": prix_u,
                "prix_total": prix_tot,
                "statut_paiement": statut_pay
            })
            
    return {
        "periode": {
            "start": start_date.strftime("%Y-%m-%d"),
            "end": end_date.strftime("%Y-%m-%d")
        },
        "total_lignes": len(rapport),
        "data": rapport
    }


def get_evolution_risque(magasin_id, year, livreur_id=None):
    """
    Analyse avancée de l'évolution du risque (Financier & Opérationnel).
    Retourne une analyse mensuelle détaillée : CA, Dettes, Valeur des Retours, 
    Valeur des Ristournes, et Top Clients à risque.
    """
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", 
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    stats_by_month = {}
    for i in range(1, 13):
        stats_by_month[i] = {
            "revenue": 0.0,
            "unpaid": 0.0,
            "returns_value": 0.0,
            "ristournes_value": 0.0,
            "returns_count": 0,
            "ristournes_count": 0,
            "clients_debt": defaultdict(float)
        }
    
    start_date = datetime(year, 1, 1)
    end_date = datetime(year, 12, 31, 23, 59, 59)
    
    # 1. Analyse des Livraisons (CA, Dettes, Retours, Ristournes directs)
    livraisons = LivraisonModel.get_livraisons_between_dates(start_date, end_date, magasin_id=magasin_id, livreur_id=livreur_id)
    
    for l in livraisons:
        date_obj = l.get("date_livraison") or l.get("created_at")
        if not date_obj: continue
        
        m = date_obj.month
        ca = float(l.get("montant_total", 0))
        paye = float(l.get("montant_paye", 0))
        dette = max(0, ca - paye)
        # Valeur retournée ; « retour » (reste à rembourser) pour les anciennes ventes.
        ret_val = float(l.get("montant_retourne") or l.get("retour", 0.0) or 0)
        rist_val = float(l.get("ristourne", 0.0))
        
        stats_by_month[m]["revenue"] += ca
        stats_by_month[m]["unpaid"] += dette
        stats_by_month[m]["returns_value"] += ret_val
        stats_by_month[m]["ristournes_value"] += rist_val
        
        if ret_val > 0: stats_by_month[m]["returns_count"] += 1
        if rist_val > 0: stats_by_month[m]["ristournes_count"] += 1
        
        if dette > 0 and l.get("client_id"):
            stats_by_month[m]["clients_debt"][str(l["client_id"])] += dette

    # Formatter la réponse
    evolution = []
    accumulated_debt = 0.0
    
    for i, name in enumerate(months, 1):
        m_s = stats_by_month[i]
        ca = m_s["revenue"]
        debt = m_s["unpaid"]
        accumulated_debt += debt
        
        risk_ratio = round((debt / ca * 100), 2) if ca > 0 else 0.0
        
        # Niveau de risque
        health_status = "Sain"
        if risk_ratio > 30: health_status = "Critique"
        elif risk_ratio > 15: health_status = "Sous Surveillance"
        
        # Top 3 débiteurs du mois
        top_clients = []
        sorted_debts = sorted(m_s["clients_debt"].items(), key=lambda x: x[1], reverse=True)[:3]
        for cid, amt in sorted_debts:
            client = ClientModel.get_client_by_id(cid)
            name_c = client.get("nom") or client.get("name") or "Inconnu" if client else "Inconnu"
            top_clients.append({"name": name_c, "debt": round(amt, 2)})

        evolution.append({
            "month": name,
            "revenue": round(ca, 2),
            "unpaid": round(debt, 2),
            "risk_ratio": risk_ratio,
            "health_status": health_status,
            "loss_details": {
                "returns_value": round(m_s["returns_value"], 2),
                "ristournes_value": round(m_s["ristournes_value"], 2),
                "returns_count": m_s["returns_count"]
            },
            "top_debtors": top_clients,
            "cumulative_debt": round(accumulated_debt, 2)
        })

    return {
        "year": year,
        "evolution": evolution,
        "global_stats": {
            "total_revenue": round(sum(e["revenue"] for e in evolution), 2),
            "total_unpaid": round(accumulated_debt, 2),
            "total_returns_loss": round(sum(e["loss_details"]["returns_value"] for e in evolution), 2),
            "global_risk_ratio": round((accumulated_debt / sum(e["revenue"] for e in evolution) * 100), 2) if sum(e["revenue"] for e in evolution) > 0 else 0.0
        }
    }

def get_benefice_net_total():
    """Bénéfice net depuis le début, limité au magasin de l'utilisateur connecté.

    Même calcul que la page Finance (CA hors TVA et hors dette reportée,
    pertes, amortissements) : voir services.compta_service.
    """
    from services.compta_service import resume_global
    from utils.inject_magasin_id import inject_magasin_id
    return resume_global(inject_magasin_id({})["magasin_id"])
