from datetime import datetime, timedelta

def get_date_range_and_format(granularite, page):
    """
    Retourne la plage de dates et le format d'affichage selon la granularité.
    Chaque page est une fenêtre temporelle en arrière.
    """
    now = datetime.utcnow()
    if granularite == "jour":
        end = now - timedelta(days=page)
        start = end.replace(hour=0, minute=0, second=0, microsecond=0)
        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        format_str = "%Y-%m-%d"

    elif granularite == "semaine":
        start = now - timedelta(weeks=page + 1)
        end = now - timedelta(weeks=page)
        start = start - timedelta(days=start.weekday())
        end = end - timedelta(days=end.weekday() - 6)
        format_str = "Semaine du %d/%m"

    elif granularite == "mois":
        month = (now.month - page - 1) % 12 + 1
        year = now.year - ((now.month - page - 1) // 12)
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
        else:
            end = datetime(year, month + 1, 1) - timedelta(seconds=1)
        format_str = "%Y-%m"

    elif granularite == "annee":
        year = now.year - page
        start = datetime(year, 1, 1)
        end = datetime(year, 12, 31, 23, 59, 59)
        format_str = "%Y"

    else:
        raise ValueError("Granularité non supportée")

    return {"start": start, "end": end}, format_str

from datetime import datetime, timedelta
import calendar

def generate_period_blocks(granularite, page):
    """
    Génère les périodes temporelles à afficher pour une granularité donnée (jour, semaine, mois, année)
    et une page donnée (pour la navigation paginée).
    Retourne une liste de dictionnaires avec start, end, label, et le format à utiliser pour strftime.
    """

    now = datetime.now()
    periods = []

    if granularite == "jour":
        # 7 jours, page * 7 vers le passé
        for i in range(7):
            day_offset = i + (page - 1) * 7
            day = now - timedelta(days=day_offset)
            start = datetime(day.year, day.month, day.day)
            end = start + timedelta(days=1)
            label = start.strftime("%d/%m")
            periods.append({"start": start, "end": end, "label": label})
        format_str = "%d/%m"

    elif granularite == "semaine":
        # 4 semaines
        current = now - timedelta(weeks=(page - 1) * 4)
        for i in range(4):
            week_start = current - timedelta(days=current.weekday() + i * 7)
            week_end = week_start + timedelta(days=7)
            label = f"S{week_start.isocalendar()[1]} {week_start.strftime('%b %Y')}"
            periods.append({"start": week_start, "end": week_end, "label": label})
        format_str = "S%W %b %Y"

    elif granularite == "mois":
        # 4 mois
        year = now.year
        month = now.month - (page - 1) * 4
        for _ in range(4):
            if month <= 0:
                month += 12
                year -= 1
            start = datetime(year, month, 1)
            end_month = month + 1
            end_year = year
            if end_month > 12:
                end_month = 1
                end_year += 1
            end = datetime(end_year, end_month, 1)
            label = start.strftime("%b %Y")
            periods.append({"start": start, "end": end, "label": label})
            month -= 1
        format_str = "%b %Y"

    elif granularite == "annee":
        # 4 années
        for i in range(4):
            y = now.year - (page - 1) * 4 - i
            start = datetime(y, 1, 1)
            end = datetime(y + 1, 1, 1)
            label = str(y)
            periods.append({"start": start, "end": end, "label": label})
        format_str = "%Y"

    else:
        raise ValueError("Granularité non prise en charge")

    return periods, format_str


from datetime import timedelta, datetime

def get_all_sub_periods(start_date, granularite):
    """
    Retourne une liste de datetime représentant les sous-périodes
    (7 jours, 4 semaines, 4 mois, 4 années)
    """
    periods = []
    if granularite == "jour":
        for i in range(7):
            periods.append(start_date + timedelta(days=i))
    elif granularite == "semaine":
        for i in range(4):
            periods.append(start_date + timedelta(weeks=i))
    elif granularite == "mois":
        for i in range(4):
            month = (start_date.month - 1 + i) % 12 + 1
            year = start_date.year + (start_date.month - 1 + i) // 12
            periods.append(datetime(year, month, 1))
    elif granularite == "annee":
        for i in range(4):
            periods.append(datetime(start_date.year + i, 1, 1))
    return periods
