# tests/integration/test_dashboard.py


import pytest
from flask import json
from bson import ObjectId
from datetime import datetime
from extensions import db
from models.user_model import UserModel
from models.livraison_model import LivraisonModel
from utils.jwt_token import generate_token

@pytest.fixture
def setup_manager_and_livraisons(app):
    with app.app_context():
        magasin_id = "662c0cbf6b5a4a1f25facc22"
        manager_id = UserModel.get_collection().insert_one({
            "name": "Manager",
            "first_name": "Test",
            "email": "manager@test.com",
            "role": "manager",
            "magasin_id": ObjectId(magasin_id),
            "created_at": datetime.utcnow()
        }).inserted_id
        # Insérer exactement deux livraisons pour le test
        livraison_1_id = LivraisonModel.get_collection().insert_one({
            "manager_id": ObjectId(manager_id),
            "magasin_id": ObjectId(magasin_id),
            "statut_livraison": "en cours",
            "created_at": datetime.utcnow()
        }).inserted_id
        livraison_2_id = LivraisonModel.get_collection().insert_one({
            "manager_id": ObjectId(manager_id),
            "magasin_id": ObjectId(magasin_id),
            "statut_livraison": "livrée",
            "created_at": datetime.utcnow()
        }).inserted_id
        yield {
            "manager_id": str(manager_id),
            "magasin_id": magasin_id
        }
        # Nettoyage de la base
        db.tables.users.delete_one({"_id": ObjectId(manager_id)})
        db.tables.livraisons.delete_many({"magasin_id": ObjectId(magasin_id)})

def test_get_livraisons_success(client, setup_manager_and_livraisons):
    """
    Teste la récupération du nombre de livraisons avec un manager authentifié.
    """
    # Débogage : Vérifier le nombre de livraisons dans la base avant la requête
    magasin_id = ObjectId(setup_manager_and_livraisons["magasin_id"])
    livraisons_count = db.tables.livraisons.count_documents({"magasin_id": magasin_id})
    print(f"[DEBUG] Nombre de livraisons dans la base pour magasin_id={magasin_id}: {livraisons_count}")

    token = generate_token(user_id=setup_manager_and_livraisons["manager_id"], role="manager")
    response = client.get(
        "/managers/stats/livraisons",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "valeur" in data
    assert data["valeur"] == 2, f"Attendu 2 livraisons, mais obtenu {data['valeur']}"

def test_get_livraisons_unauthorized(client, setup_manager_and_livraisons):
    """
    Teste l'accès à la statistique des livraisons sans token JWT.
    """
    response = client.get("/managers/stats/livraisons")
    assert response.status_code == 401
    assert "msg" in response.get_json()
    assert response.get_json()["msg"] == "Missing Authorization Header"

def test_get_livraisons_forbidden(client, setup_manager_and_livraisons):
    """
    Teste l'accès à la statistique des livraisons avec un rôle non autorisé (livreur).
    """
    token = generate_token(user_id=setup_manager_and_livraisons["manager_id"], role="livreur")
    response = client.get(
        "/managers/stats/livraisons",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert "msg" in response.get_json()
    assert response.get_json()["msg"] == "Accès interdit pour le rôle actuel."
