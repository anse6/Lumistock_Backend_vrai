# scripts/construire_localites.py
"""Enregistre en local les villes et quartiers de plusieurs pays.

Source gratuite et sans clé : OpenStreetMap (licence ODbL), via les serveurs
publics Overpass. Pour chaque pays, deux requêtes :
  1. les localités (city, town, village) région par région ;
  2. les quartiers (suburb, neighbourhood, quarter, city_district, borough,
     plus les localités, hameaux et villages collés à une ville), eux aussi
     région par région.
Chaque quartier est rattaché à la ville indiquée dans ses étiquettes
(addr:city, is_in…), sinon à la ville ou bourg le plus proche de la même
région (un quartier de Dubaï ne part pas à Sharjah, plus proche).

Si les serveurs OpenStreetMap refusent (saturés), la seconde source gratuite
et sans clé est GeoNames (fichier complet du pays : villes, quartiers « PPLX »,
provinces, populations), téléchargé en une fois.

Le résultat va dans data/localites/<PAYS>.json, lu par routes/location_routes.py.
L'application ne dépend donc plus des serveurs gratuits pour ces pays.

Utilisation (depuis backend/) :
    venv/bin/python -m scripts.construire_localites CM CI SN GA AE CA
    venv/bin/python -m scripts.construire_localites --geonames CA   (GeoNames directement)
La variable LOCALITES_BRUT=<dossier> garde les réponses brutes d'OpenStreetMap
pour relancer la construction sans tout retélécharger.
"""
import csv
import datetime
import io
import json
import math
import os
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile

DOSSIER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "localites")
SERVEURS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
USER_AGENT = "Lumistock/1.0 (liste des villes et quartiers)"
TYPES_QUARTIER = ("suburb", "neighbourhood", "quarter", "city_district", "borough")
TYPES_PROCHES = ("locality", "hamlet", "village", "isolated_dwelling")
# Rayon de rattachement (km) selon la taille de la ville.
RAYON_QUARTIER = {"city": 25, "town": 8}
RAYON_METROPOLE = 45  # villes de plus d'un million d'habitants (Dubaï, Toronto…)
RAYON_PROCHE = {"city": 10, "town": 3}


def normaliser(nom):
    nom = unicodedata.normalize("NFKD", nom or "")
    return "".join(c for c in nom if not unicodedata.combining(c)).strip().lower()


def nom_lisible(tags):
    nom = tags.get("name:fr") or tags.get("name:en") or tags.get("name") or ""
    return nom.split(";")[0].strip()  # « Al Khabeesi;Al Khibeesi » : le premier


def autres_noms(tags, nom):
    """Noms anglais, local… (« Dubai » pour « Doubaï »), pour la recherche."""
    noms, vus = [], {normaliser(nom)}
    for cle in ("name", "name:en", "int_name", "name:fr", "alt_name", "old_name", "short_name"):
        for valeur in str(tags.get(cle, "")).split(";"):
            valeur = valeur.strip()
            # Écritures non latines (arabe, chinois…) : inutiles pour la saisie.
            if valeur and normaliser(valeur) not in vus and all(ord(c) < 0x250 for c in valeur):
                vus.add(normaliser(valeur))
                noms.append(valeur)
    return noms


def overpass(requete, nom_brut=None, tours=4):
    dossier_brut = os.environ.get("LOCALITES_BRUT")
    chemin_brut = os.path.join(dossier_brut, nom_brut + ".json") if dossier_brut and nom_brut else None
    if chemin_brut and os.path.exists(chemin_brut):
        with open(chemin_brut, encoding="utf-8") as fichier:
            return json.load(fichier)
    resultat = _overpass(requete, tours)
    if chemin_brut:
        os.makedirs(dossier_brut, exist_ok=True)
        with open(chemin_brut, "w", encoding="utf-8") as fichier:
            json.dump(resultat, fichier, ensure_ascii=False)
    return resultat


def _overpass(requete, tours):
    donnees = urllib.parse.urlencode({"data": requete}).encode()
    for tour in range(tours):
        for url in SERVEURS:
            try:
                req = urllib.request.Request(url, data=donnees, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=400) as reponse:
                    return json.load(reponse)
            except Exception as erreur:  # serveur saturé : suivant
                print(f"    {url.split('/')[2]} : {erreur}", flush=True)
        time.sleep(20 * (tour + 1))
    raise RuntimeError("aucun serveur Overpass n'a répondu")


def deja_brut(nom_brut):
    dossier_brut = os.environ.get("LOCALITES_BRUT")
    return bool(dossier_brut) and os.path.exists(os.path.join(dossier_brut, nom_brut + ".json"))


