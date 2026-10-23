"""Profil détaillé des membres de l'équipe : création, validation, modification."""
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from tests.integration.test_caisse import m, h  # noqa: F401

PROFIL = {
    "telephone": "+237 690 12 34 56", "sexe": "Femme", "date_naissance": "1995-04-12", "fonction": "Responsable boutique",
    "pays": "cm", "ville": "Douala", "quartier": "Akwa", "adresse": "Face pharmacie du Port",
    "piece_identite": {"type": "CNI", "numero": "123456789"},
    "contact_urgence": {"nom": "Paul Ngono", "lien": "Frère", "telephone": "+237677000000"},
    "date_embauche": "2026-01-05",
}


def test_creation_d_un_gerant_avec_profil_et_boutique(client, app, m):
    corps = {"name": "Ngono", "first_name": "Alice", "email": "alice@b.test", **PROFIL,
             "boutique": {"nom": "Boutique Akwa", "type": "Kiosque", "telephone": "+237699000000", "pays": "CM",
                          "ville": "Douala", "quartier": "Akwa", "adresse": "Rue Joss", "horaires": "Lun-Sam 8h-20h"}}
    r = client.post("/users/create_gerant", json=corps, headers=h(m, "admin"))
    assert r.status_code == 201, r.get_json()
    with app.app_context():
        u = db.tables.users.find_one({"email": "alice@b.test"})
    assert u["telephone"] == "+237690123456" and u["pays"] == "CM" and u["sexe"] == "Femme"
    assert u["piece_identite"] == {"type": "CNI", "numero": "123456789"}
    assert u["boutique"]["type"] == "Kiosque" and u["nom_boutique"] == "Boutique Akwa"


@pytest.mark.parametrize("champ, valeur", [
    ("telephone", "690123"), ("sexe", "X"), ("date_naissance", "2020-01-01"), ("pays", "Cameroun"),
    ("date_embauche", "2999-01-01"), ("piece_identite", {"numero": "123"}),
    ("contact_urgence", {"nom": "Paul"}),
])
def test_profil_invalide_refuse_sans_creer_le_compte(client, app, m, champ, valeur):
    r = client.post("/users/create_manager", json={"name": "A", "first_name": "B", "email": "x@b.test", champ: valeur},
                    headers=h(m, "admin"))
    assert r.status_code == 400
    with app.app_context():
        assert db.tables.users.find_one({"email": "x@b.test"}) is None


def test_modification_du_profil_et_retrait_d_un_champ(client, app, m):
    url = f"/users/{m['manager']}"
    assert client.put(url, json={"name": "manager", **PROFIL}, headers=h(m, "admin")).status_code == 200
    # Enregistrer sans changement : pas d'erreur.
    assert client.put(url, json={"name": "manager"}, headers=h(m, "admin")).status_code == 200
    # Vider le quartier le retire du compte.
    assert client.put(url, json={"quartier": ""}, headers=h(m, "admin")).status_code == 200
    with app.app_context():
        u = db.tables.users.find_one({"_id": ObjectId(m["manager"])})
    assert "quartier" not in u and u["ville"] == "Douala"
    assert client.put(url, json={"telephone": "abc"}, headers=h(m, "admin")).status_code == 400
