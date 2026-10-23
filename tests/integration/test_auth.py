import pytest
from bson import ObjectId
from datetime import datetime
from extensions import db
from models.user_model import UserModel
from models.magasin_model import MagasinModel
from utils.jwt_token import generate_token, generate_account_token
from flask_bcrypt import Bcrypt
from unittest.mock import patch

bcrypt = Bcrypt()
MOT_DE_PASSE = "password1"
NOUVEAU = "Nouveau2026"


@pytest.fixture
def setup_auth(app):
    with app.app_context():
        magasin_id = "662c0cbf6b5a4a1f25facc22"
        MagasinModel.get_collection().insert_one({
            "_id": ObjectId(magasin_id),
            "denomination": "Test Magasin",
            "logo": "logo.png",
            "is_active": True,
            "created_at": datetime.utcnow(),
            "pays": "Cameroun",
            "ville": "Douala",
            "quartier": "Bonamoussadi",
            "email": "magasin@test.com",
            "activité": "Commerce",
            "licence": {"max_managers": 2, "max_livreurs": 5}
        })
        comptes = {}
        for cle, role, email, actif, magasin in (
            ("admin_id", "admin", "admin@test.com", True, magasin_id),
            ("staff_id", "staff", "staff@test.com", True, None),
            ("inactive_user_id", "manager", "inactive@test.com", False, magasin_id),
        ):
            doc = {
                "name": cle, "first_name": "Test", "email": email,
                "password": bcrypt.generate_password_hash(MOT_DE_PASSE).decode("utf-8"),
                "role": role, "is_active": actif, "created_at": datetime.utcnow(),
            }
            if magasin:
                doc["magasin_id"] = ObjectId(magasin)
            comptes[cle] = str(UserModel.get_collection().insert_one(doc).inserted_id)
        yield {**comptes, "magasin_id": magasin_id}
        db.tables.users.delete_many({"_id": {"$in": [ObjectId(v) for v in comptes.values()]}})
        db.tables.magasin.delete_one({"_id": ObjectId(magasin_id)})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _login(client, email="admin@test.com", password=MOT_DE_PASSE):
    return client.post("/auth/login", json={"email": email, "password": password})


# --- Connexion -------------------------------------------------------------

def test_login_success_admin(client, setup_auth):
    response = _login(client)
    assert response.status_code == 200
    data = response.get_json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["role"] == "admin"
    assert data["email"] == "admin@test.com"
    assert data["denomination_magasin"] == "Test Magasin"
    assert data["magasin_id"] == setup_auth["magasin_id"]


def test_login_email_insensible_a_la_casse(client, setup_auth):
    assert _login(client, email="  ADMIN@Test.com ").status_code == 200


def test_login_success_staff(client, setup_auth):
    response = _login(client, email="staff@test.com")
    assert response.status_code == 200
    data = response.get_json()
    assert data["role"] == "staff"
    assert "denomination_magasin" not in data
    assert "magasin_id" not in data


def test_login_message_identique_compte_inconnu_ou_mauvais_mot_de_passe(client, setup_auth):
    mauvais = _login(client, password="wrong_password")
    inconnu = _login(client, email="personne@test.com")
    assert mauvais.status_code == inconnu.status_code == 401
    assert mauvais.get_json() == inconnu.get_json() == {"error": "Email ou mot de passe incorrect."}


def test_login_inactive_user(client, setup_auth):
    # L'état du compte n'est révélé qu'avec le bon mot de passe.
    assert _login(client, email="inactive@test.com", password="faux").status_code == 401
    response = _login(client, email="inactive@test.com")
    assert response.status_code == 403
    assert "inactif" in response.get_json()["error"]


def test_login_verrouillage_apres_5_echecs(client, setup_auth):
    for _ in range(5):
        assert _login(client, password="faux").status_code == 401
    # Même le bon mot de passe est refusé pendant le verrouillage.
    response = _login(client)
    assert response.status_code == 429
    assert "Trop de tentatives" in response.get_json()["error"]


