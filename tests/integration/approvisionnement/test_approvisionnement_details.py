# test_approvisionnements_details.py


import pytest
from flask import json
from bson import ObjectId
from datetime import datetime
from extensions import db
from models.user_model import UserModel
from models.approvisionnement_model import ApprovisionnementModel
from models.produit_model import ProduitModel
from models.stock_livreur_model import StockLivreurModel
from utils.jwt_token import generate_token

@pytest.fixture
def setup_manager_and_approvisionnement(app):
    with app.app_context():
        magasin_id = "662c0cbf6b5a4a1f25facc22"
        # Le magasin doit exister et être actif, sinon la connexion est refusée (401).
        if not db.tables.magasin.find_one({"_id": ObjectId(magasin_id)}):
            db.tables.magasin.insert_one({"_id": ObjectId(magasin_id), "denomination": "Magasin test",
                                          "is_active": True, "created_at": datetime.utcnow()})
        manager_id = UserModel.get_collection().insert_one({
            "name": "Manager",
            "first_name": "Test",
            "email": "manager@test.com",
            "role": "manager",
            "is_active": True,
            "magasin_id": ObjectId(magasin_id),
            "created_at": datetime.utcnow()
        }).inserted_id
        livreur_id = UserModel.get_collection().insert_one({
            "name": "Livreur",
            "first_name": "Test",
            "email": "livreur@test.com",
            "role": "livreur",
            "is_active": True,
            "magasin_id": ObjectId(magasin_id),
            "created_at": datetime.utcnow()
        }).inserted_id
        produit_id = ProduitModel.get_collection().insert_one({
            "nom": "Savon",
            "prix": 1000,
            "quantite": 100,
            "magasin_id": ObjectId(magasin_id),
            "created_at": datetime.utcnow()
        }).inserted_id
        StockLivreurModel.get_collection().insert_one({
            "livreur_id": ObjectId(livreur_id),
            "produit_id": ObjectId(produit_id),
            "quantite": 10
        })
        approvisionnement_id = ApprovisionnementModel.get_collection().insert_one({
            "admin_id": ObjectId(manager_id),
            "livreur_id": ObjectId(livreur_id),
            "magasin_id": ObjectId(magasin_id),
            "produits": [
                {
                    "produit_id": ObjectId(produit_id),
                    "quantite": 10
                }
            ],
            "created_at": datetime.utcnow()
        }).inserted_id
        yield {
            "manager_id": str(manager_id),
            "livreur_id": str(livreur_id),
            "produit_id": str(produit_id),
            "magasin_id": magasin_id,
            "approvisionnement_id": str(approvisionnement_id)
        }
        db.tables.users.delete_many({"_id": {"$in": [ObjectId(manager_id), ObjectId(livreur_id)]}})
        db.tables.produits.delete_one({"_id": ObjectId(produit_id)})
        db.tables.approvisionnements.drop()
        db.tables.stock_livreur.drop()

def test_approvisionnement_details_success(client, setup_manager_and_approvisionnement):
    """
    Teste la récupération des détails d'un approvisionnement avec un livreur authentifié.
    """
    token = generate_token(user_id=setup_manager_and_approvisionnement["livreur_id"], role="livreur")
    response = client.get(
        f"/approvisionnements/{setup_manager_and_approvisionnement['approvisionnement_id']}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["id"] == setup_manager_and_approvisionnement["approvisionnement_id"]
    assert data["montant"] == 10000  # 10 * 1000
    assert data["quantite"] == 10
    assert data["produits"][0]["nom"] == "Savon"
    assert data["produits"][0]["quantite_livreur"] == 10
    assert "first_name" in data and "last_name" in data and data["created_at"].endswith("Z")

def test_approvisionnement_details_unauthorized(client, setup_manager_and_approvisionnement):
    """
    Teste l'accès aux détails d'un approvisionnement sans token JWT.
    """
    response = client.get(f"/approvisionnements/{setup_manager_and_approvisionnement['approvisionnement_id']}")
    assert response.status_code == 401
    assert "msg" in response.get_json()
    assert response.get_json()["msg"] == "Missing Authorization Header"

def test_approvisionnement_details_not_found(client, setup_manager_and_approvisionnement):
    """
    Teste l'accès aux détails d'un approvisionnement inexistant.
    """
    token = generate_token(user_id=setup_manager_and_approvisionnement["livreur_id"], role="livreur")
    response = client.get(
        "/approvisionnements/662b9c2c8f5b4a1e24688d1c",  # ID inexistant
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404
    assert response.get_json()["message"] == "Approvisionnement introuvable"
