import pytest
from flask import json
from bson import ObjectId
from datetime import datetime
from extensions import db
from models.magasin_model import MagasinModel
from models.user_model import UserModel
from models.connexion_model import ConnexionModel
from utils.jwt_token import generate_token
from unittest.mock import patch
import os
import io

@pytest.fixture
def setup_magasins(app):
    with app.app_context():
        # Nettoyer la base de données pour éviter les interférences
        MagasinModel.get_collection().delete_many({})
        UserModel.get_collection().delete_many({})
        db.tables.clients.delete_many({})
        db.tables.connexions.delete_many({})
        db.tables.livraisons.delete_many({})

        magasin_id = str(ObjectId())
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
        admin_id = UserModel.get_collection().insert_one({
            "name": "Admin Test",
            "first_name": "Test",
            "email": "admin@test.com",
            "password": "hashed_password",
            "role": "admin",
            "magasin_id": ObjectId(magasin_id),
            "is_active": True,
            "created_at": datetime.utcnow(),
            "last_login": datetime.utcnow()
        }).inserted_id
        staff_id = UserModel.get_collection().insert_one({
            "name": "Staff Test",
            "first_name": "Test",
            "email": "staff@test.com",
            "password": "hashed_password",
            "role": "staff",
            "is_active": True,
            "created_at": datetime.utcnow(),
            "last_login": datetime.utcnow()
        }).inserted_id
        manager_id = UserModel.get_collection().insert_one({
            "name": "Manager Test",
            "first_name": "Test",
            "email": "manager@test.com",
            "role": "manager",
            "magasin_id": ObjectId(magasin_id),
            "is_active": True,
            "created_at": datetime.utcnow(),
            "last_login": datetime.utcnow()
        }).inserted_id
        livreur_id = UserModel.get_collection().insert_one({
            "name": "Livreur Test",
            "first_name": "Test",
            "email": "livreur@test.com",
            "role": "livreur",
            "magasin_id": ObjectId(magasin_id),
            "is_active": True,
            "created_at": datetime.utcnow(),
            "last_login": datetime.utcnow()
        }).inserted_id
        db.tables.clients.insert_one({
            "name": "Client Test",
            "email": "client@test.com",
            "magasin_id": ObjectId(magasin_id),
            "created_at": datetime.utcnow()
        })
        # Insérer une connexion pour test_get_utilisateurs_recents_success
        db.tables.connexions.insert_one({
            "user_id": ObjectId(admin_id),
            "email": "admin@test.com",
            "role": "admin",
            "magasin_id": ObjectId(magasin_id),
            "timestamp": datetime.utcnow(),
            "ip_address": "127.0.0.1"
        })
        # Insérer une livraison pour test_get_top_magasins_success
        db.tables.livraisons.insert_one({
            "magasin_id": ObjectId(magasin_id),
            "client_id": ObjectId(),
            "livreur_id": ObjectId(livreur_id),
            "montant_total": 1000.0,
            "created_at": datetime.utcnow(),
            "produits": [{"quantite": 2}]
        })
        yield {
            "magasin_id": magasin_id,
            "admin_id": str(admin_id),
            "staff_id": str(staff_id),
            "manager_id": str(manager_id),
            "livreur_id": str(livreur_id)
        }
        # Nettoyage
        UserModel.get_collection().delete_many({"_id": {"$in": [ObjectId(admin_id), ObjectId(staff_id), ObjectId(manager_id), ObjectId(livreur_id)]}})
        db.tables.clients.delete_many({"magasin_id": ObjectId(magasin_id)})
        MagasinModel.get_collection().delete_many({"_id": ObjectId(magasin_id)})
        db.tables.connexions.delete_many({"magasin_id": ObjectId(magasin_id)})
        db.tables.livraisons.delete_many({"magasin_id": ObjectId(magasin_id)})

