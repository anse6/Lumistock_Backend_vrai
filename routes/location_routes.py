# routes/location_routes.py
import gzip
import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

from flask import Blueprint, Response, request, jsonify, current_app

location_bp = Blueprint("location_routes", __name__, url_prefix="/locations")

# Base de données étendue des villes et quartiers d'Afrique
CITY_DATA = {
    "CM": {
        "Douala": [
            "Akwa", "Bali", "Bassa", "Bessengue", "Bonabéri", "Bonamouti",
            "Bonamoussadi", "Bonanjo", "Bonapriso", "Deïdo", "Japoma", "Kotto",
            "Logbessou", "Logpom", "Makepe", "Ndogbong", "Ndogpassi", "New Bell",
            "Nyalla", "Yassa", "Bonabenga", "Cite SIC", "Ndog-Bong", "Ndog-Passi I",
            "Ndog-Passi II", "Ndog-Passi III", "Village", "Sodiko", "Soboum",
            "Mbangue", "Nkongmondo", "Mambanda", "Ndoghem", "Bonadale", "Bonaloka",
            "Cité des palmiers", "Pk7", "Pk8", "Pk10", "Pk12", "Pk14", "Pk17",
            "Pk19", "Pk21", "Mboppi", "Bépanda", "Cité Cicam", "Bassa Zone Industrielle"
        ],
        "Yaoundé": [
            "Ahala", "Bastos", "Biyem-Assi", "Dragage", "Ekounou", "Elig-Dzoa",
            "Elig-Essono", "Emana", "Etoa-Méki", "Étoudi", "Melen", "Mendong",
            "Messassi", "Ngoa-Ekellé", "Nkolbisson", "Nkolondon", "Nlongkak",
            "Odza", "Olembé", "Quartier du Golf", "Tsinga", "Mvog-Ada", "Mvog-Atangana Mballa",
            "Mvog-Mbi", "Mimboman", "Mfandena", "Nkomo", "Kondengui",
            "Madagascar", "Essos", "Nsam", "Biyem-Assi Carrefour", "Elig-Effa",
            "Cité Verte", "Damas", "Jouvence", "Manguiers", "Centre ville",
            "Quartier Militaire", "Messa", "Obili", "Carrière", "Nkolmesseng",
            "Nkoldongo", "Simbock", "Bane", "Nkolbisson", "Ekié", "Nkol-Eton",
            "Awae", "Nkolbewa", "Mbala II", "Oyomabang", "Efoulan", "Ngousso"
        ],
        "Bafoussam": [
            "Banengo", "Djeleng", "Tamlo", "Toungang", "Ndiangdam", "Koptchou",
            "Kouoptamo", "Tougang", "Kamkop", "Nitoukou", "Famla", "Kapkap",
            "Nkouoptchuo", "Souza", "Kwa", "Djemba", "Nkong", "Tamdja",
            "Tougang Pk1", "Tougang Pk2", "Centre ville", "Marché B"
        ],
        "Garoua": [
            "Djamboutou", "Roumdé Adjia", "Laindé", "Nyelvé", "Ngalbidjé",
            "Lopéré", "Foulbéré", "Poumpoumré", "Boklé", "Bibemi", "Ouro Labbo",
            "Mosquée", "Marché", "Administratif", "Boukarou", "Kouang"
        ],
        "Maroua": [
            "Dugoy", "Kakataré", "Founangué", "Pitoare", "Domayo",
            "Dougoy", "Zokok", "Doualaré", "Payataré", "Lopéré", "Kourgui",
            "Ouro-Tchédé", "Garin Walde", "Mouktaré", "Djoulgouf", "Kodek"
        ],
        "Bamenda": [
            "Commercial Avenue", "Up Station", "Nkwen", "Mankon", "Mile 2",
            "Nkunghi", "Old Town", "Small Mankon", "Ntarikon", "Mile 4",
            "Abangoh", "Cowbel", "Nchang", "Buea Road", "Hospital Roundabout"
        ],
        "Kribi": [
            "Mboamanga", "Bwambé", "Mpangou", "Nziou", "Talla",
            "Nlomé", "Angoundou", "Nlondé", "Balue", "Massock"
        ],
        "Ngaoundéré": [
            "Baladji", "Dang", "Gadamabanga", "Joli Soir", "Lahoré",
            "Mbideng", "Moungel", "Ngalewa", "Ouro-Nabé", "Sabongari", "Wouro-Kessoum"
        ],
        "Bertoua": [
            "Haoussa", "Mokolo", "Mansa", "Nkolbikon", "Régie",
            "Djamboutou", "Administration", "Marché", "Mokolo II"
        ],
        "Ebolowa": [
            "Angalé", "Bindoumba", "Maképé", "Nkolndongo", "Nkoulouloun",
            "Afan Mabe", "Ambam", "Mvangan", "Zoétélé"
        ],
        "Kumba": [
            "Fiango", "Kake", "Kumba I", "Kumba II", "Kumba III",
            "Marché Central", "Mile 1", "Mbonge Road"
        ],
        "Buea": [
            "Bonduma", "Buea Town", "Clerk's Quarter", "GRA", "Great Soppo",
            "Mile 16", "Molyko", "Muea", "Small Soppo", "Tole"
        ],
        "Limbe": [
            "Bota", "Churchill Avenue", "Down Beach", "GRA", "Lumpsum",
            "Mile 1", "Mile 2", "Mile 4", "New Town", "Old Town"
        ]
    },
    "SN": {
        "Dakar": [
            "Almadies", "Bel Air", "Castors", "Dieuppeul", "Fann", "Gueule Tapée",
            "Grand Dakar", "Grand Yoff", "Guediawaye", "HLM", "Liberté",
            "Médina", "Mermoz", "Ngor", "Ouakam", "Parcelles Assainies",
            "Pikine", "Plateau", "Point E", "Sacré Coeur", "Yoff"
        ],
        "Saint-Louis": [
            "Île nord", "Île sud", "Sor", "Pikine", "Khor", "Ndar Toute",
            "Guet Ndar", "Léona", "Lodo", "Ndioloffèn"
        ],
        "Thiès": [
            "Mbour", "Thiès Nord", "Thiès Ouest", "Thiès Est",
            "Liberté", "Médina Fall", "Randoulène", "Ngathie Naoudé"
        ],
        "Ziguinchor": [
            "Boucotte", "Diabir", "Néma", "Tilène", "Kandialang",
            "Lyndiane", "Lomen", "Santhiaba"
        ]
    },
    "CI": {
        "Abidjan": [
            "Abobo", "Adjamé", "Attécoubé", "Cocody", "Koumassi",
            "Marcory", "Plateau", "Port-Bouët", "Treichville", "Yopougon",
            "Riviera", "Deux Plateaux", "Bingerville", "Anyama"
        ],
        "Bouaké": [
            "Air France", "Belleville", "Commerce", "Koko", "N'Gattakro",
            "Nimbo", "Sokoura", "Tolakouadiokro"
        ],
        "Daloa": [
            "Commerce", "Lobia", "Orly", "Tazibouo", "Petit Paris", "Abidjanaise"
        ],
        "Yamoussoukro": [
            "Assabou", "Dioulakro", "Féticherie", "Habitat", "Morofé", "N'Gokro"
        ],
        "Korhogo": [
            "Banaforo", "Calao", "Koko", "Millionnaire", "Nioungue", "Soba"
        ]
    },
    "GH": {
        "Accra": [
            "Achimota", "Adabraka", "Airport Residential", "Asylum Down",
            "Cantonments", "Dansoman", "East Legon", "Labadi",
            "Labone", "Madina", "North Labone", "Nungua", "Osu",
            "Spintex", "Tema", "Tesano", "Teshie", "Tema Community"
        ],
        "Kumasi": [
            "Adum", "Bantama", "Dichemso", "Danyame", "Fante New Town",
            "Kaase", "Kwadaso", "Mampong Road", "Nhyiaeso", "Suame"
        ],
        "Tamale": [
            "Choggu", "Kukuo", "Moshie Zongo", "Nawuni", "Sagnarigu"
        ]
    },
    "NG": {
        "Lagos": [
            "Agege", "Ajah", "Alimosho", "Apapa", "Badagry", "Epe",
            "Eti-Osa", "Festac Town", "Gbagada", "Ibeju-Lekki",
            "Ifako-Ijaiye", "Ikeja", "Ikotun", "Ikorodu", "Island",
            "Isolo", "Kosofe", "Lagos Island", "Lekki", "Maryland",
            "Mushin", "Ojodu", "Ojo", "Orile", "Shomolu",
            "Surulere", "Victoria Island", "Yaba"
        ],
        "Abuja": [
            "Asokoro", "Central Business District", "Garki", "Gwarinpa",
            "Jabi", "Katampe", "Kubwa", "Maitama", "Nyanya", "Utako", "Wuse"
        ],
        "Kano": [
            "Dala", "Fagge", "Gwale", "Kano Municipal", "Nasarawa",
            "Tarauni", "Ungogo"
        ],
        "Port Harcourt": [
            "D-Line", "GRA Phase 1", "GRA Phase 2", "Rumuola", "Rumuosi",
            "Trans Amadi", "Woji"
        ]
    },
    "ML": {
        "Bamako": [
            "ACI 2000", "Badalabougou", "Bacodjicoroni", "Banconi", "Boulkassoumbougou",
            "Djélibougou", "Hippodrome", "Kalaban-Coro", "Korofina", "Lafiabougou",
            "Medina Coura", "Magnambougou", "Niarela", "Point G", "Quinzambougou",
            "Sabalibougou", "Sébenicoro", "Sikoro", "Sotuba", "Titibougou"
        ],
        "Sikasso": ["Wayérema", "Médine", "Zangasso", "Kléla", "Niéna"],
        "Ségou": ["Bougouwere", "Farakoloni", "Hamdallaye", "Pelengana"]
    },
    "BF": {
        "Ouagadougou": [
            "Balkuy", "Bilbalogho", "Cissin", "Dassasgo", "Gounghin",
            "Hamdalaye", "Karpala", "Kossodo", "Koulouba", "Kuinima",
            "Ouidi", "Patte d'Oie", "Pissy", "Samandin", "Secteur 28",
            "Tanghin", "Tampouy", "Toecin", "Wemtenga", "Zogona", "Zongo"
        ],
        "Bobo-Dioulasso": [
            "Accart-ville", "Artisanat", "Bolomakoté", "Dar Salam",
            "Dioulassoba", "Guimbi", "Koko", "Koura", "Sarfalao", "Tounouma"
        ]
    },
    "TG": {
        "Lomé": [
            "Adidogomé", "Agbalépédogan", "Agbavé", "Agoè", "Akodésséwa",
            "Anfamé", "Bè", "Cacavéli", "Dékon", "Hédzranawoe",
            "Kégué", "Kodjoviakopé", "Nyékonakpoè", "Tokoin", "Wuiti", "Zanguéra"
        ],
        "Sokodé": ["Arafat", "Kadjalla", "Komah", "Sadola"],
        "Kara": ["Agbandé", "Kpangalam", "Préfecture", "Tomdè"]
    },
    "BJ": {
        "Cotonou": [
            "Aibatin", "Akpakpa", "Cadjèhoun", "Dandji", "Dowa",
            "Fifadji", "Gbégamey", "Gbeto", "Houéyiho", "Jéricho",
            "Literacy", "Midombo", "Sainte Rita", "Sikècodji", "Vèdoko", "Wologuédé"
        ],
        "Porto-Novo": ["Ague", "Honvo", "Houégbo", "Ouando", "Toffo"],
        "Parakou": ["Banikanni", "Cotonou Hôtel", "Gah", "Guéma", "Madécali"]
    },
    "GA": {
        "Libreville": [
            "Akanda", "Alibandeng", "Angondjé", "Avorbam", "Batterie IV",
            "Derrière l'Hôpital", "Cocotiers", "Glass", "IAI", "Lalala",
            "Londji", "Louis", "Mont-Bouët", "Nkembo", "Nombakélé",
            "Owendo", "PK5", "PK6", "PK7", "PK8", "PK9", "PK12",
            "Plaine Orety", "Quartier Louis", "Sotega"
        ],
        "Port-Gentil": ["Awendjé", "Balise", "Bac", "Cité, Eni", "Grand Village", "Mandji"]
    },
    "CG": {
        "Brazzaville": [
            "Bacongo", "Djiri", "Kintelé", "Madibou", "Makélékélé",
            "Moungali", "Mpila", "Ouenzé", "Poto-Poto", "Talangaï", "Mfilou"
        ],
        "Pointe-Noire": ["Centre Ville", "Loandjili", "Lumumba", "Ngoyo", "Tié-Tié"]
    },
    "CD": {
        "Kinshasa": [
            "Barumbu", "Bumbu", "Gombe", "Kalamu", "Kasa-Vubu",
            "Kimbanseke", "Kinshasa", "Kisenso", "Lemba", "Limete",
            "Lingwala", "Makala", "Maluku", "Masina", "Mont Ngafula",
            "Ndjili", "Ngaba", "Ngaliema", "Ngiri-Ngiri", "Nsele",
            "Selembao", "Kintambo", "Bandalungwa"
        ],
        "Lubumbashi": ["Annexe", "Bonheur", "Golf", "Katuba", "Kenya", "Kampemba", "Ruashi"]
    },
    "TZ": {
        "Dar es Salaam": [
            "Ilala", "Kariakoo", "Kinondoni", "Masaki", "Mbagala",
            "Mikocheni", "Msasani", "Mwananyamala", "Temeke", "Upanga"
        ],
        "Mwanza": ["Ilemela", "Nyamagana", "Kirumba", "Seke"],
        "Arusha": ["Kaloleni", "Kijenge", "Lemara", "Sokoni", "Themi"]
    },
    "KE": {
        "Nairobi": [
            "Eastleigh", "Embakasi", "Gigiri", "Hurlingham", "Kamukunji",
            "Karen", "Kileleshwa", "Kilimani", "Langata", "Lavington",
            "Mathare", "Milimani", "Parklands", "Roysambu", "Ruaraka",
            "South C", "South B", "Spring Valley", "Umoja", "Westlands"
        ],
        "Mombasa": ["Bamburi", "Ganjoni", "Island", "Kisauni", "Likoni", "Nyali", "Port Reitz"],
        "Kisumu": ["Kondele", "Milimani", "Nyalenda", "Obunga", "Shaurimoyo"]
    },
    "ZA": {
        "Johannesburg": [
            "Alexandra", "Boksburg", "Braamfontein", "Edenvale", "Fordsburg",
            "Germiston", "Hillbrow", "Jeppe", "Kempton Park", "Langlaagte",
            "Meadowlands", "Midrand", "Newtown", "Northcliff", "Orlando",
            "Randburg", "Sandton", "Soweto", "Yeoville"
        ],
        "Cape Town": [
            "Athlone", "Bellville", "Bo-Kaap", "Camps Bay", "Century City",
            "De Waterkant", "Goodwood", "Green Point", "Kalk Bay",
            "Mitchells Plain", "Observatory", "Sea Point", "Somerset West",
            "Stellenbosch", "Table View"
        ],
        "Durban": ["Bluff", "Glenwood", "Pinetown", "Umlazi", "Umbilo", "Berea"]
    },
    "MA": {
        "Casablanca": [
            "Ain Chock", "Ain Diab", "Ain Sebaa", "Anfa", "Ben M'Sick",
            "Bourgogne", "California", "Centre Ville", "Derb Sultan", "El Fida",
            "Hay Hassani", "Hay Mohammadi", "Maarif", "Médina", "Mohammédia",
            "Oasis", "Racine", "Sidi Bernoussi", "Sidi Moumen", "Sidi Othmane"
        ],
        "Rabat": [
            "Agdal", "Hassan", "Hay Riad", "L'Océan", "Médina", "Souika",
            "Temara", "Youssoufia"
        ],
        "Marrakech": [
            "Guéliz", "Hivernage", "Médina", "Mellah", "Ménara", "Semlalia", "Sidi Youssef Ben Ali"
        ],
        "Fès": ["Aïn Kadous", "Dar Batha", "El Adarissa", "Médina", "Narjiss", "Zouagha"],
        "Tanger": ["Charf", "Hay Riad", "Médina", "Moghogha", "Mrabatten"]
    },
    "DZ": {
        "Alger": [
            "Bab El Oued", "Belouizdad", "Ben Aknoun", "Birmandreis", "Birkhadem",
            "Bir Mourad Raïs", "Bordj El Kiffan", "Casbah", "Chéraga", "Dély Ibrahim",
            "El Biar", "El Harrach", "Gue de Constantine", "Hussein Dey",
            "Kouba", "Oued Koriche", "Rouiba", "Saoula"
        ],
        "Oran": ["Es Senia", "Hai El Yasmine", "Khemisti", "M'Dina Jdida", "Maraval"],
        "Constantine": ["Ain Abid", "Bab El Kantara", "Cité Boussouf", "El Bir", "Gammas"]
    },
    "TN": {
        "Tunis": [
            "Ariana", "Bardo", "Belvédère", "Carthage", "El Menzah",
            "El Omrane", "Ennasr", "Ettadhamen", "Hai Khadra", "Jardins D'El Menzah",
            "La Goulette", "La Marsa", "Le Kram", "Mutuelleville", "Sidi Bou Saïd"
        ],
        "Sfax": ["Bir Hlou", "Chihia", "Hay Mahrajène", "Sidi Abid", "Sfax El Jadida"],
        "Sousse": ["Akouda", "Hammam Sousse", "Kantaoui", "Médina", "Sahloul"]
    },
    "ET": {
        "Addis Abeba": [
            "Addis Ketema", "Akaki Kaliti", "Arada", "Bole", "Gullele",
            "Kirkos", "Kolfe Keranio", "Lideta", "Nifas Silk-Lafto", "Yeka"
        ],
        "Dire Dawa": ["Dire Dawa City", "Gota", "Legehare", "Sabian"],
        "Mekelle": ["Adi Haqi", "Adi Haki", "Ayder", "Hadnet", "Semien"]
    },
    "UG": {
        "Kampala": [
            "Bukoto", "Bweyogerere", "Kabalagala", "Kamwokya", "Kawempe",
            "Kibuli", "Kololo", "Kyengera", "Makindye", "Mulago",
            "Mutungo", "Naguru", "Nakasero", "Nakulabye", "Nsambya",
            "Ntinda", "Rubaga", "Wandegeya"
        ],
        "Entebbe": ["Aeroport", "Garuga", "Katabi", "Kigungu", "Nkumba"]
    },
    "RW": {
        "Kigali": [
            "Gacuriro", "Gahanga", "Gikondo", "Giporoso", "Kabeza",
            "Kagugu", "Kanombe", "Kicukiro", "Kimironko", "Kinyinya",
            "Kirehe", "Masaka", "Ndera", "Niboye", "Remera", "Rugarama",
            "Rugenge", "Rwezamenyo"
        ]
    },
    "GN": {
        "Conakry": [
            "Bambeto", "Dixinn", "Kaporo", "Kaloum", "Kagbelen",
            "Lambanyi", "Lansanaya", "Madina", "Matam", "Matoto",
            "Ratoma", "Sonfonia", "Wanindara"
        ]
    },
    "MG": {
        "Antananarivo": [
            "Ambanidia", "Ambatobe", "Ambodinifaho", "Ambohijatovo", "Analakely",
            "Ankorondrano", "Ankadifotsy", "Antaninarenina", "Behoririka",
            "Faravohitra", "Isoraka", "Mahamasina", "Tsaralalana"
        ]
    },
    "FR": {
        "Paris": [
            "1er arrondissement", "2e arrondissement", "3e arrondissement",
            "4e arrondissement", "5e arrondissement", "6e arrondissement",
            "7e arrondissement", "8e arrondissement", "9e arrondissement",
            "10e arrondissement", "11e arrondissement", "12e arrondissement",
            "13e arrondissement", "14e arrondissement", "15e arrondissement",
            "16e arrondissement", "17e arrondissement", "18e arrondissement",
            "19e arrondissement", "20e arrondissement"
        ],
        "Lyon": ["Bron", "Caluire", "Confluence", "Croix-Rousse", "Part-Dieu", "Presqu'île", "Villeurbanne"],
        "Marseille": ["Baumettes", "Castellane", "La Joliette", "Le Panier", "Noailles", "Prado", "Vieux-Port"]
    },
    "BE": {
        "Bruxelles": [
            "Anderlecht", "Auderghem", "Berchem-Sainte-Agathe", "Elsene",
            "Etterbeek", "Evere", "Forest", "Ganshoren", "Jette",
            "Koekelberg", "Laeken", "Molenbeek", "Saint-Gilles",
            "Saint-Josse", "Schaerbeek", "Uccle", "Woluwe-Saint-Lambert"
        ]
    },
    "CA": {
        "Montréal": [
            "Ahuntsic-Cartierville", "Anjou", "Côte-des-Neiges", "Côte-Saint-Luc",
            "Hochelaga", "LaSalle", "Lachine", "Mercier", "Mont-Royal",
            "Outremont", "Plateau-Mont-Royal", "Rosemont", "Verdun",
            "Ville-Émard", "Villeray"
        ]
    },
    "US": {
        "New York": [
            "Bronx", "Brooklyn", "Harlem", "Lower Manhattan", "Manhattan",
            "Queens", "Staten Island", "Upper East Side", "Upper West Side", "Williamsburg"
        ],
        "Los Angeles": [
            "Culver City", "Downtown", "Hollywood", "Koreatown", "Long Beach",
            "Santa Monica", "Venice", "Westwood"
        ]
    },
    "GB": {
        "Londres": [
            "Brixton", "Camden", "Chelsea", "Croydon", "Hackney",
            "Hammersmith", "Islington", "Kensington", "Lambeth",
            "Lewisham", "Newham", "Southwark", "Tower Hamlets", "Westminster"
        ]
    }
}


