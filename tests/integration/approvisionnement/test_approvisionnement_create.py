# test_approvisionement_create.py

import pytest
from flask import json
from bson import ObjectId
from datetime import datetime
from extensions import db
from models.user_model import UserModel
from models.produit_model import ProduitModel
from models.stock_livreur_model import StockLivreurModel
from utils.jwt_token import generate_token

@pytest.fixture
def setup_manager_and_livreur(app):
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
        livreur_id = UserModel.get_collection().insert_one({
            "name": "Livreur",
            "first_name": "Test",
            "email": "livreur@test.com",
            "role": "livreur",
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
        yield {
            "manager_id": str(manager_id),
            "livreur_id": str(livreur_id),
            "produit_id": str(produit_id),
            "magasin_id": magasin_id
        }
        db.tables.users.delete_many({"_id": {"$in": [ObjectId(manager_id), ObjectId(livreur_id)]}})
        db.tables.produits.delete_one({"_id": ObjectId(produit_id)})
        db.tables.approvisionnements.drop()
        db.tables.stock_livreur.drop()

def test_approvisionnement_create_success(client, setup_manager_and_livreur):
    """
    Teste la création d'un approvisionnement avec un manager authentifié.
    """
    token = generate_token(user_id=setup_manager_and_livreur["manager_id"], role="manager")
    payload = {
        "livreur_id": setup_manager_and_livreur["livreur_id"],
        "produits": [
            {
                "produit_id": setup_manager_and_livreur["produit_id"],
                "quantite": 10
            }
        ]
    }
    response = client.post(
        "/approvisionnements",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 201
    data = response.get_json()
    assert "message" in data
    assert data["message"] == "Approvisionnement réussi"
    assert "approvisionnement_id" in data

def test_approvisionnement_create_unauthorized(client, setup_manager_and_livreur):
    """
    Teste la création d'un approvisionnement sans token JWT.
    """
    payload = {
        "livreur_id": setup_manager_and_livreur["livreur_id"],
        "produits": [
            {
                "produit_id": setup_manager_and_livreur["produit_id"],
                "quantite": 10
            }
        ]
    }
    response = client.post("/approvisionnements", json=payload)
    assert response.status_code == 401
    assert "msg" in response.get_json()
    assert response.get_json()["msg"] == "Missing Authorization Header"

def test_approvisionnement_create_forbidden(client, setup_manager_and_livreur):
    """
    Teste la création d'un approvisionnement avec un token de livreur (non autorisé).
    """
    token = generate_token(user_id=setup_manager_and_livreur["livreur_id"], role="livreur")
    payload = {
        "livreur_id": setup_manager_and_livreur["livreur_id"],
        "produits": [
            {
                "produit_id": setup_manager_and_livreur["produit_id"],
                "quantite": 10
            }
        ]
    }
    response = client.post(
        "/approvisionnements",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert "msg" in response.get_json()
    assert response.get_json()["msg"] == "Accès interdit pour le rôle actuel."

def test_approvisionnement_create_invalid_data(client, setup_manager_and_livreur):
    """
    Teste la création d'un approvisionnement avec des données invalides.
    """
    token = generate_token(user_id=setup_manager_and_livreur["manager_id"], role="manager")
    payload = {
        "livreur_id": setup_manager_and_livreur["livreur_id"],
        "produits": [
            {
                "produit_id": "invalid_id",  # ID invalide
                "quantite": 10
            }
        ]
    }
    response = client.post(
        "/approvisionnements",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400
    assert "error" in response.get_json()
