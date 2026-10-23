"""Fiche du magasin (page d'accueil) : identité légale et conformité OHADA."""

from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


def _magasin(**champs):
    mag, admin = ObjectId(), ObjectId()
    db.tables.magasin.insert_one({"_id": mag, "denomination": "Boutique", "is_active": True, "created_at": datetime.utcnow(),
                                  "licence": {"max_managers": 1, "max_livreurs": 2}, **champs})
    db.tables.users.insert_one({"_id": admin, "role": "admin", "email": f"{admin}@t.com", "magasin_id": mag, "is_active": True})
    return {"Authorization": f"Bearer {generate_token(str(admin), 'admin')}"}


def test_magasin_uemoa_complet(client, clear_db):
    h = _magasin(pays="Sénégal", country_code="SN", raison_sociale="Boutique SA", forme_juridique="SA",
                 numero_immatriculation="SN-DKR-2026-B-1", numero_fiscal="0012345", adresse_ligne_1="Rue 10", telephone="+221")
    d = client.get("/managers/magasin/profil", headers=h).get_json()
    c = d["conformite"]
    assert c["membre_ohada"] and c["zone"] == "UEMOA" and c["taux_tva"] == 18.0
    assert c["devise"] == "XOF"  # devise de la zone quand elle n'est pas renseignée
    assert c["referentiel"] == "SYSCOHADA révisé" and c["exercice_en_cours"] == datetime.now().year
    assert d["champs_manquants"] == []
    assert sum(len(g["pays"]) for g in d["pays_ohada"]) == 17


def test_magasin_hors_ohada_et_fiche_incomplete(client, clear_db):
    h = _magasin(pays="France", country_code="FR", devise="EUR")
    d = client.get("/managers/magasin/profil", headers=h).get_json()
    assert d["conformite"]["membre_ohada"] is False and d["conformite"]["taux_tva"] == 20.0
    assert "N° RCCM" in d["champs_manquants"] and "N° contribuable (NIU)" in d["champs_manquants"]