def test_toggle_magasin_status_success(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.patch(
        f"/staff/magasins/{setup_magasins['magasin_id']}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["message"] in ["Magasin activé avec succès.", "Magasin désactivé avec succès."]
    magasin = MagasinModel.get_magasin_by_id(setup_magasins["magasin_id"])
    assert magasin["is_active"] is not True

def test_get_all_magasins_success(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.get(
        "/staff/magasins",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "magasins" in data
    assert len(data["magasins"]) >= 1
    assert any(m["_id"] == setup_magasins["magasin_id"] for m in data["magasins"])

def test_delete_magasin_success(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.delete(
        f"/staff/magasins/{setup_magasins['magasin_id']}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["message"] == "Magasin archivé avec succès."
    magasin = MagasinModel.get_magasin_by_id(setup_magasins["magasin_id"])
    assert magasin is None

def test_get_magasin_success(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["admin_id"], role="admin")
    response = client.get(
        f"/staff/magasins/{setup_magasins['magasin_id']}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "magasin" in data
    assert data["magasin"]["_id"] == setup_magasins["magasin_id"]
    assert data["magasin"]["denomination"] == "Test Magasin"
    assert all("password" not in user for user in data["magasin"]["utilisateurs"])


def test_get_unknown_magasin_returns_404(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.get(
        f"/magasin/{ObjectId()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_admin_cannot_read_another_magasin(client, setup_magasins):
    with client.application.app_context():
        other_id = str(ObjectId())
        MagasinModel.get_collection().insert_one({
            "_id": ObjectId(other_id),
            "denomination": "Autre magasin",
            "pays": "Cameroun",
            "ville": "Douala",
            "quartier": "Akwa",
            "email": "autre@test.com",
            "activité": "Commerce",
            "licence": {"max_managers": 1, "max_livreurs": 1},
            "is_active": True,
            "created_at": datetime.utcnow(),
        })
    token = generate_token(user_id=setup_magasins["admin_id"], role="admin")
    response = client.get(
        f"/magasin/{other_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403

def test_get_nombre_magasins_total(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.get(
        "/staff/magasins/total",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "valeur" in data
    assert data["valeur"] >= 1

def test_get_nombre_admins_managers_total(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.get(
        "/staff/admins-managers/total",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "valeur" in data
    assert data["valeur"] >= 1

def test_get_nombre_livreurs_total(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.get(
        "/staff/livreurs/total",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "valeur" in data
    assert data["valeur"] >= 1

def test_get_nombre_clients_total(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.get(
        "/staff/clients/total",
        headers={"Authorization": f"Bearer {token}"}
    )
    print(response.get_json())  # Pour déboguer
    assert response.status_code == 200
    data = response.get_json()
    assert "valeur" in data
    assert data["valeur"] >= 1

def test_export_magasins_excel_success(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.get(
        "/staff/magasins/export",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

def test_get_stats_utilisation_success(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.get(
        "/staff/stats/utilisation",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["periode"] == "semaine"
    assert "totaux" in data
    assert "livraisons" in data["totaux"]

def test_get_utilisateurs_recents_success(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.get(
        "/staff/stats/utilisateurs-recents",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "utilisateurs_recents_connectes" in data
    assert len(data["utilisateurs_recents_connectes"]) >= 1
    assert any(u["email"] == "admin@test.com" for u in data["utilisateurs_recents_connectes"])

def test_get_top_magasins_success(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["staff_id"], role="staff")
    response = client.get(
        "/staff/stats/top-magasins",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "meilleurs_magasins" in data
    assert len(data["meilleurs_magasins"]) >= 1
    assert any(m["magasin_id"] == setup_magasins["magasin_id"] for m in data["meilleurs_magasins"])


def _auth(user_id, role):
    return {"Authorization": f"Bearer {generate_token(user_id=user_id, role=role)}"}


@pytest.mark.parametrize("url", [
    "/staff/magasins/export",
    "/staff/magasins/total",
    "/staff/admins-managers/total",
    "/staff/livreurs/total",
    "/staff/clients/total",
])
def test_platform_wide_routes_are_staff_only(client, setup_magasins, url):
    response = client.get(url, headers=_auth(setup_magasins["admin_id"], "admin"))
    assert response.status_code == 403


def test_update_accepts_blank_optional_fields(client, setup_magasins):
    magasin_id = setup_magasins["magasin_id"]
    response = client.put(
        f"/magasin/update/{magasin_id}",
        data={"denomination": "Test Magasin", "devise": "", "region": "", "country_code": ""},
        headers=_auth(setup_magasins["staff_id"], "staff"),
    )
    assert response.status_code == 200, response.get_json()
    with client.application.app_context():
        magasin = MagasinModel.get_magasin_by_id(magasin_id)
    assert magasin.get("devise") is None


def test_update_rejects_invalid_phone_with_400(client, setup_magasins):
    response = client.put(
        f"/magasin/update/{setup_magasins['magasin_id']}",
        json={"country_code": "CM", "telephone": "+23712345"},
        headers=_auth(setup_magasins["staff_id"], "staff"),
    )
    assert response.status_code == 400
    assert "telephone" in response.get_json()["errors"]


def test_register_accepts_multipart_with_logo(client, setup_magasins):
    form = {
        "denomination": "Boutique Multipart", "pays": "Cameroun", "country_code": "CM",
        "ville": "Douala", "quartier": "Akwa", "email": "multipart@test.com",
        "telephone": "+237699999999", "activité": "Commerce",
        "admin_name": "Nom", "admin_first_name": "Prenom", "admin_email": "multipart-admin@test.com",
        "devise": "", "accepte_conditions": "true",
        "image": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "logo.png", "image/png"),
    }
    with patch("services.magasin_service.send_registration_pending_email"):
        response = client.post("/magasin/register", data=form, content_type="multipart/form-data")
    assert response.status_code == 201, response.get_json()
    with client.application.app_context():
        magasin = MagasinModel.get_magasin_by_email("multipart@test.com")
        assert magasin["is_active"] is False
        assert magasin["logo"]
        os.remove(os.path.join(client.application.config["UPLOAD_FOLDER"], magasin["logo"]))
        UserModel.get_collection().delete_many({"email": "multipart-admin@test.com"})
        MagasinModel.get_collection().delete_many({"email": "multipart@test.com"})


def test_staff_can_read_archived_magasin(client, setup_magasins):
    magasin_id = setup_magasins["magasin_id"]
    headers = _auth(setup_magasins["staff_id"], "staff")
    assert client.delete(f"/magasin/delete/{magasin_id}", headers=headers).status_code == 200

    response = client.get(f"/magasin/{magasin_id}", headers=headers)
    assert response.status_code == 200
    assert response.get_json()["magasin"]["is_archived"] is True


def test_invitation_is_sent_only_on_first_activation_of_public_signup(client, setup_magasins):
    with client.application.app_context():
        magasin_id = str(ObjectId())
        MagasinModel.get_collection().insert_one({
            "_id": ObjectId(magasin_id), "denomination": "Inscription publique",
            "email": "public@test.com", "is_active": False, "created_by_staff": False,
            "licence": {"max_managers": 1, "max_livreurs": 1}, "created_at": datetime.utcnow(),
        })
        UserModel.get_collection().insert_one({
            "name": "Public", "first_name": "Admin", "email": "public-admin@test.com",
            "password": "initial", "role": "admin", "magasin_id": ObjectId(magasin_id),
            "is_active": False, "created_at": datetime.utcnow(),
        })
    headers = _auth(setup_magasins["staff_id"], "staff")
    url = f"/magasin/{magasin_id}/toggle-status"

    with patch("utils.email.send_lien_compte_email", return_value=True) as mail:
        assert client.put(url, headers=headers).status_code == 200  # 1re validation
        assert mail.call_count == 1
        assert mail.call_args.args[:2] == ("public-admin@test.com", "invitation")
        with client.application.app_context():
            first_password = UserModel.find_by_email("public-admin@test.com")["password"]
        assert client.put(url, headers=headers).status_code == 200  # désactivation
        assert client.put(url, headers=headers).status_code == 200  # réactivation
        assert mail.call_count == 1

    with client.application.app_context():
        admin = UserModel.find_by_email("public-admin@test.com")
        assert admin["is_active"] is True
        assert admin["password"] == first_password
        UserModel.get_collection().delete_many({"email": "public-admin@test.com"})
        MagasinModel.get_collection().delete_many({"_id": ObjectId(magasin_id)})


def test_reactivating_staff_created_store_keeps_admin_password(client, setup_magasins):
    magasin_id = setup_magasins["magasin_id"]
    with client.application.app_context():
        MagasinModel.get_collection().update_one(
            {"_id": ObjectId(magasin_id)}, {"$set": {"created_by_staff": True}}
        )
    headers = _auth(setup_magasins["staff_id"], "staff")
    with patch("utils.email.send_lien_compte_email") as mail:
        client.put(f"/magasin/{magasin_id}/toggle-status", headers=headers)
        client.put(f"/magasin/{magasin_id}/toggle-status", headers=headers)
    mail.assert_not_called()
    with client.application.app_context():
        assert UserModel.find_by_email("admin@test.com")["password"] == "hashed_password"


def test_neighborhoods_local_match_ignores_accents(client):
    response = client.get("/locations/neighborhoods?country_code=CM&city=yaounde")
    assert response.status_code == 200
    assert "Bastos" in response.get_json()


def test_neighborhoods_fall_back_to_openstreetmap(client):
    with patch("routes.location_routes._read_cache", return_value={}), \
         patch("routes.location_routes._write_cache_entry") as write, \
         patch("routes.location_routes._osm_neighborhoods", return_value=["Bolognina", "Navile"]):
        response = client.get("/locations/neighborhoods?country_code=IT&city=Bologna")
    assert response.status_code == 200
    assert response.get_json() == ["Bolognina", "Navile"]
    write.assert_called_once_with("IT|bologna", ["Bolognina", "Navile"])


# --- Page Paramètres : l'admin modifie la fiche du magasin, le manager la consulte.

def test_manager_ne_modifie_pas_le_magasin(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["manager_id"], role="manager")
    response = client.put(
        f"/magasin/update/{setup_magasins['magasin_id']}",
        json={"denomination": "Nouveau nom"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    magasin = MagasinModel.get_collection().find_one({"_id": ObjectId(setup_magasins["magasin_id"])})
    assert magasin["denomination"] == "Test Magasin"


def test_admin_modifie_le_magasin(client, setup_magasins):
    token = generate_token(user_id=setup_magasins["admin_id"], role="admin")
    response = client.put(
        f"/magasin/update/{setup_magasins['magasin_id']}",
        json={"denomination": "Nouveau nom", "licence": {"max_managers": 9, "max_livreurs": 9}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    magasin = MagasinModel.get_collection().find_one({"_id": ObjectId(setup_magasins["magasin_id"])})
    assert magasin["denomination"] == "Nouveau nom"
    assert magasin["licence"]["max_managers"] == 2  # licence réservée au staff


def test_personne_ne_desactive_son_propre_compte(client, setup_magasins):
    for role in ("admin", "manager"):
        user_id = setup_magasins[f"{role}_id"]
        token = generate_token(user_id=user_id, role=role)
        response = client.put(f"/users/deactivate-user/{user_id}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 400
        assert UserModel.get_collection().find_one({"_id": ObjectId(user_id)})["is_active"] is True


# --- Inscription publique (page « Créer mon magasin ») ----------------------

_INSCRIPTION = {
    "denomination": "Boutique Publique", "pays": "Cameroun", "country_code": "CM",
    "ville": "Douala", "quartier": "Akwa", "email": "publique@test.com",
    "telephone": "+237699999998", "activité": "Quincaillerie",
    "admin_name": "Mballa", "admin_first_name": "Awa", "admin_email": "publique-admin@test.com",
    "accepte_conditions": True,
}


def _nettoyer_inscription(app):
    with app.app_context():
        UserModel.get_collection().delete_many({"email": "publique-admin@test.com"})
        MagasinModel.get_collection().delete_many({"email": "publique@test.com"})


def test_inscription_publique_avec_profil_admin(client, app, setup_magasins):
    data = {**_INSCRIPTION, "admin_telephone": "+237 6 90 12 34 56", "admin_sexe": "Femme",
            "admin_fonction": "Gérante", "raison_sociale": "Boutique Publique SARL"}
    with patch("services.magasin_service.send_registration_pending_email"):
        r = client.post("/magasin/register", json=data)
    assert r.status_code == 201, r.get_json()
    with app.app_context():
        admin = UserModel.get_collection().find_one({"email": "publique-admin@test.com"})
        assert admin["telephone"] == "+237690123456" and admin["sexe"] == "Femme" and admin["is_active"] is False
        assert MagasinModel.get_magasin_by_email("publique@test.com")["licence"]["max_caissiers"] == 2
    _nettoyer_inscription(app)


def test_inscription_refusee_sans_conditions_ou_par_un_robot(client, app, setup_magasins):
    with patch("services.magasin_service.send_registration_pending_email"):
        r = client.post("/magasin/register", json={**_INSCRIPTION, "accepte_conditions": False})
        assert r.status_code == 400 and "accepte_conditions" in r.get_json()["errors"]
        r = client.post("/magasin/register", json={k: v for k, v in _INSCRIPTION.items() if k != "accepte_conditions"})
        assert r.status_code == 400
        r = client.post("/magasin/register", json={**_INSCRIPTION, "site_internet_societe": "http://spam"})
        assert r.status_code == 400
        r = client.post("/magasin/register", json={**_INSCRIPTION, "admin_telephone": "12"})
        assert r.status_code == 400
    with app.app_context():
        assert MagasinModel.get_magasin_by_email("publique@test.com") is None


def test_inscription_acceptee_si_le_navigateur_remplit_le_piege_avec_un_email(client, app, setup_magasins):
    """Chrome remplissait le champ anti-robots avec l'email de l'inscrit : ce
    n'est pas un robot, l'inscription doit passer."""
    with patch("services.magasin_service.send_registration_pending_email"):
        r = client.post("/magasin/register", json={**_INSCRIPTION, "site_internet_societe": "publique@test.com"})
        assert r.status_code == 201, r.get_json()
    with app.app_context():
        assert MagasinModel.get_magasin_by_email("publique@test.com") is not None
    _nettoyer_inscription(app)


def test_inscriptions_limitees_par_adresse_ip(client, app, setup_magasins):
    with patch("services.magasin_service.send_registration_pending_email"):
        statuts = [client.post("/magasin/register", json={**_INSCRIPTION, "email": f"spam{i}@test.com",
                                                           "admin_email": f"spam-admin{i}@test.com"}).status_code
                   for i in range(6)]
    assert statuts[:5] == [201] * 5 and statuts[5] == 429
    with app.app_context():
        for i in range(6):
            UserModel.get_collection().delete_many({"email": f"spam-admin{i}@test.com"})
            MagasinModel.get_collection().delete_many({"email": f"spam{i}@test.com"})
