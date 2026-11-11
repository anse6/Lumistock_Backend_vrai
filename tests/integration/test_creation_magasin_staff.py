"""Création d'un magasin par le staff : mêmes règles que l'inscription publique."""
import io
import json
import os
from datetime import datetime
from unittest.mock import patch

import pytest

from extensions import db
from models.magasin_model import MagasinModel
from models.user_model import UserModel
from utils.jwt_token import generate_token

FORMULAIRE = {
    "denomination": "  Boutique Staff  ", "pays": "Cameroun", "country_code": "CM",
    "ville": "Douala", "quartier": "Akwa", "email": "Staff-Boutique@Test.com",
    "telephone": "+237699999997", "activité": "Quincaillerie", "devise": "XAF",
    "admin_name": "Mballa", "admin_first_name": "Awa", "admin_email": "Staff-Admin@Test.com",
    "nombre_manager": "1", "nombre_livreur": "2", "nombre_caissier": "3",
}


@pytest.fixture
def staff(app):
    with app.app_context():
        for table in ("magasin", "users"):
            db.tables[table].delete_many({})
        staff_id = str(db.tables.users.insert_one({
            "name": "Staff", "first_name": "Test", "email": "staff@lumistock.test", "role": "staff",
            "is_active": True, "created_at": datetime.utcnow(),
        }).inserted_id)
    return {"Authorization": f"Bearer {generate_token(user_id=staff_id, role='staff')}"}


def _creer(client, headers, **changes):
    data = {**FORMULAIRE, **changes}
    with patch("services.user_service.envoyer_invitation", return_value=True):
        return client.post("/magasin/create", data=data, headers=headers, content_type="multipart/form-data")


def test_creation_avec_admin_a_plat(client, app, staff):
    r = _creer(client, staff, admin_telephone="+237 6 90 12 34 56", admin_sexe="Femme", admin_fonction="Gérante")
    assert r.status_code == 201, r.get_json()
    with app.app_context():
        magasin = MagasinModel.get_magasin_by_email("staff-boutique@test.com")
        admin = UserModel.find_by_email("staff-admin@test.com")
    assert magasin["denomination"] == "Boutique Staff" and magasin["is_active"] is True
    assert magasin["licence"]["max_caissiers"] == 3
    assert admin["role"] == "admin" and admin["sexe"] == "Femme" and admin["telephone"] == "+237690123456"


def test_le_sexe_n_est_plus_impose(client, app, staff):
    assert _creer(client, staff).status_code == 201
    with app.app_context():
        assert "sexe" not in UserModel.find_by_email("staff-admin@test.com")


def test_ancien_format_json_encore_accepte(client, app, staff):
    data = {k: v for k, v in FORMULAIRE.items() if not k.startswith("admin_")}
    data["admin"] = json.dumps({"name": "Mballa", "first_name": "Awa", "email": "staff-admin@test.com"})
    with patch("services.user_service.envoyer_invitation", return_value=True):
        r = client.post("/magasin/create", data=data, headers=staff, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()


@pytest.mark.parametrize("champ, valeur", [
    ("admin_email", "pas-un-email"),
    ("admin_first_name", "A"),
    ("telephone", "699999997"),
])
def test_champs_invalides_signales_sur_le_champ(client, staff, champ, valeur):
    r = _creer(client, staff, **{champ: valeur})
    assert r.status_code == 400 and champ in r.get_json()["errors"]


@pytest.mark.parametrize("champ, valeur", [
    ("admin_telephone", "12"),
    ("admin_sexe", "Autre"),
    ("admin_date_naissance", "2020-01-01"),  # moins de 16 ans
])
def test_profil_admin_valide_comme_a_l_inscription(client, app, staff, champ, valeur):
    r = _creer(client, staff, **{champ: valeur})
    assert r.status_code == 400, r.get_json()
    with app.app_context():
        assert MagasinModel.get_magasin_by_email("staff-boutique@test.com") is None


def test_doublon_refuse_sans_logo_orphelin(client, app, staff):
    assert _creer(client, staff).status_code == 201
    dossier = app.config["UPLOAD_FOLDER"]
    avant = set(os.listdir(dossier))
    r = _creer(client, staff, admin_email="autre@test.com",
               image=(io.BytesIO(b"\x89PNG\r\n\x1a\n"), "logo.png", "image/png"))
    assert r.status_code == 400 and "existe déjà" in r.get_json()["message"]
    assert set(os.listdir(dossier)) == avant


def test_reserve_au_staff(client, app, staff):
    with app.app_context():
        admin_id = str(db.tables.users.insert_one({"email": "a@b.test", "role": "admin", "is_active": True}).inserted_id)
    r = _creer(client, {"Authorization": f"Bearer {generate_token(user_id=admin_id, role='admin')}"})
    assert r.status_code == 403
