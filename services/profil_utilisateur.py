"""Profil détaillé d'un membre de l'équipe (manager, gérant, caissier) et de sa boutique.

Un seul endroit valide et nettoie ces champs, à la création comme à la
modification : téléphones au format international, pays en code ISO, dates
réalistes, listes fermées pour le sexe, la pièce d'identité et le type de
boutique, longueurs bornées. Les champs vides sont ignorés (jamais d'écrasement
par une chaîne vide involontaire).
"""
import re
from datetime import date, datetime

TELEPHONE = re.compile(r"^\+[1-9]\d{6,14}$")
PAYS = re.compile(r"^[A-Z]{2}$")
SEXES = ("Homme", "Femme")
PIECES = ("CNI", "Passeport", "Carte de séjour", "Permis de conduire", "Carte consulaire", "Autre")
TYPES_BOUTIQUE = ("Boutique", "Kiosque", "Supérette", "Supermarché", "Dépôt", "Entrepôt",
                  "Point de vente mobile", "Stand de marché", "Pharmacie", "Autre")

# Champs simples : (nom, longueur maximale).
TEXTES = {
    "fonction": 100, "ville": 120, "quartier": 120, "adresse": 200, "notes": 500, "poste_caisse": 50,
}
TEXTES_BOUTIQUE = {"nom": 120, "ville": 120, "quartier": 120, "adresse": 200, "horaires": 150, "description": 300}

CHAMPS_PROFIL = (
    "telephone", "sexe", "date_naissance", "fonction", "pays", "ville", "quartier", "adresse",
    "piece_identite", "contact_urgence", "date_embauche", "notes", "poste_caisse", "boutique",
)


class ProfilInvalide(ValueError):
    pass


def _texte(valeur, maximum, libelle):
    texte = re.sub(r"\s+", " ", str(valeur or "")).strip()
    if len(texte) > maximum:
        raise ProfilInvalide(f"{libelle} : {maximum} caractères au maximum.")
    return texte


def _telephone(valeur, libelle="Téléphone"):
    numero = re.sub(r"[\s.\-()]", "", str(valeur or ""))
    if not numero:
        return ""
    if not TELEPHONE.match(numero):
        raise ProfilInvalide(f"{libelle} invalide : format international attendu (ex. +237690123456).")
    return numero


def _pays(valeur, libelle="Pays"):
    code = str(valeur or "").strip().upper()
    if code and not PAYS.match(code):
        raise ProfilInvalide(f"{libelle} invalide : code à deux lettres attendu (ex. CM).")
    return code


def _date(valeur, libelle, age_min=None, futur=False):
    texte = str(valeur or "").strip()[:10]
    if not texte:
        return ""
    try:
        jour = datetime.strptime(texte, "%Y-%m-%d").date()
    except ValueError:
        raise ProfilInvalide(f"{libelle} invalide (format AAAA-MM-JJ).")
    aujourd_hui = date.today()
    if not futur and jour > aujourd_hui:
        raise ProfilInvalide(f"{libelle} ne peut pas être dans le futur.")
    if age_min is not None:
        age = aujourd_hui.year - jour.year - ((aujourd_hui.month, aujourd_hui.day) < (jour.month, jour.day))
        if age < age_min or age > 100:
            raise ProfilInvalide(f"{libelle} : l'âge doit être compris entre {age_min} et 100 ans.")
    return jour.isoformat()


def _choix(valeur, options, libelle):
    texte = str(valeur or "").strip()
    if texte and texte not in options:
        raise ProfilInvalide(f"{libelle} invalide : {', '.join(options)}.")
    return texte


def nettoyer_profil(data, role=None):
    """Champs de profil valides et non vides présents dans ``data``.

    :raises ProfilInvalide: avec un message clair pour l'utilisateur.
    """
    data = data or {}
    profil = {}

    def garder(cle, valeur):
        if valeur not in ("", None, {}):
            profil[cle] = valeur

    if "telephone" in data:
        garder("telephone", _telephone(data["telephone"]))
    if "sexe" in data:
        garder("sexe", _choix(data["sexe"], SEXES, "Sexe"))
    if "date_naissance" in data:
        garder("date_naissance", _date(data["date_naissance"], "Date de naissance", age_min=16))
    if "date_embauche" in data:
        garder("date_embauche", _date(data["date_embauche"], "Date d'entrée"))
    if "pays" in data:
        garder("pays", _pays(data["pays"]))
    for cle, maximum in TEXTES.items():
        if cle in data and (cle != "poste_caisse" or role in (None, "caissier")):
            garder(cle, _texte(data[cle], maximum, cle.replace("_", " ").capitalize()))

    if isinstance(data.get("piece_identite"), dict):
        piece = data["piece_identite"]
        type_ = _choix(piece.get("type"), PIECES, "Type de pièce d'identité")
        numero = _texte(piece.get("numero"), 50, "Numéro de pièce d'identité")
        if numero and not type_:
            raise ProfilInvalide("Choisissez le type de la pièce d'identité.")
        if type_ or numero:
            profil["piece_identite"] = {"type": type_, "numero": numero}

    if isinstance(data.get("contact_urgence"), dict):
        contact = data["contact_urgence"]
        urgence = {
            "nom": _texte(contact.get("nom"), 120, "Nom du contact d'urgence"),
            "lien": _texte(contact.get("lien"), 60, "Lien avec le contact d'urgence"),
            "telephone": _telephone(contact.get("telephone"), "Téléphone du contact d'urgence"),
        }
        if any(urgence.values()):
            if not urgence["telephone"]:
                raise ProfilInvalide("Indiquez le téléphone du contact d'urgence.")
            profil["contact_urgence"] = urgence

    if isinstance(data.get("boutique"), dict) and role in (None, "gerant", "livreur"):
        b = data["boutique"]
        boutique = {cle: _texte(b.get(cle), maximum, f"Boutique : {cle}") for cle, maximum in TEXTES_BOUTIQUE.items()}
        boutique["type"] = _choix(b.get("type"), TYPES_BOUTIQUE, "Type de boutique")
        boutique["telephone"] = _telephone(b.get("telephone"), "Téléphone de la boutique")
        boutique["pays"] = _pays(b.get("pays"), "Pays de la boutique")
        boutique = {k: v for k, v in boutique.items() if v}
        if boutique:
            profil["boutique"] = boutique
            if boutique.get("nom"):
                profil["nom_boutique"] = boutique["nom"]  # champ historique, toujours rempli
    return profil