def test_login_succes_remet_les_echecs_a_zero(client, setup_auth):
    for _ in range(4):
        _login(client, password="faux")
    assert _login(client).status_code == 200
    for _ in range(4):
        assert _login(client, password="faux").status_code == 401
    assert _login(client).status_code == 200


def test_login_missing_data(client):
    response = client.post("/auth/login", json={"email": "admin@test.com"})
    assert response.status_code == 400
    assert response.get_json()["error"] == "Email et mot de passe sont requis."


def test_login_invalid_schema(client, setup_auth):
    response = client.post("/auth/login", json={"email": "invalid_email", "password": "password"})
    assert response.status_code == 401
    assert "error" in response.get_json()


# --- Sessions : refresh et déconnexion --------------------------------------

def test_refresh_token_success(client, setup_auth):
    refresh = _login(client).get_json()["refresh_token"]
    response = client.post("/auth/refresh", headers=_bearer(refresh))
    assert response.status_code == 200
    nouveau = response.get_json()["access_token"]
    assert client.get("/users/me", headers=_bearer(nouveau)).status_code == 200


def test_refresh_token_unauthorized(client):
    response = client.post("/auth/refresh")
    assert response.status_code == 401
    assert response.get_json()["msg"] == "Missing Authorization Header"


def test_logout_revoque_access_et_refresh(client, setup_auth):
    tokens = _login(client).get_json()
    access, refresh = tokens["access_token"], tokens["refresh_token"]
    assert client.get("/users/me", headers=_bearer(access)).status_code == 200
    response = client.post("/auth/logout", json={"refresh_token": refresh}, headers=_bearer(access))
    assert response.status_code == 200
    assert client.get("/users/me", headers=_bearer(access)).status_code == 401
    assert client.post("/auth/refresh", headers=_bearer(refresh)).status_code == 401


def test_logout_all_ferme_toutes_les_sessions(client, setup_auth):
    session_1 = _login(client).get_json()["access_token"]
    session_2 = _login(client).get_json()["access_token"]
    assert client.post("/auth/logout_all", headers=_bearer(session_1)).status_code == 200
    assert client.get("/users/me", headers=_bearer(session_1)).status_code == 401
    assert client.get("/users/me", headers=_bearer(session_2)).status_code == 401
    assert _login(client).status_code == 200


def test_desactivation_ferme_les_sessions(client, setup_auth):
    access = _login(client).get_json()["access_token"]
    staff = generate_token(setup_auth["staff_id"], "staff")
    response = client.put(f"/users/deactivate-user/{setup_auth['admin_id']}", headers=_bearer(staff))
    assert response.status_code == 200
    # Même réactivé, l'ancien token ne revient pas.
    client.put(f"/users/activate-user/{setup_auth['admin_id']}", headers=_bearer(staff))
    assert client.get("/users/me", headers=_bearer(access)).status_code == 401


# --- Mot de passe oublié / liens à usage unique ------------------------------

def test_forgot_password_success(client, setup_auth):
    with patch("utils.email.send_lien_compte_email", return_value=True) as mail:
        response = client.post("/auth/forgot_password", json={"email": "admin@test.com"})
    assert response.status_code == 200
    assert response.get_json()["message"] == "Si cet email existe, un lien a été envoyé"
    mail.assert_called_once()
    assert mail.call_args.args[:2] == ("admin@test.com", "reset")


def test_forgot_password_non_existent_email(client):
    with patch("utils.email.send_lien_compte_email") as mail:
        response = client.post("/auth/forgot_password", json={"email": "nonexistent@test.com"})
    assert response.status_code == 200
    assert response.get_json()["message"] == "Si cet email existe, un lien a été envoyé"
    mail.assert_not_called()


def test_forgot_password_compte_desactive_ne_recoit_rien(client, setup_auth):
    with patch("utils.email.send_lien_compte_email") as mail:
        response = client.post("/auth/forgot_password", json={"email": "inactive@test.com"})
    assert response.status_code == 200
    mail.assert_not_called()


