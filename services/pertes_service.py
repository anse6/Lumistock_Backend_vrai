from collections import defaultdict
from models.ristourne_model import RistourneModel
from models.client_model import ClientModel
from bson import ObjectId
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta


def get_week_date_range(year, week):
    """Retourne le lundi et le dimanche de la semaine ISO donnée."""
    first_day = date.fromisocalendar(year, week, 1)
    last_day = first_day + timedelta(days=6)
    return first_day, last_day

def get_pertes_par_periode_total(livreur_id, granularite="jour", page=0, magasin_id=None):

    
    if livreur_id:
        ristournes = RistourneModel.get_ristournes_by_livreur(livreur_id)
    else:
        ristournes = RistourneModel.get_all_ristournes(magasin_id)
        
    ristournes_par_periode = defaultdict(float)
    #print(f"Ristournes trouvées : {ristournes.count()}")
    #print("Ristourne :", ristournes)
    
    for r in ristournes:
        date_obj = r.get("created_at")
        if not date_obj:
            continue
        montant_initial = r.get("montant_initial", 0)
        montant_paye = r.get("montant_paye", 0)
        
        if granularite == "jour":
            key = date_obj.strftime("%Y-%m-%d")
        elif granularite == "semaine":
            iso_year, iso_week, _ = date_obj.isocalendar()
            key = f"{iso_year}-W{iso_week:02d}"
        elif granularite == "mois":
            key = f"{date_obj.year}-{date_obj.month:02d}"
        elif granularite == "annee":
            key = str(date_obj.year)
        else:
            raise ValueError("Granularité non supportée")

        ristournes_par_periode[key] += (montant_initial - montant_paye)

    now = date.today()
    periodes = []
    
    if granularite == "jour":
        current_monday = now - timedelta(days=now.weekday())
        start = current_monday + timedelta(weeks=page)  
        for i in range(7):
            day = start + timedelta(days=i)
            key = day.strftime("%Y-%m-%d")
            periodes.append((key, day, day ))
            
    elif granularite == "semaine":
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
        
    elif granularite == "mois":
        base = now.replace(day=1) + relativedelta(months=page * 4)
        for i in range(4):
            d = base + relativedelta(months=i)
            key = f"{d.year}-{d.month:02d}"
            start = d
            end = (d + relativedelta(months=1)) - timedelta(days=1)
            periodes.append((key, start, end))
    
    elif granularite == "annee":
        start_year = now.year + (page * 4)
        for i in range(4):
            y = start_year + i
            key = str(y)
            start = date(y, 1, 1)
            end = date(y, 12, 31)
            periodes.append((key, start, end))
            
    # Génération des résultats
    detail = []
    ristourne_total = 0.0
    for key, start, end in periodes:
        montant = ristournes_par_periode.get(key, 0.0)
        ristourne_total += montant
        entry = {
            "periode": key,
            "montant_total": round(montant, 2),
            "start": str(start),
            "fin": str(end)
        }
        if montant == 0 and end > now:
            entry["incomplet"] = True
        if granularite == "semaine":
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
        "valeur" : round(ristourne_total, 2),
        "variation_percent": round(variation, 2),
        "granularite": granularite,
        "page": page,
        "detail": detail,
    }