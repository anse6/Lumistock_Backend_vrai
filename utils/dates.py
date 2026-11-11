from datetime import datetime, timedelta

def get_week_date_range(year: int, week: int):
    """
    Retourne la plage de dates (début, fin) d'une semaine ISO donnée.
    - year : année
    - week : numéro de la semaine ISO (1-53)
    
    Retourne deux chaînes de caractères au format 'YYYY-MM-DD' : (début, fin)
    """
    # Le lundi de la première semaine ISO est le premier jour de la semaine ISO
    first_day_of_year = datetime(year, 1, 4)  # Le 4 janvier est toujours dans la semaine 1
    first_monday = first_day_of_year - timedelta(days=first_day_of_year.isoweekday() - 1)

    start_of_week = first_monday + timedelta(weeks=week - 1)
    end_of_week = start_of_week + timedelta(days=6)

    return start_of_week.strftime("%Y-%m-%d"), end_of_week.strftime("%Y-%m-%d")


def iso_utc(valeur):
    """Horodatage enregistré en UTC -> texte ISO qui le dit (« …Z »).

    Sans ce « Z », le navigateur lit l'heure comme une heure locale : au
    Cameroun (UTC+1) tout paraît décalé d'une heure et une opération faite
    entre minuit et 1 h tombe la veille. Accepte un datetime (naïf = UTC) ou un
    texte ISO déjà enregistré ; une simple date (sans heure) reste inchangée.
    """
    if isinstance(valeur, datetime):
        return valeur.isoformat() + ("Z" if valeur.tzinfo is None else "")
    if isinstance(valeur, str) and "T" in valeur:
        heure = valeur.split("T", 1)[1]
        if not (heure.endswith("Z") or "+" in heure or "-" in heure):
            return valeur + "Z"
    return valeur


def utc_naif(valeur):
    """Inverse de iso_utc pour un texte : « …Z » ou « …+00:00 » -> ISO UTC sans
    fuseau, le format enregistré en base."""
    if isinstance(valeur, str) and "T" in valeur:
        if valeur.endswith("Z"):
            return valeur[:-1]
        if valeur.endswith("+00:00"):
            return valeur[:-6]
    return valeur