def test_forgot_password_limite_les_demandes(client, setup_auth):
    with patch("utils.email.send_lien_compte_email", return_value=True) as mail:
        for _ in range(8):
            assert client.post("/auth/forgot_password", json={"email": "admin@test.com"}).status_code == 200
    assert mail.call_count == 5


def _lien(app, user_id, purpose="reset"):
    with app.app_context():
        return generate_account_token(UserModel.find_by_id(user_id), purpose)


def test_reset_password_success_et_usage_unique(app, client, setup_auth):
    session = _login(client).get_json()["access_token"]
    token = _lien(app, setup_auth["admin_id"])
    assert client.get(f"/auth/verify_link/{token}").get_json()["valid"] is True

    response = client.post(f"/auth/reset_password/{token}",
                           json={"new_password": NOUVEAU, "confirm_password": NOUVEAU})
    assert response.status_code == 200
    user = UserModel.find_by_id(setup_auth["admin_id"])
    assert bcrypt.check_password_hash(user["password"], NOUVEAU)

    # Le lien ne sert qu'une fois, et les sessions ouvertes sont fermées.
    second = client.post(f"/auth/reset_password/{token}",
                         json={"new_password": "Autre2026x", "confirm_password": "Autre2026x"})
    assert second.status_code == 400
    assert client.get(f"/auth/verify_link/{token}").status_code == 400
    assert client.get("/users/me", headers=_bearer(session)).status_code == 401
    assert _login(client, password=NOUVEAU).status_code == 200


def test_reset_password_refuse_un_token_de_connexion(client, setup_auth):
    """Un access token volé ne doit pas permettre de changer le mot de passe."""
    access = _login(client).get_json()["access_token"]
    response = client.post(f"/auth/reset_password/{access}",
                           json={"new_password": NOUVEAU, "confirm_password": NOUVEAU})
    assert response.status_code == 400
    assert _login(client).status_code == 200


def test_reset_password_mot_de_passe_faible(app, client, setup_auth):
    token = _lien(app, setup_auth["admin_id"])
    for faible in ("court1", "seulementdeslettres", "12345678"):
        response = client.post(f"/auth/reset_password/{token}",
                               json={"new_password": faible, "confirm_password": faible})
        assert response.status_code == 400
    # Le lien n'a pas été consommé par les essais refusés.
    assert client.get(f"/auth/verify_link/{token}").status_code == 200


def test_reset_password_mismatch(app, client, setup_auth):
    token = _lien(app, setup_auth["admin_id"])
    response = client.post(f"/auth/reset_password/{token}",
                           json={"new_password": NOUVEAU, "confirm_password": "different"})
    assert response.status_code == 400
    assert response.get_json()["error"] == "Les nouveaux mots de passe ne correspondent pas."


def test_reset_password_invalid_token(client):
    response = client.post("/auth/reset_password/invalid_token",
                           json={"new_password": NOUVEAU, "confirm_password": NOUVEAU})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_lien_inutilisable_pour_un_compte_desactive(app, client, setup_auth):
    token = _lien(app, setup_auth["admin_id"])
    staff = generate_token(setup_auth["staff_id"], "staff")
    client.put(f"/users/deactivate-user/{setup_auth['admin_id']}", headers=_bearer(staff))
    response = client.post(f"/auth/reset_password/{token}",
                           json={"new_password": NOUVEAU, "confirm_password": NOUVEAU})
    assert response.status_code == 400


def test_activate_account_desactive(app, client, setup_auth):
    """L'ancien lien d'activation permettait à un compte désactivé de se réactiver."""
    token = _lien(app, setup_auth["admin_id"])
    response = client.post(f"/auth/activate_account/{token}")
    assert response.status_code == 410
    assert UserModel.find_by_id(setup_auth["inactive_user_id"])["is_active"] is False


