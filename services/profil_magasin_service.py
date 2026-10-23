"""Fiche du magasin pour la page d'accueil : identité légale et conformité OHADA.

Tout ce qui est renvoyé vient de la base ou des règles déjà appliquées par
l'application (TVA par pays, devise, méthode de valorisation, clôtures) :
la page d'accueil n'affiche rien qui ne soit réellement géré.
"""

from datetime import datetime

from bson import ObjectId
from flask import request

from extensions import db
from services import cloture_service
from services.tva_service import TAUX_TVA, code_pays, decimales_devise
from models.magasin_model import MAX_CAISSIERS_DEFAUT

# Les 17 États membres de l'OHADA, par zone monétaire.
ZONES_OHADA = {
    "CEMAC": {"CM": "Cameroun", "CF": "Centrafrique", "CG": "Congo", "GA": "Gabon", "GQ": "Guinée équatoriale", "TD": "Tchad"},
    "UEMOA": {"BJ": "Bénin", "BF": "Burkina Faso", "CI": "Côte d'Ivoire", "GW": "Guinée-Bissau", "ML": "Mali",
              "NE": "Niger", "SN": "Sénégal", "TG": "Togo"},
    "Autres États membres": {"KM": "Comores", "GN": "Guinée", "CD": "RD Congo"},
}
DEVISES_ZONE = {"CEMAC": "XAF", "UEMOA": "XOF"}
METHODES_VALORISATION = {
    "CMUP": "Coût moyen unitaire pondéré (CMUP)",
    "FIFO": "Premier entré, premier sorti (FIFO)",
    "LIFO": "Dernier entré, premier sorti (LIFO)",
}
CHAMPS_LEGAUX = {
    "raison_sociale": "Raison sociale",
    "forme_juridique": "Forme juridique",
    "numero_immatriculation": "N° RCCM",
    "numero_fiscal": "N° contribuable (NIU)",
    "adresse_ligne_1": "Adresse",
    "telephone": "Téléphone",
}


def _texte(valeur):
    valeur = (valeur or "").strip() if isinstance(valeur, str) else valeur
    return valeur or None


def _zone(code):
    for zone, pays in ZONES_OHADA.items():
        if code in pays:
            return zone
    return None


def profil_magasin(magasin_id):
    magasin = db.tables.magasin.find_one({"_id": ObjectId(str(magasin_id))}) or {}
    code = code_pays(magasin)
    zone = _zone(code)
    devise = str(magasin.get("devise") or DEVISES_ZONE.get(zone) or "XAF").upper()
    etat_clotures = cloture_service.etat(str(magasin_id))
    derniere = etat_clotures["clotures"][0] if etat_clotures["clotures"] else None
    exercice = (int(derniere["exercice"]) + 1) if derniere and derniere.get("exercice") else datetime.now().year

    utilisateurs = [u for u in db.tables.users.find({"magasin_id": ObjectId(str(magasin_id))}) if u.get("is_active")]
    licence = magasin.get("licence") if isinstance(magasin.get("licence"), dict) else {}
    adresse = ", ".join(p for p in (magasin.get("adresse_ligne_1"), magasin.get("adresse_ligne_2"), magasin.get("quartier"),
                                     magasin.get("ville"), magasin.get("pays")) if _texte(p))

    return {
        "identite": {
            "denomination": magasin.get("denomination") or "",
            "raison_sociale": _texte(magasin.get("raison_sociale")),
            "forme_juridique": _texte(magasin.get("forme_juridique")),
            "rccm": _texte(magasin.get("numero_immatriculation")),
            "niu": _texte(magasin.get("numero_fiscal")),
            "activite": _texte(magasin.get("activité")),
            "adresse": adresse or None,
            "ville": _texte(magasin.get("ville")),
            "pays": _texte(magasin.get("pays")),
            "code_pays": code or None,
            "telephone": _texte(magasin.get("telephone")),
            "email": _texte(magasin.get("email")),
            "site_web": _texte(magasin.get("site_web")),
            # Même URL que le reste de l'application (fichier servi par /uploads/images/).
            "logo": f"{request.host_url}uploads/images/{magasin['logo']}" if _texte(magasin.get("logo")) else None,
            "cree_le": magasin["created_at"].isoformat() if isinstance(magasin.get("created_at"), datetime) else None,
        },
        "conformite": {
            "membre_ohada": zone is not None,
            "zone": zone,
            "referentiel": "SYSCOHADA révisé",
            "taux_tva": TAUX_TVA.get(code),
            "devise": devise,
            "decimales": decimales_devise(devise),
            "valorisation": METHODES_VALORISATION.get(str(magasin.get("methode_valorisation") or "CMUP").upper(), "CMUP"),
            "exercice_en_cours": exercice,
            "derniere_cloture": {"exercice": derniere["exercice"], "date_fin": derniere["date_fin"], "resultat": derniere["resultat"]}
            if derniere else None,
            "periode_verrouillee_jusquau": etat_clotures["date_verrouillage"],
        },
        "licence": {
            "managers": sum(1 for u in utilisateurs if u.get("role") == "manager"),
            "max_managers": licence.get("max_managers"),
            "gerants": sum(1 for u in utilisateurs if u.get("role") in ("gerant", "livreur")),
            "max_gerants": licence.get("max_livreurs"),
            "caissiers": sum(1 for u in utilisateurs if u.get("role") == "caissier"),
            "max_caissiers": licence.get("max_caissiers", MAX_CAISSIERS_DEFAUT),
        },
        "champs_manquants": [libelle for champ, libelle in CHAMPS_LEGAUX.items() if not _texte(magasin.get(champ))],
        "pays_ohada": [{"zone": z, "pays": [{"code": c, "nom": n} for c, n in pays.items()]} for z, pays in ZONES_OHADA.items()],
        "pays_tva": len(TAUX_TVA),
    }
