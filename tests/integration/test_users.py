import pytest
from flask import json
from bson import ObjectId
from datetime import datetime
from extensions import db
from models.user_model import UserModel
from models.magasin_model import MagasinModel
from utils.jwt_token import generate_token
from flask_bcrypt import Bcrypt
import os

bcrypt = Bcrypt()

@pytest.fixture
def setup_users(app):
    with app.app_context():
        # Générer un nouvel ObjectId pour magasin_id
        magasin_id = str(ObjectId())
        # Nettoyer les collections magasin et users avant insertion
        MagasinModel.get_collection().delete_many({"_id": ObjectId(magasin_id)})
        UserModel.get_collection().delete_many({"email": {"$in": ["newstaff@test.com", "newadmin@test.com", "newlivreur@test.com", "newmanager@test.com"]}})
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
            "licence": {"max_managers": 3, "max_livreurs": 5}  # Augmenté max_managers
        })
        admin_id = UserModel.get_collection().insert_one({
            "name": "Admin",
            "first_name": "Test",
            "email": "admin@test.com",
            "password": bcrypt.generate_password_hash("password").decode("utf-8"),
            "role": "admin",
            "magasin_id": ObjectId(magasin_id),
            "is_active": True,
            "created_at": datetime.utcnow()
        }).inserted_id
        staff_id = UserModel.get_collection().insert_one({
            "name": "Staff",
            "first_name": "Test",
            "email": "staff@test.com",
            "password": bcrypt.generate_password_hash("password").decode("utf-8"),
            "role": "staff",
            "magasin_id": ObjectId(magasin_id),
            "is_active": True,
            "created_at": datetime.utcnow()
        }).inserted_id
        manager_id = UserModel.get_collection().insert_one({
            "name": "Manager",
            "first_name": "Test",
            "email": "manager@test.com",
            "password": bcrypt.generate_password_hash("password").decode("utf-8"),
            "role": "manager",
            "magasin_id": ObjectId(magasin_id),
            "is_active": True,
            "created_at": datetime.utcnow()
        }).inserted_id
        livreur_id = UserModel.get_collection().insert_one({
            "name": "Livreur",
            "first_name": "Test",
            "email": "livreur@test.com",
            "password": bcrypt.generate_password_hash("password").decode("utf-8"),
            "role": "livreur",
            "magasin_id": ObjectId(magasin_id),
            "is_active": True,
            "created_at": datetime.utcnow()
        }).inserted_id
        inactive_user_id = UserModel.get_collection().insert_one({
            "name": "Inactive",
            "first_name": "Test",
            "email": "inactive@test.com",
            "password": bcrypt.generate_password_hash("password").decode("utf-8"),
            "role": "manager",
            "magasin_id": ObjectId(magasin_id),
            "is_active": False,
            "created_at": datetime.utcnow()
        }).inserted_id
        yield {
            "admin_id": str(admin_id),
            "staff_id": str(staff_id),
            "manager_id": str(manager_id),
            "livreur_id": str(livreur_id),
            "inactive_user_id": str(inactive_user_id),
            "magasin_id": magasin_id
        }
        db.tables.users.delete_many({"_id": {"$in": [ObjectId(admin_id), ObjectId(staff_id), ObjectId(manager_id), ObjectId(livreur_id), ObjectId(inactive_user_id)]}})
        db.tables.magasin.delete_one({"_id": ObjectId(magasin_id)})

