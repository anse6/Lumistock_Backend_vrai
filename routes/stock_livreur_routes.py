from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from utils.decorators import role_required
from services.stock_livreur_service import (
    get_stock_par_categorie_pour_livreur,
    get_stock_livreur_avec_details,
    get_details_produit_livreur,
    retourner_stock_magasin
)

stock_livreur_bp = Blueprint("stock_livreur", __name__)

@stock_livreur_bp.route("/retour-magasin", methods=["POST"])
@jwt_required()
@role_required("livreur")
def retour_magasin():
    """
    Retourner des produits au stock du magasin
    ---
    tags:
      - Stock Livreur
    summary: Retour de produits invendus au magasin
    description: Permet à un livreur de retourner des produits de son stock personnel vers le stock du magasin.
    
    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT du livreur (format **Bearer &lt;token&gt;**)
      - in: body
        name: body
        required: true
        description: Liste des produits à retourner
        schema:
          type: object
          properties:
            produits:
              type: array
              items:
                type: object
                properties:
                  produit_id:
                    type: string
                    example: "64b8fae5e3b9ab9c8dbf8f67"
                  quantite:
                    type: integer
                    example: 5

    responses:
      200:
        description: Produits retournés avec succès
      400:
        description: Données invalides ou stock insuffisant
      401:
        description: Non autorisé
      403:
        description: Accès refusé
      500:
        description: Erreur serveur
    """
    data = request.get_json()
    produits = data.get("produits", [])
    
    livreur_id = get_jwt_identity()
    result, status_code = retourner_stock_magasin(livreur_id, produits)
    return jsonify(result), status_code
