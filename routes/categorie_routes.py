# routes/categorie_routes.py

from flask import Blueprint, request, jsonify
from utils.inject_magasin_id import inject_magasin_id
from utils.decorators import permission_requise, role_required
from flask_jwt_extended import jwt_required  # Import jwt_required
from bson import ObjectId
from schemas.categorie_schema import CreateCategorieSchema, UpdateCategorieSchema
from services.categorie_service import (
    create_categorie_service,
    get_categories_by_magasin_service,
    update_categorie_service,
    delete_categorie_service,
    get_categorie_details_service
)

categorie_bp = Blueprint("categorie", __name__, url_prefix="/categorie")


def _magasin_courant():
    """Magasin de l'utilisateur connecté, ou None s'il n'en a pas."""
    try:
        return inject_magasin_id({})["magasin_id"]
    except Exception:
        return None


#Cree une nouvelle catégorie dans un magasin


@categorie_bp.route("/create", methods=["POST"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def create_categorie():
    """
    Création d’une catégorie
    ---
    tags:
      - Catégories
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - nom
            - magasin_id
          properties:
            nom:
              type: string
              example: "Catégorie Exemple"
            description:
              type: string
              example: "Description de la catégorie"
            magasin_id:
              type: string
              example: "605c72ef1532072e7c32ed47"
    responses:
      201:
        description: Catégorie créée avec succès
      400:
        description: Erreur dans les données envoyées
    """
    data = request.get_json()
    data = inject_magasin_id(data)
    errors = CreateCategorieSchema().validate(data)
    if errors:
        return jsonify({"errors": errors}), 400
    return create_categorie_service(data)


# voir toute les catégories de produit d’un magasin

@categorie_bp.route("/", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def get_categories_by_magasin():
    """
    Récupération des catégories d’un magasin
    ---
    tags:
      - Catégories
    parameters:
      - in: path
        name: magasin_id
        required: true
        type: string
        description: ID du magasin
        example: "605c72ef1532072e7c32ed47"
    responses:
      200:
        description: Liste des catégories du magasin
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "605c72ef1532072e7c32ed48"
              nom:
                type: string
                example: "Catégorie Exemple"
              description:
                type: string
                example: "Description de la catégorie"
              magasin_id:
                type: string
                example: "605c72ef1532072e7c32ed47"
    responses:
      404:
        description: Magasin non trouvé
    """
    data = {}
    data = inject_magasin_id(data)
    magasin_id = data.get("magasin_id")
    categories = get_categories_by_magasin_service(magasin_id)
    from services.referentiels_service import compter_produits
    nombres = compter_produits(magasin_id, "categorie_id")
    for cat in categories:
        cat["_id"] = str(cat["_id"])
        cat["magasin_id"] = str(cat["magasin_id"])
        cat["nb_produits"] = nombres.get(cat["_id"], 0)
    return jsonify(categories), 200


# Mettre à jour une catégorie

@categorie_bp.route("/<categorie_id>", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager", "livreur")
@permission_requise("produits.modifier")
def update_categorie(categorie_id):
    """
    Mise à jour d’une catégorie
    ---
    tags:
      - Catégories
    parameters:
      - in: path
        name: categorie_id
        required: true
        type: string
        description: ID de la catégorie à mettre à jour
        example: "605c72ef1532072e7c32ed48"
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - nom
          properties:
            nom:
              type: string
              example: "Catégorie Exemple Modifiée"
            description:
              type: string
              example: "Nouvelle description"
    responses:
      200:
        description: Catégorie mise à jour avec succès
      400:
        description: Erreur dans les données envoyées
    """
    data = request.get_json()
    errors = UpdateCategorieSchema().validate(data)
    if errors:
        return jsonify({"errors": errors}), 400

    magasin_id = _magasin_courant()
    if not magasin_id:
        return jsonify({"message": "Catégorie non trouvée"}), 404
    reponse, statut = update_categorie_service(categorie_id, magasin_id, data)
    return jsonify(reponse), statut


@categorie_bp.route("/<categorie_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager", "livreur")
@permission_requise("produits.modifier")
def delete_categorie(categorie_id):
    """
    Suppression d’une catégorie
    ---
    tags:
      - Catégories
    parameters:
      - in: path
        name: categorie_id
        required: true
        type: string
        description: ID de la catégorie à supprimer
        example: "605c72ef1532072e7c32ed48"
    responses:
      200:
        description: Catégorie supprimée avec succès
      404:
        description: Catégorie non trouvée
    """
    magasin_id = _magasin_courant()
    if not magasin_id:
        return jsonify({"message": "Catégorie non trouvée"}), 404
    reponse, statut = delete_categorie_service(categorie_id, magasin_id)
    return jsonify(reponse), statut


# Détails d'un catégorie
@categorie_bp.route("/<categorie_id>", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur", "staff")
def get_categorie_details(categorie_id):
    """
    Récupération des détails d’une catégorie
    ---
    tags:
      - Catégories
    parameters:
      - in: path
        name: categorie_id
        required: true
        type: string
        description: ID de la catégorie à récupérer
        example: "605c72ef1532072e7c32ed48"
    responses:
      200:
        description: Détails de la catégorie récupérés avec succès
      404:
        description: Catégorie non trouvée
    """
    from flask_jwt_extended import get_jwt
    from models.categorie_model import CategorieModel
    if get_jwt().get("role") == "staff":
        # Le staff de la plateforme consulte les catégories de tous les magasins.
        categorie = CategorieModel.get_by_id(categorie_id) if ObjectId.is_valid(str(categorie_id)) else None
    else:
        categorie = CategorieModel.get_for_magasin(categorie_id, _magasin_courant())
    if not categorie:
        return jsonify({"message": "Catégorie non trouvée"}), 404

    categorie["_id"] = str(categorie["_id"])
    categorie["magasin_id"] = str(categorie["magasin_id"])
    return jsonify(categorie), 200