def par_region(pays, corps, nom_brut):
    """Exécute `corps` région par région (une requête chacune, plus légère :
    indispensable pour un grand pays comme le Canada). Chaque région est
    précédée d'un élément « area » portant son nom."""
    if deja_brut(nom_brut):  # ancien téléchargement en une seule requête
        return overpass(None, nom_brut)["elements"]
    regions = overpass(f"""[out:json][timeout:300];
area["ISO3166-1"="{pays}"][admin_level=2]->.pays;
rel(area.pays)["boundary"="administrative"]["admin_level"="4"];
out tags;""", f"{pays}_regions")["elements"]
    elements = []
    for i, region in enumerate(regions, 1):
        print(f"    région {i}/{len(regions)} : {nom_lisible(region.get('tags', {}))}", flush=True)
        elements += overpass(f"""[out:json][timeout:900];
rel({region["id"]});
map_to_area->.r;
.r out tags;
{corps}""", f"{nom_brut}_{region['id']}")["elements"]
    return elements


def position(element):
    if "lat" in element:
        return element["lat"], element["lon"]
    centre = element.get("center")
    return (centre["lat"], centre["lon"]) if centre else (None, None)


def distance_km(a_lat, a_lon, b_lat, b_lon):
    p = math.pi / 180
    h = (math.sin((b_lat - a_lat) * p / 2) ** 2
         + math.cos(a_lat * p) * math.cos(b_lat * p) * math.sin((b_lon - a_lon) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


def population(tags):
    try:
        return int(str(tags.get("population", "0")).replace(" ", "").replace(",", "").split(";")[0])
    except ValueError:
        return 0


def localites(pays):
    """Villes, bourgs et villages, avec leur région (niveau administratif 4)."""
    if deja_brut(f"{pays}_localites"):  # ancien téléchargement en une requête
        elements = overpass(None, f"{pays}_localites")["elements"]
    else:
        # Villes du pays entier d'abord (celles hors de toute région comprises).
        elements = overpass(f"""[out:json][timeout:600];
area["ISO3166-1"="{pays}"][admin_level=2]->.pays;
node(area.pays)["place"~"^(city|town)$"]["name"];
out qt;""", f"{pays}_villes")["elements"]
        elements += par_region(pays, """node(area.r)["place"~"^(city|town|village)$"]["name"];
out qt;""", f"{pays}_localites_region")
    villes, region = {}, None
    for element in elements:
        tags = element.get("tags", {})
        if element["type"] == "area":
            region = nom_lisible(tags) or None
            continue
        nom = nom_lisible(tags)
        if not nom:
            continue
        if element["id"] in villes:  # ville vue dans le premier passage : sa région
            villes[element["id"]]["region"] = villes[element["id"]]["region"] or region
            continue
        villes[element["id"]] = {
            "nom": nom,
            "autres_noms": autres_noms(tags, nom),
            "lat": round(element["lat"], 5),
            "lon": round(element["lon"], 5),
            "region": region,  # None au premier passage (pays entier)
            "type": tags["place"],
            "population": population(tags),
            "capitale": str(tags.get("capital", "")) in ("yes", "2"),
            "reconnue": bool(tags.get("wikidata") or tags.get("wikipedia")),
        }
    return list(villes.values())


def quartiers_bruts(pays):
    types = "|".join(TYPES_QUARTIER + TYPES_PROCHES)
    corps = f"""node(area.r)["place"~"^({types})$"]["name"];
out qt;
wr(area.r)["place"~"^({types})$"]["name"];
out tags center qt;"""
    elements, region = [], None
    for element in par_region(pays, corps, f"{pays}_quartiers_regions"):
        if element["type"] == "area":
            region = nom_lisible(element.get("tags", {})) or None
        else:
            element["region"] = region
            elements.append(element)
    return elements


def absorbee(v, metropoles):
    """Petite localité sans fiche Wikidata collée à une métropole (« Air men
    city » dans Yaoundé) : c'est un quartier de la métropole, pas une ville qui
    lui prendrait les siens. Pikine (Wikidata) reste une ville près de Dakar."""
    # Arrondissement GeoNames (PPLA3/4 : Plateau-Mont-Royal à Montréal) : absorbé
    # quelle que soit sa population.
    arrondissement = v.get("arrondissement")
    if not arrondissement and (v["population"] >= 50_000 or v["reconnue"]):
        return None
    for m in metropoles:
        if m is not v and (not v["region"] or v["region"] == m["region"]) \
                and distance_km(v["lat"], v["lon"], m["lat"], m["lon"]) <= (15 if arrondissement else 12):
            return m
    return None


def rattacher(villes, elements):
    metropoles = [v for v in villes if v["type"] == "city" and v["population"] >= 1_000_000]
    for v in villes:
        v["quartiers"] = set()
    parents = []
    for v in villes:
        if v["type"] not in RAYON_QUARTIER:
            continue
        m = absorbee(v, metropoles)
        if m:
            m["quartiers"].add(v["nom"])
        else:
            parents.append(v)
    par_nom = {}
    for v in parents:
        for n in [v["nom"], *v["autres_noms"]]:
            par_nom.setdefault(normaliser(n), []).append(v)
    # Grille de 0,5° pour ne comparer qu'aux villes voisines.
    grille = {}
    for v in parents:
        grille.setdefault((int(v["lat"] * 2), int(v["lon"] * 2)), []).append(v)
    for element in elements:
        tags = element.get("tags", {})
        nom, (lat, lon) = nom_lisible(tags), position(element)
        if not nom or lat is None:
            continue
        proche = tags.get("place") in TYPES_PROCHES
        rayons = RAYON_PROCHE if proche else RAYON_QUARTIER
        region = element.get("region")
        voisines = [
            v for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            for v in grille.get((int(lat * 2) + dx, int(lon * 2) + dy), [])
            if not region or not v["region"] or v["region"] == region
        ]
        # 1) Ville indiquée dans les étiquettes, si elle est assez proche.
        choix = None
        for cle in ("addr:city", "is_in:city", "is_in:town", "is_in"):
            for candidat in str(tags.get(cle, "")).split(","):
                for v in par_nom.get(normaliser(candidat), []):
                    if distance_km(lat, lon, v["lat"], v["lon"]) <= 2 * RAYON_QUARTIER[v["type"]]:
                        choix = v
            if choix:
                break
        # 2) Sinon la ville la plus proche ; les grandes villes comptent double,
        #    les métropoles triple (Outremont ne prend pas les quartiers de Montréal).
        if not choix:
            meilleur = None
            for v in voisines:
                d = distance_km(lat, lon, v["lat"], v["lon"])
                rayon = rayons[v["type"]]
                if not proche and v["population"] >= 1_000_000:
                    rayon = RAYON_METROPOLE
                if d > rayon:
                    continue
                score = d / (3 if v["population"] >= 1_000_000 else 2 if v["type"] == "city" else 1)
                if meilleur is None or score < meilleur:
                    meilleur, choix = score, v
        if choix and normaliser(nom) not in {normaliser(n) for n in [choix["nom"], *choix["autres_noms"]]}:
            choix["quartiers"].add(nom)


# --- Seconde source : GeoNames -------------------------------------------------
GEONAMES_URL = "https://download.geonames.org/export/dump/"
# Lieux disparus, historiques ou abandonnés : ni villes ni quartiers.
GEONAMES_EXCLUS = {"PPLH", "PPLQ", "PPLW", "PPLCH", "PPLR"}


def _geonames_fichier(nom):
    """Fichier GeoNames (gardé dans LOCALITES_BRUT s'il est défini)."""
    dossier = os.environ.get("LOCALITES_BRUT")
    chemin = os.path.join(dossier, "geonames_" + nom) if dossier else None
    if chemin and os.path.exists(chemin):
        with open(chemin, "rb") as fichier:
            return fichier.read()
    req = urllib.request.Request(GEONAMES_URL + nom, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as reponse:
        contenu = reponse.read()
    if chemin:
        os.makedirs(dossier, exist_ok=True)
        with open(chemin, "wb") as fichier:
            fichier.write(contenu)
    return contenu


def geonames(pays):
    """(villes, quartiers) au même format que la source OpenStreetMap."""
    regions = {}
    for ligne in _geonames_fichier("admin1CodesASCII.txt").decode("utf-8").splitlines():
        code, nom, *_ = ligne.split("\t")
        if code.startswith(pays + "."):
            regions[code.split(".", 1)[1]] = nom
    archive = zipfile.ZipFile(io.BytesIO(_geonames_fichier(f"{pays}.zip")))
    texte = archive.read(f"{pays}.txt").decode("utf-8")
    villes, elements = [], []
    for r in csv.reader(io.StringIO(texte), delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(r) < 15 or r[6] != "P" or r[7] in GEONAMES_EXCLUS:
            continue
        nom, code = r[1].strip(), r[7]
        lat, lon, pop = float(r[4]), float(r[5]), int(r[14] or 0)
        region = regions.get(r[10])
        if code == "PPLX":  # section d'une ville : un quartier
            elements.append({"tags": {"name": nom, "place": "suburb"}, "lat": lat, "lon": lon, "region": region})
            continue
        if code in ("PPLC", "PPLA") or pop >= 100_000:
            genre = "city"
        elif code in ("PPLA2", "PPLA3", "PPLA4") or pop >= 5_000:
            genre = "town"
        else:
            genre = "village"
        villes.append({
            "nom": nom, "autres_noms": [], "lat": round(lat, 5), "lon": round(lon, 5), "region": region,
            "type": genre, "population": pop, "capitale": code == "PPLC", "reconnue": pop > 0,
            "arrondissement": code in ("PPLA3", "PPLA4"),
        })
        if genre == "village":  # hameau collé à une ville : aussi un quartier possible
            elements.append({"tags": {"name": nom, "place": "hamlet"}, "lat": lat, "lon": lon, "region": region})
    return villes, elements


def construire(pays, source="osm"):
    print(f"{pays} : localités…", flush=True)
    if source == "osm":
        try:
            villes = localites(pays)
            print(f"{pays} : {len(villes)} localités ; quartiers…", flush=True)
            elements = quartiers_bruts(pays)
        except RuntimeError as erreur:
            print(f"{pays} : OpenStreetMap indisponible ({erreur}) : GeoNames", flush=True)
            source = "geonames"
    if source == "geonames":
        villes, elements = geonames(pays)
    else:
        # Les quartiers de GeoNames complètent ceux d'OpenStreetMap (Émirats :
        # près de 2 000). Leurs noms de régions diffèrent (« Dubai » /
        # « Doubaï ») : rattachement à la distance seule.
        try:
            supplement = [e for e in geonames(pays)[1] if e["tags"]["place"] == "suburb"]
        except Exception as erreur:
            print(f"{pays} : GeoNames indisponible ({erreur})", flush=True)
            supplement = []
        # Même lieu déjà connu d'OpenStreetMap à moins de 700 m (« Al Barāḩah » /
        # « Al Baraha ») : doublon translittéré, écarté.
        grille = {}
        for e in elements:
            lat, lon = position(e)
            if lat is not None:
                grille.setdefault((round(lat * 50), round(lon * 50)), []).append((lat, lon))
        for e in supplement:
            cellule = (round(e["lat"] * 50), round(e["lon"] * 50))
            proches = [p for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                       for p in grille.get((cellule[0] + dx, cellule[1] + dy), [])]
            if not any(distance_km(e["lat"], e["lon"], *p) < 0.7 for p in proches):
                e["region"] = None
                elements.append(e)
    print(f"{pays} : {len(elements)} quartiers et lieux-dits ; rattachement…", flush=True)
    rattacher(villes, elements)
    rang = {"city": 0, "town": 1, "village": 2}
    villes.sort(key=lambda v: (rang[v["type"]], not v["capitale"], -v["population"], normaliser(v["nom"])))
    # Une seule entrée par nom (la plus importante). Même nom à moins de 30 km
    # (« Abu Dhabi » / « Abou Dabi ») : même ville, quartiers fusionnés ;
    # plus loin : un homonyme (village d'une autre région), gardé à part.
    par_cle, resultat = {}, []
    for v in villes:
        cles = {normaliser(n) for n in [v["nom"], *v["autres_noms"]]}
        deja = next((par_cle[c] for c in cles if c in par_cle), None)
        if deja and distance_km(v["lat"], v["lon"], deja["lat"], deja["lon"]) < 30:
            deja["quartiers"] |= v["quartiers"]
            continue
        resultat.append(v)
        for c in cles:
            par_cle.setdefault(c, v)
    for v in resultat:
        v["quartiers"] = sorted(v["quartiers"], key=normaliser)
        if not v["autres_noms"]:
            del v["autres_noms"]
        if not v["capitale"]:
            del v["capitale"]
        del v["reconnue"]
        v.pop("arrondissement", None)
    os.makedirs(DOSSIER, exist_ok=True)
    chemin = os.path.join(DOSSIER, f"{pays}.json")
    with open(chemin + ".tmp", "w", encoding="utf-8") as fichier:
        json.dump({
            "pays": pays,
            "source": "© les contributeurs d'OpenStreetMap (ODbL)" if source == "osm"
                      else "GeoNames (CC BY 4.0, geonames.org)",
            "date": datetime.date.today().isoformat(),
            "villes": resultat,
        }, fichier, ensure_ascii=False, separators=(",", ":"))
    os.replace(chemin + ".tmp", chemin)
    avec = sum(1 for v in resultat if v["quartiers"])
    total = sum(len(v["quartiers"]) for v in resultat)
    print(f"{pays} : {len(resultat)} localités, {avec} avec quartiers, {total} quartiers → {chemin}", flush=True)


if __name__ == "__main__":
    arguments = sys.argv[1:]
    choix = "geonames" if "--geonames" in arguments else "osm"
    for code in [c.upper() for c in arguments if not c.startswith("--")] or ["CM"]:
        construire(code, choix)