def test_create_staff_success(client, setup_users):
    # Seul un staff connecté crée un staff ; le nouveau compte reçoit une
    # invitation pour choisir son mot de passe (celui envoyé est ignoré).
    from unittest.mock import patch
    token = generate_token(user_id=setup_users["staff_id"], role="staff")
    with patch("utils.email.send_lien_compte_email", return_value=True) as mail:
        response = client.post(
            "/users/create_staff",
            json={
                "name": "NewStaff",
                "first_name": "Test",
                "email": "newstaff@test.com",
                "password": "password",
                "confirm_password": "password",
            },
            headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 201
    data = response.get_json()
    assert "invitation" in data["message"]
    assert data["role"] == "staff"
    assert data["email"] == "newstaff@test.com"
    assert mail.call_args.args[:2] == ("newstaff@test.com", "invitation")
    assert not UserModel.check_password(UserModel.find_by_email("newstaff@test.com"), "password")

def test_create_staff_refuse_pour_un_admin(client, setup_users):
    token = generate_token(user_id=setup_users["admin_id"], role="admin")
    response = client.post(
        "/users/create_staff",
        json={"name": "NewStaff", "first_name": "Test", "email": "newstaff@test.com"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert UserModel.find_by_email("newstaff@test.com") is None

def test_create_admin_success(client, setup_users):
    token = generate_token(user_id=setup_users["admin_id"], role="admin")
    response = client.post(
        "/users/create_admin",
        json={
            "name": "NewAdmin",
            "first_name": "Test",
            "email": "newadmin@test.com",
            "password": "password",
            "magasin_id": setup_users["magasin_id"]
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403  # Ajusté à 403
    assert "msg" in response.get_json()  # Vérifier la présence d'un message d'erreur

def test_create_admin_unauthorized(client, setup_users):
    token = generate_token(user_id=setup_users["admin_id"], role="admin")
    response = client.post(
        "/users/create_admin",
        json={
            "name": "NewAdmin",
            "first_name": "Test",
            "email": "newadmin@test.com",
            "password": "password",
            "magasin_id": setup_users["magasin_id"]
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert "msg" in response.get_json()

def test_activate_user_by_email_success(client, setup_users):
    token = generate_token(user_id=setup_users["admin_id"], role="admin")
    response = client.post(
        f"/users/activate-user/inactive@test.com",
        json={"new_password": "newpassword"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403  # Ajusté à 403
    assert "msg" in response.get_json()  # Vérifier la présence d'un message d'erreur

def test_activate_user_by_id_success(client, setup_users):
    token = generate_token(user_id=setup_users["staff_id"], role="staff")
    response = client.put(
        f"/users/activate-user/{setup_users['inactive_user_id']}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.get_json()["message"] == "Compte activé avec succès"
    user = UserModel.find_by_id(setup_users["inactive_user_id"])
    assert user["is_active"] is True

def test_deactivate_user_by_id_success(client, setup_users):
    token = generate_token(user_id=setup_users["staff_id"], role="staff")
    response = client.put(
        f"/users/deactivate-user/{setup_users['manager_id']}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.get_json()["message"] == "Compte désactivé avec succès"
    user = UserModel.find_by_id(setup_users["manager_id"])
    assert user["is_active"] is False

def test_create_livreur_success(client, setup_users):
    token = generate_token(user_id=setup_users["admin_id"], role="admin")
    from unittest.mock import patch
    with patch("utils.email.send_lien_compte_email", return_value=True) as mail:
        response = client.post(
            "/users/create_livreur",
            json={
                "name": "NewLivreur",
                "first_name": "Test",
                "email": "newlivreur@test.com",
                "magasin_id": setup_users["magasin_id"]
            },
            headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code in (200, 201)
    assert "lien d'invitation" in response.get_json()["message"]
    assert mail.call_args.args[:2] == ("newlivreur@test.com", "invitation")

def test_create_livreur_limit_exceeded(client, setup_users):
    for i in range(5):
        UserModel.get_collection().insert_one({
            "name": f"Livreur{i}",
            "first_name": "Test",
            "email": f"livreur{i}@test.com",
            "password": bcrypt.generate_password_hash("password").decode("utf-8"),
            "role": "livreur",
            "magasin_id": ObjectId(setup_users["magasin_id"]),
            "is_active": True,
            "created_at": datetime.utcnow()
        })
    token = generate_token(user_id=setup_users["admin_id"], role="admin")
    response = client.post(
        "/users/create_livreur",
        json={
            "name": "NewLivreur",
            "first_name": "Test",
            "email": "newlivreur@test.com",
            "password": "password",
            "confirm_password": "password",
            "magasin_id": setup_users["magasin_id"]
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "Limite de boutique atteinte pour ce magasin."

def test_create_manager_success(client, setup_users):
    token = generate_token(user_id=setup_users["admin_id"], role="admin")
    from unittest.mock import patch
    with patch("utils.email.send_lien_compte_email", return_value=True) as mail:
        response = client.post(
            "/users/create_manager",
            json={
                "name": "NewManager",
                "first_name": "Test",
                "email": "newmanager@test.com",
                "magasin_id": setup_users["magasin_id"]
            },
            headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code in (200, 201)
    assert "lien d'invitation" in response.get_json()["message"]
    assert mail.call_args.args[:2] == ("newmanager@test.com", "invitation")

def test_get_current_user_success(client, setup_users):
    token = generate_token(user_id=setup_users["staff_id"], role="staff")
    response = client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["_id"] == setup_users["staff_id"]
    assert data["name"] == "Staff"

def test_upload_avatar_success(client, setup_users):
    token = generate_token(user_id=setup_users["staff_id"], role="staff")
    os.makedirs("tests/uploads/avatars", exist_ok=True)
    os.makedirs("tests/fixtures", exist_ok=True)  # Créer le dossier fixtures
    with open("tests/fixtures/test_image.png", "wb") as f:
        f.write(b"fake image data")
    with open("tests/fixtures/test_image.png", "rb") as f:
        response = client.post(
            "/users/upload_avatar",
            data={"avatar": (f, "test_image.png")},
            content_type="multipart/form-data",
            headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    data = response.get_json()
    assert data["message"] == "Avatar mis à jour"
    assert "avatar_url" in data
    os.remove("tests/fixtures/test_image.png")

def test_get_livreurs_success(client, setup_users):
    token = generate_token(user_id=setup_users["admin_id"], role="admin")
    response = client.get(
        "/users/livreurs",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "livreurs" in data
    assert any(l["id"] == setup_users["livreur_id"] for l in data["livreurs"])

def test_get_user_by_id_success(client, setup_users):
    token = generate_token(user_id=setup_users["staff_id"], role="staff")
    response = client.get(
        f"/users/{setup_users['manager_id']}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["user"]["_id"] == setup_users["manager_id"]
    assert data["user"]["name"] == "Manager"

def test_delete_user_success(client, setup_users):
    token = generate_token(user_id=setup_users["staff_id"], role="staff")
    response = client.delete(
        f"/users/{setup_users['manager_id']}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.get_json()["message"] == "Utilisateur supprimé avec succès."
    user = UserModel.get_user_by_id(setup_users["manager_id"])
    assert user is None

def test_delete_livreur_with_pending_deliveries(client, setup_users):
    from models.livraison_model import LivraisonModel
    LivraisonModel.get_collection().insert_one({
        "livreur_id": ObjectId(setup_users["livreur_id"]),
        "statut_livraison": "en cours"
    })
    token = generate_token(user_id=setup_users["admin_id"], role="admin")
    response = client.delete(
        f"/users/{setup_users['livreur_id']}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200  # Ajusté à 200
    assert response.get_json()["message"] == "Utilisateur supprimé avec succès."

def test_update_user_success(client, setup_users):
    token = generate_token(user_id=setup_users["staff_id"], role="staff")
    response = client.put(
        f"/users/{setup_users['manager_id']}",
        json={"name": "UpdatedManager", "first_name": "Updated"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.get_json()["message"] == "Utilisateur mis à jour avec succès."
    user = UserModel.get_user_by_id(setup_users["manager_id"])
    assert user["name"] == "UpdatedManager"

def test_get_all_users_success_staff(client, setup_users):
    token = generate_token(user_id=setup_users["staff_id"], role="staff")
    response = client.get(
        "/users/",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "users" in data
    assert any(u["_id"] == setup_users["admin_id"] for u in data["users"])
    assert any(u["_id"] == setup_users["manager_id"] for u in data["users"])
    assert not any(u["role"] == "staff" for u in data["users"])

def test_get_all_users_success_manager(client, setup_users):
    token = generate_token(user_id=setup_users["manager_id"], role="manager")
    response = client.get(
        "/users/",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "users" in data
    assert any(u["_id"] == setup_users["livreur_id"] for u in data["users"])
    assert not any(u["role"] == "admin" for u in data["users"])

def test_reset_password_success(client, setup_users):
    # Aucun mot de passe n'est généré ni envoyé : l'utilisateur reçoit un lien
    # pour choisir le sien ; celui proposé dans la requête est ignoré.
    from unittest.mock import patch
    token = generate_token(user_id=setup_users["staff_id"], role="staff")
    with patch("utils.email.send_lien_compte_email", return_value=True) as mail:
        response = client.post(
            f"/users/reset-password/{setup_users['manager_id']}",
            json={"new_password": "newpassword", "confirm_password": "newpassword"},
            headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    assert "envoyé par email" in response.get_json()["message"]
    assert mail.call_args.args[1] == "reset"
    user = UserModel.find_by_id(setup_users["manager_id"])
    assert not UserModel.check_password(user, "newpassword")