def _country_key():
    """Accept ISO country codes while preserving the historical `cameroon` query."""
    country_code = request.args.get("country_code", "").upper()
    if country_code:
        return country_code
    country = request.args.get("country", "cameroon").lower()
    return "CM" if country in {"cameroon", "cameroun", "cm"} else country.upper()


def _normalize(name):
    """Comparaison insensible à la casse et aux accents (« Yaounde » == « Yaoundé »)."""
    decomposed = unicodedata.normalize("NFKD", name or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().lower()


# --- Base locale complète (data/localites/<PAYS>.json) ------------------------
# Villes et quartiers enregistrés une fois pour toutes depuis OpenStreetMap par
# scripts/construire_localites.py : aucune dépendance aux serveurs gratuits
# pour ces pays.
RANG_TYPE = {"city": 0, "town": 1, "village": 2}
_localites_cache = {}


def _importance(ville):
    """Capitale, puis grandes villes par population, puis villes, puis villages."""
    return (RANG_TYPE.get(ville.get("type"), 2), not ville.get("capitale"),
            -(ville.get("population") or 0), _normalize(ville.get("nom")))


def _localites(country):
    """{nom normalisé: ville} du fichier du pays, relu s'il a changé.
    Les autres noms y figurent aussi : « dubai » donne « Doubaï »."""
    return _base_locale(country)[0]


def _villes_locales(country):
    """Liste des villes du fichier du pays (une entrée par ville)."""
    return _base_locale(country)[1]


def _base_locale(country):
    if not re.fullmatch(r"[A-Z]{2}", country or ""):
        return {}, []
    chemin = os.path.join(current_app.root_path, "data", "localites", f"{country}.json")
    try:
        date = os.path.getmtime(chemin)
    except OSError:
        return {}, []
    deja = _localites_cache.get(country)
    if deja and deja[0] == date:
        return deja[1], deja[2]
    try:
        with open(chemin, encoding="utf-8") as fichier:
            villes = json.load(fichier).get("villes", [])
    except (OSError, ValueError):
        return {}, []
    index = {}
    for ville in villes:  # nom principal d'abord : il prime sur un autre nom
        index.setdefault(_normalize(ville.get("nom")), ville)
    for ville in villes:
        for autre in ville.get("autres_noms", []):
            index.setdefault(_normalize(autre), ville)
    _localites_cache[country] = (date, index, villes)
    return index, villes


def _local_neighborhoods(country, city):
    wanted = _normalize(city)
    quartiers = []
    for c_name, c_neighborhoods in CITY_DATA.get(country, {}).items():
        if _normalize(c_name) == wanted:
            quartiers = list(c_neighborhoods)
    enregistree = _localites(country).get(wanted)
    if enregistree:
        # « Dubai » saisi : la base manuelle est aussi cherchée sous « Doubaï ».
        noms = {_normalize(n) for n in [enregistree["nom"], *enregistree.get("autres_noms", [])]}
        for c_name, c_neighborhoods in CITY_DATA.get(country, {}).items():
            if _normalize(c_name) in noms and not quartiers:
                quartiers = list(c_neighborhoods)
        quartiers += enregistree.get("quartiers", [])
    uniques = {}  # « Atong-Abe » et « Atong Abe » ne font qu'un (la base manuelle prime)
    for nom in quartiers:
        uniques.setdefault(re.sub(r"[\s\-_']+", " ", _normalize(nom)), nom)
    return list(uniques.values())


# --- Repli OpenStreetMap -----------------------------------------------------
# La base locale ne couvre qu'une partie des villes. Pour toutes les autres,
# on interroge OpenStreetMap une seule fois par ville : Nominatim situe la
# ville dans le pays choisi, puis Overpass liste les quartiers
# (place=suburb|neighbourhood|quarter) dans un rayon autour de son centre.
# Les résultats sont mis en cache sur disque ; un échec réseau n'est retenu
# que quelques minutes pour ne pas bloquer une ville définitivement.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Miroirs publics et gratuits d'Overpass, du plus rapide au plus lent : le
# serveur principal est souvent saturé (erreur 502), on passe alors au suivant.
OVERPASS_URLS = (
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
# Photon (komoot) : moteur de recherche gratuit et sans clé sur les données
# OpenStreetMap, utilisé pour l'autocomplétion (villes et quartiers).
PHOTON_URL = "https://photon.komoot.io/api/"
TYPES_QUARTIER = ("suburb", "neighbourhood", "quarter", "city_district", "borough")
# Dans les petites villes, les quartiers sont souvent cartographiés comme
# localités, hameaux ou villages rattachés.
TYPES_LISTE = TYPES_QUARTIER + ("locality", "hamlet", "village")
TYPES_VILLE = ("city", "town", "village", "municipality")
OSM_USER_AGENT = "Lumistock/1.0 (configuration des magasins)"
OSM_TIME_BUDGET = 20  # secondes maximum par recherche
OSM_FAILURE_TTL = 20  # secondes avant qu'une demande retente directement
DELAIS_REPRISE = (20, 60, 180, 600)  # reprises automatiques en arrière-plan
NEIGHBORHOOD_RADIUS_M = 10000
MAX_CITY_LENGTH = 120

_cache_lock = threading.Lock()
_failures = {}


def _cache_path():
    folder = os.path.join(current_app.root_path, "cache")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "osm_neighborhoods.json")


def _read_cache():
    try:
        with open(_cache_path(), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _write_cache_entry(key, neighborhoods):
    with _cache_lock:
        cache = _read_cache()
        cache[key] = neighborhoods
        temporary = _cache_path() + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, ensure_ascii=False)
        os.replace(temporary, _cache_path())


def _http_json(url, data=None, timeout=8):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": OSM_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def _nom_lisible(tags):
    """Nom en français, sinon en anglais, sinon le nom local (évite les noms
    en caractères chinois, arabes… quand une traduction existe)."""
    return tags.get("name:fr") or tags.get("name:en") or tags.get("name")


def _coordonnees(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _osm_neighborhoods(country, city, coordonnees=None):
    """Retourne la liste des quartiers, ou None si OpenStreetMap est injoignable."""
    deadline = time.monotonic() + OSM_TIME_BUDGET
    if coordonnees:
        lat, lon = coordonnees  # fournies par le navigateur : pas besoin de Nominatim
    else:
        places = _http_json(NOMINATIM_URL + "?" + urllib.parse.urlencode({
            "city": city, "countrycodes": country.lower(), "format": "json", "limit": 1,
        }), timeout=6)
        if not places:
            return []  # ville inconnue d'OpenStreetMap : réponse définitive
        lat, lon = float(places[0]["lat"]), float(places[0]["lon"])
    types = "|".join(TYPES_LISTE)
    query = (
        f'[out:json][timeout:15];'
        f'node(around:{NEIGHBORHOOD_RADIUS_M},{lat},{lon})'  # requête légère : passe mieux
        f'["place"~"^({types})$"]["name"];'
        f'out tags 400;'
    )
    payload = urllib.parse.urlencode({"data": query}).encode()
    # Deux tours sur les miroirs : ils tombent et reviennent d'une minute à l'autre.
    for tour, url in enumerate(OVERPASS_URLS * 2):
        remaining = deadline - time.monotonic()
        if remaining < 2:
            break
        if tour == len(OVERPASS_URLS):
            time.sleep(1.5)  # courte pause avant le second tour
        try:
            data = _http_json(url, data=payload, timeout=min(remaining, 10))
        except Exception:
            continue  # serveur saturé : essayer le miroir suivant
        names = {
            _nom_lisible(element["tags"])
            for element in data.get("elements", [])
            if element.get("tags", {}).get("name")
        }
        return sorted(names, key=_normalize)
    return None


def _remote_neighborhoods(country, city, coordonnees=None):
    key = f"{country}|{_normalize(city)}"
    cached = _read_cache().get(key)
    if cached:  # une liste vide en cache est retentée (souvent un échec passé)
        return cached
    if time.monotonic() - _failures.get(key, -OSM_FAILURE_TTL) < OSM_FAILURE_TTL:
        return []
    try:
        neighborhoods = _osm_neighborhoods(country, city, coordonnees)
    except Exception as error:
        current_app.logger.warning("Recherche OSM des quartiers impossible (%s) : %s", key, error)
        neighborhoods = None
    if neighborhoods is None:
        _failures[key] = time.monotonic()
        _reprendre_plus_tard(key, country, city, coordonnees)
        return []
    _write_cache_entry(key, neighborhoods)
    return neighborhoods


_reprises_en_cours = set()


def _reprendre_plus_tard(key, country, city, coordonnees):
    """OpenStreetMap saturé : nouvelles tentatives en arrière-plan.

    Dès qu'une tentative réussit, la liste est mise en cache : la page qui la
    redemande (toutes les 20 s) l'affiche alors sans intervention.
    """
    if key in _reprises_en_cours:
        return
    _reprises_en_cours.add(key)
    app = current_app._get_current_object()

    def travail():
        try:
            for delai in DELAIS_REPRISE:
                time.sleep(delai)
                with app.app_context():
                    if _read_cache().get(key):
                        return
                    try:
                        resultat = _osm_neighborhoods(country, city, coordonnees)
                    except Exception:
                        resultat = None
                    if resultat is not None:
                        _write_cache_entry(key, resultat)
                        _failures.pop(key, None)
                        return
        finally:
            _reprises_en_cours.discard(key)

    threading.Thread(target=travail, name=f"quartiers-{key}", daemon=True).start()


@location_bp.route("/cities", methods=["GET"])
def get_cities():
    """
    Récupère la liste des villes disponibles.
    ---
    tags:
      - Locations
    responses:
      200:
        description: Liste des villes récupérée
    """
    country = _country_key()
    # Grandes villes d'abord, puis les villes de la base manuelle absentes du fichier.
    villes = [v["nom"] for v in sorted(_villes_locales(country), key=_importance)]
    connues = set(_localites(country))
    manuelles = sorted((n for n in CITY_DATA.get(country, {}) if _normalize(n) not in connues), key=_normalize)
    return jsonify(manuelles + villes if villes else manuelles), 200


@location_bp.route("/villes", methods=["GET"])
def get_villes():
    """
    Villes d'un pays avec coordonnées, région et importance (base locale).
    ---
    tags:
      - Locations
    parameters:
      - {name: country_code, in: query, type: string, required: true}
    responses:
      200:
        description: "[{nom, lat, lon, region, rang, quartiers}] — rang 0 = grande ville, 1 = ville, 2 = village"
    """
    country = _country_key()
    villes, index = [], {}
    for v in _villes_locales(country):
        ville = {
            "nom": v["nom"], "autres_noms": v.get("autres_noms", []), "lat": v.get("lat"), "lon": v.get("lon"),
            "region": v.get("region"), "rang": RANG_TYPE.get(v.get("type"), 2),
            "population": v.get("population") or 0, "quartiers": len(v.get("quartiers", [])),
            "capitale": bool(v.get("capitale")),
        }
        villes.append(ville)
        for n in [v["nom"], *ville["autres_noms"]]:
            index.setdefault(_normalize(n), ville)
    for nom, quartiers in CITY_DATA.get(country, {}).items():
        ville = index.get(_normalize(nom))
        if not ville:  # ville de la base manuelle absente du fichier
            ville = index[_normalize(nom)] = {
                "nom": nom, "autres_noms": [], "lat": None, "lon": None, "region": None,
                "rang": 0, "population": 0, "quartiers": 0}
            villes.append(ville)
        ville["rang"] = 0
        ville["quartiers"] = len(_local_neighborhoods(country, nom))
    resultat = sorted(villes, key=lambda v: (
        v["rang"], not v.get("capitale"), -v["population"], _normalize(v["nom"])))
    # Réponse compacte (champs vides omis) et compressée : le Canada compte
    # près de 20 000 localités.
    compacte = [{k: val for k, val in v.items() if val or k == "rang"} for v in resultat]
    contenu = json.dumps(compacte, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    entetes = {"Cache-Control": "private, max-age=3600"}
    if "gzip" in request.headers.get("Accept-Encoding", ""):
        contenu = gzip.compress(contenu, 6)
        entetes.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(contenu, 200, headers=entetes, mimetype="application/json")


@location_bp.route("/neighborhoods", methods=["GET"])
def get_neighborhoods():
    """
    Récupère les quartiers pour une ville donnée.
    La base locale est consultée en premier ; pour toute autre ville, les
    quartiers sont recherchés dans OpenStreetMap puis mis en cache.
    ---
    tags:
      - Locations
    parameters:
      - name: country_code
        in: query
        type: string
        required: false
      - name: city
        in: query
        type: string
        required: true
    responses:
      200:
        description: Liste des quartiers récupérée (éventuellement vide)
    """
    city = (request.args.get("city") or "").strip()
    country = _country_key()

    if not city:
        return jsonify({"error": "Le paramètre 'city' est requis"}), 400
    if len(city) > MAX_CITY_LENGTH:
        return jsonify({"error": "Nom de ville trop long"}), 400

    neighborhoods = _local_neighborhoods(country, city)

    # Ancien comportement sans pays explicite : chercher la ville partout.
    if not neighborhoods and not request.args.get("country_code"):
        for country_key in CITY_DATA:
            neighborhoods = _local_neighborhoods(country_key, city)
            if neighborhoods:
                break

    if not neighborhoods and re.fullmatch(r"[A-Z]{2}", country):
        neighborhoods = _remote_neighborhoods(
            country, city, _coordonnees(request.args.get("lat"), request.args.get("lon")))

    return jsonify(sorted(set(neighborhoods), key=_normalize)), 200


# --- Autocomplétion gratuite (Photon) ----------------------------------------
_photon_cache = {}
PHOTON_CACHE_MAX = 2000


def _photon(params, cle):
    """Appel Photon mis en cache (mémoire) ; [] si le service ne répond pas."""
    if cle in _photon_cache:
        return _photon_cache[cle]
    try:
        data = _http_json(PHOTON_URL + "?" + urllib.parse.urlencode(params, doseq=True), timeout=25)
    except Exception as error:
        current_app.logger.warning("Photon injoignable : %s", error)
        return []
    resultats = data.get("features", [])
    if len(_photon_cache) >= PHOTON_CACHE_MAX:
        _photon_cache.clear()
    _photon_cache[cle] = resultats
    return resultats


def _saisie():
    q = (request.args.get("q") or "").strip()
    return q if 2 <= len(q) <= MAX_CITY_LENGTH else None


@location_bp.route("/cities/search", methods=["GET"])
def search_cities():
    """
    Autocomplétion des villes d'un pays (n'importe quelle ville ou village du monde).
    ---
    tags:
      - Locations
    parameters:
      - {name: q, in: query, type: string, required: true}
      - {name: country_code, in: query, type: string, required: true}
    responses:
      200:
        description: "[{nom, lat, lon}]"
    """
    q, country = _saisie(), _country_key()
    if not q or not re.fullmatch(r"[A-Z]{2}", country):
        return jsonify([]), 200
    params = {"q": q, "limit": 40, "lang": "fr", "layer": "city"}
    resultats = _photon(params, f"ville|{country}|{_normalize(q)}")
    villes, vus = [], set()
    for f in resultats:
        prop = f.get("properties", {})
        if (prop.get("countrycode") or "").upper() != country or prop.get("osm_value") not in TYPES_VILLE:
            continue
        nom = prop.get("name")
        if not nom or _normalize(nom) in vus:
            continue
        vus.add(_normalize(nom))
        lon, lat = f.get("geometry", {}).get("coordinates", [None, None])
        villes.append({"nom": nom, "lat": lat, "lon": lon, "region": prop.get("state")})
    return jsonify(villes[:20]), 200


@location_bp.route("/neighborhoods/search", methods=["GET"])
def search_neighborhoods():
    """
    Autocomplétion des quartiers autour d'une ville (lat/lon), pour toute ville du monde.
    ---
    tags:
      - Locations
    parameters:
      - {name: q, in: query, type: string, required: true}
      - {name: lat, in: query, type: number, required: true}
      - {name: lon, in: query, type: number, required: true}
    responses:
      200:
        description: Noms de quartiers
    """
    q, coordonnees = _saisie(), _coordonnees(request.args.get("lat"), request.args.get("lon"))
    if not q or not coordonnees:
        return jsonify([]), 200
    lat, lon = coordonnees
    marge = 0.25  # ≈ 25 km autour du centre de la ville
    params = {
        "q": q, "limit": 50, "lang": "fr",
        "bbox": f"{lon - marge},{lat - marge},{lon + marge},{lat + marge}",
        "osm_tag": [f"place:{t}" for t in TYPES_QUARTIER + ("locality", "village", "hamlet")],
    }
    resultats = _photon(params, f"quartier|{round(lat, 2)}|{round(lon, 2)}|{_normalize(q)}")
    noms = sorted({f["properties"]["name"] for f in resultats if f.get("properties", {}).get("name")}, key=_normalize)
    return jsonify(noms), 200
