"""Isolation entre magasins pour les routes /users/<id>."""
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def comptes(app):
    with app.app_context():
        for table in ("magasin", "users"):
            db.tables[table].delete_many({})
        result = {}
        for nom in ("A", "B"):
            magasin_id = ObjectId()
            db.tables.magasin.insert_one({"_id": magasin_id, "denomination": nom, "is_active": True,
                                          "created_at": datetime.utcnow()})
            for role in ("admin", "manager", "gerant"):
                result[f"{role}_{nom}"] = str(db.tables.users.insert_one({
                    "name": role, "first_name": nom, "email": f"{role}@{nom.lower()}.test", "role": role,
                    "password": "hash", "magasin_id": magasin_id, "is_active": True,
                    "created_at": datetime.utcnow(),
                }).inserted_id)
        result["staff"] = str(db.tables.users.insert_one({
            "name": "staff", "first_name": "S", "email": "staff@test", "role": "staff",
            "is_active": True, "created_at": datetime.utcnow(),
        }).inserted_id)
        yield result


def _headers(comptes, cle):
    role = "staff" if cle == "staff" else cle.split("_")[0]
    return {"Authorization": f"Bearer {generate_token(user_id=comptes[cle], role=role)}"}


def _user(app, user_id):
    with app.app_context():
        return db.tables.users.find_one({"_id": ObjectId(user_id)})


@pytest.mark.parametrize("method, url, kwargs", [
    ("get", "/users/{id}", {}),
    ("put", "/users/{id}", {"json": {"name": "Pirate"}}),
    ("delete", "/users/{id}", {}),
    ("put", "/users/deactivate-user/{id}", {}),
    ("put", "/users/activate-user/{id}", {}),
    ("post", "/users/reset-password/{id}", {"json": {"new_password": "piratage"}}),
])
def test_un_admin_ne_peut_pas_toucher_aux_comptes_d_un_autre_magasin(client, app, comptes, method, url, kwargs):
    cible = comptes["gerant_A"]
    response = getattr(client, method)(url.format(id=cible), headers=_headers(comptes, "admin_B"), **kwargs)
    assert response.status_code == 404
    user = _user(app, cible)
    assert user is not None
    assert (user["name"], user["password"], user["is_active"]) == ("gerant", "hash", True)


def test_lecture_autorisee_dans_son_magasin_et_pour_le_staff(client, comptes):
    for cle in ("admin_A", "manager_A", "gerant_A", "staff"):
        response = client.get(f"/users/{comptes['gerant_A']}", headers=_headers(comptes, cle))
        assert response.status_code == 200, cle
        assert response.get_json()["user"]["email"] == "gerant@a.test"
        assert "password" not in response.get_json()["user"]


def test_un_manager_ne_peut_pas_modifier_un_admin(client, app, comptes):
    headers = _headers(comptes, "manager_A")
    assert client.put(f"/users/{comptes['admin_A']}", json={"name": "X"}, headers=headers).status_code == 403
    assert client.delete(f"/users/{comptes['admin_A']}", headers=headers).status_code == 403
    assert client.post(f"/users/reset-password/{comptes['admin_A']}", json={}, headers=headers).status_code == 403
    assert _user(app, comptes["admin_A"])["name"] == "admin"
    # Il garde la main sur les gérants de son magasin.
    response = client.put(f"/users/{comptes['gerant_A']}", json={"name": "Nouveau"}, headers=headers)
    assert response.status_code == 200
    assert _user(app, comptes["gerant_A"])["name"] == "Nouveau"


@pytest.mark.parametrize("champ, valeur", [
    ("role", "staff"),
    ("magasin_id", str(ObjectId())),
    ("password", "en-clair"),
    ("is_active", True),
])
def test_impossible_de_s_attribuer_un_role_ou_un_autre_magasin(client, app, comptes, champ, valeur):
    response = client.put(f"/users/{comptes['admin_A']}", json={champ: valeur, "name": "admin"},
                          headers=_headers(comptes, "admin_A"))
    assert response.status_code == 400
    user = _user(app, comptes["admin_A"])
    assert user["role"] == "admin" and user["password"] == "hash"


def test_email_deja_utilise_refuse(client, comptes):
    response = client.put(f"/users/{comptes['gerant_A']}", json={"email": "Admin@A.test"},
                          headers=_headers(comptes, "admin_A"))
    assert response.status_code == 409


def test_le_staff_gere_tous_les_magasins(client, app, comptes):
    response = client.put(f"/users/deactivate-user/{comptes['gerant_B']}", headers=_headers(comptes, "staff"))
    assert response.status_code == 200
    assert _user(app, comptes["gerant_B"])["is_active"] is False


def test_reinitialisation_envoie_un_lien_et_jamais_de_mot_de_passe(client, app, comptes):
    from unittest.mock import patch

    with patch("utils.email.send_lien_compte_email", return_value=True) as mail:
        response = client.post(f"/users/reset-password/{comptes['gerant_A']}", json={},
                               headers=_headers(comptes, "admin_A"))
    assert response.status_code == 200
    body = response.get_json()
    assert "gerant@a.test" in body["message"]

    email, type_lien, token = mail.call_args.args
    assert (email, type_lien) == ("gerant@a.test", "reset")
    assert mail.call_args.kwargs["prenom"] == "A"
    # L'ancien mot de passe ne fonctionne plus ; le lien permet d'en choisir un.
    assert _user(app, comptes["gerant_A"])["password"] != "hash"
    response = client.post(f"/auth/reset_password/{token}",
                           json={"new_password": "Choisi2026", "confirm_password": "Choisi2026"})
    assert response.status_code == 200


def test_si_l_email_echoue_le_mot_de_passe_n_est_pas_change(client, app, comptes):
    from unittest.mock import patch

    with patch("utils.email.send_lien_compte_email", return_value=False):
        response = client.post(f"/users/reset-password/{comptes['gerant_A']}", json={},
                               headers=_headers(comptes, "admin_A"))
    assert response.status_code == 502
    assert _user(app, comptes["gerant_A"])["password"] == "hash"


def test_un_mot_de_passe_impose_par_l_admin_est_ignore(client, app, comptes):
    from unittest.mock import patch

    with patch("utils.email.send_lien_compte_email", return_value=True):
        client.post(f"/users/reset-password/{comptes['gerant_A']}", json={"new_password": "123456"},
                    headers=_headers(comptes, "admin_A"))
    with app.app_context():
        from models.user_model import UserModel
        assert UserModel.check_password(UserModel.find_by_id(comptes["gerant_A"]), "123456") is False


def test_un_gerant_dont_les_livraisons_sont_terminees_peut_etre_supprime(client, app, comptes):
    with app.app_context():
        db.tables.livraisons.delete_many({})
        db.tables.livraisons.insert_one({
            "livreur_id": ObjectId(comptes["gerant_A"]), "statut_livraison": "livrée",
            "produits": [], "created_at": datetime.utcnow(),
        })
    response = client.delete(f"/users/{comptes['gerant_A']}", headers=_headers(comptes, "admin_A"))
    assert response.status_code == 200, response.get_json()
    assert _user(app, comptes["gerant_A"]) is None