# --- Changement de mot de passe ---------------------------------------------

def test_change_password_success(client, setup_auth):
    ancienne_session = _login(client).get_json()["access_token"]
    token = _login(client).get_json()["access_token"]
    response = client.post(
        "/auth/change_password",
        json={"old_password": MOT_DE_PASSE, "new_password": NOUVEAU, "confirm_password": NOUVEAU},
        headers=_bearer(token),
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["message"] == "Mot de passe modifié avec succès"
    user = UserModel.find_by_id(setup_auth["admin_id"])
    assert bcrypt.check_password_hash(user["password"], NOUVEAU)
    # Les autres sessions sont fermées, la session courante a de nouveaux tokens.
    assert client.get("/users/me", headers=_bearer(ancienne_session)).status_code == 401
    assert client.get("/users/me", headers=_bearer(data["access_token"])).status_code == 200


def test_change_password_incorrect_old_password(client, setup_auth):
    token = generate_token(setup_auth["admin_id"], "admin")
    response = client.post(
        "/auth/change_password",
        json={"old_password": "wrong_password", "new_password": NOUVEAU, "confirm_password": NOUVEAU},
        headers=_bearer(token),
    )
    assert response.status_code == 401
    assert response.get_json()["error"] == "Mot de passe actuel incorrect"


def test_change_password_mismatch(client, setup_auth):
    token = generate_token(setup_auth["admin_id"], "admin")
    response = client.post(
        "/auth/change_password",
        json={"old_password": MOT_DE_PASSE, "new_password": NOUVEAU, "confirm_password": "different"},
        headers=_bearer(token),
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "Les nouveaux mots de passe ne correspondent pas."


def test_change_password_invalid_schema(client, setup_auth):
    token = generate_token(setup_auth["admin_id"], "admin")
    response = client.post(
        "/auth/change_password",
        json={"old_password": MOT_DE_PASSE, "new_password": ""},
        headers=_bearer(token),
    )
    assert response.status_code == 400
    assert "error" in response.get_json()


# --- Comptes staff ------------------------------------------------------------

def test_create_staff_refuse_sans_connexion(client, setup_auth):
    response = client.post("/users/create_staff", json={
        "name": "Pirate", "first_name": "X", "email": "pirate@test.com",
        "password": "Pirate123", "confirm_password": "Pirate123"})
    assert response.status_code == 401
    assert UserModel.find_by_email("pirate@test.com") is None


def test_create_staff_refuse_pour_un_admin(client, setup_auth):
    token = generate_token(setup_auth["admin_id"], "admin")
    response = client.post("/users/create_staff", headers=_bearer(token), json={
        "name": "Pirate", "first_name": "X", "email": "pirate@test.com"})
    assert response.status_code == 403


def test_create_staff_par_un_staff_envoie_une_invitation(app, client, setup_auth):
    token = generate_token(setup_auth["staff_id"], "staff")
    with patch("utils.email.send_lien_compte_email", return_value=True) as mail:
        response = client.post("/users/create_staff", headers=_bearer(token), json={
            "name": "Nouveau", "first_name": "Staff", "email": "nouveau.staff@test.com",
            "password": "ignore123"})
    assert response.status_code == 201
    assert mail.call_args.args[:2] == ("nouveau.staff@test.com", "invitation")
    # Le mot de passe éventuellement envoyé est ignoré : personne ne le connaît.
    assert _login(client, email="nouveau.staff@test.com", password="ignore123").status_code == 401

    # Le lien d'invitation permet au titulaire de choisir son mot de passe.
    invitation = mail.call_args.args[2]
    response = client.post(f"/auth/reset_password/{invitation}",
                           json={"new_password": NOUVEAU, "confirm_password": NOUVEAU})
    assert response.status_code == 200
    assert _login(client, email="nouveau.staff@test.com", password=NOUVEAU).status_code == 200
    db.tables.users.delete_many({"email": "nouveau.staff@test.com"})
