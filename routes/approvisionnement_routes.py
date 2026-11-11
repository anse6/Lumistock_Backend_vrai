# routes/approvisionnement_routes.py

from flask import Blueprint, request, jsonify
from services.approvisionnement_service import (
  approvisionner_livreur,
  get_approvisionner_service,
  delete_approvisionnement_service,
  get_approvisionnement_details_service,
  update_approvisionnement_service,
  restaurer_approvisionnement_service,
  get_corbeille_approvisionner_service,
  supprimer_definitivement_approvisionnement_service
  )
from flask_jwt_extended import jwt_required, get_jwt_identity
from utils.inject_magasin_id import inject_magasin_id
from utils.decorators import role_required
from utils.decorators import permission_requise
from flask_jwt_extended import get_jwt
from models.approvisionnement_model import ApprovisionnementModel



approvisionnement_bp = Blueprint("approvisionnement", __name__)


def _magasin_courant():
    try:
        return inject_magasin_id({})["magasin_id"]
    except Exception:
        return None


def _verifier_contenu(data):
    """Le gérant et tous les produits doivent appartenir au magasin de l'utilisateur."""
    from bson import ObjectId
    from models.user_model import UserModel
    from models.produit_model import ProduitModel

    magasin_id = str(data.get("magasin_id", ""))
    livreur_id = str(data.get("livreur_id", ""))
    gerant = UserModel.get_user_by_id(livreur_id) if ObjectId.is_valid(livreur_id) else None
    if not gerant or gerant.get("role") not in ("gerant", "livreur") or str(gerant.get("magasin_id")) != magasin_id:
        return jsonify({"error": "Gérant introuvable dans votre magasin."}), 400
    for produit in data.get("produits") or []:
        if not isinstance(produit, dict):
            continue
        if not ProduitModel.get_for_magasin(produit.get("produit_id"), magasin_id):
            return jsonify({"error": f"Article {produit.get('produit_id')} introuvable dans votre magasin."}), 400
    return None


def _verifier_approvisionnement(approvisionnement_id, in_corbeille=None):
    """Vérifie que l'approvisionnement appartient au magasin de l'utilisateur.

    Le staff (sans magasin) peut consulter n'importe quel approvisionnement.
    Un approvisionnement d'un autre magasin répond 404, comme s'il n'existait pas.
    :return: None si l'accès est autorisé, sinon la réponse d'erreur.
    """
    if get_jwt().get("role") == "staff":
        return None
    magasin_id = _magasin_courant()
    if not magasin_id:
        return jsonify({"message": "Aucun magasin n'est associé à cet utilisateur."}), 403
    if not ApprovisionnementModel.get_for_magasin(approvisionnement_id, magasin_id, in_corbeille):
        message = "Approvisionnement introuvable dans la corbeille" if in_corbeille else "Approvisionnement introuvable"
        return jsonify({"message": message}), 404
    return None



  
  
@approvisionnement_bp.route("/approvisionnements", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("stock.approvisionner")
def approvisionner():
    """
    Approvisionner un livreur
    ---
    tags:
      - Approvisionnement
    summary: Approvisionner un livreur en produits
    description: >
      Cette route permet à un administrateur ou un manager d’approvisionner un livreur avec une liste de produits
      (identifiés par leurs IDs et quantités). Le `admin_id` est automatiquement injecté depuis le token JWT.

    parameters:
      - in: header
        name: Authorization
        required: true
        type: string
        description: Jeton JWT au format **Bearer &lt;token&gt;**
        default: Bearer
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - livreur_id
            - produits
          properties:
            livreur_id:
              type: string
              description: ID du livreur à approvisionner (24 caractères)
              example: "662b9cf48f5b4a1e24688d20"
            produits:
              type: array
              description: Liste des produits à approvisionner avec leur quantité
              items:
                type: object
                required:
                  - produit_id
                  - quantite
                properties:
                  produit_id:
                    type: string
                    description: ID du produit à approvisionner (24 caractères)
                    example: "662b9da88f5b4a1e24688d21"
                  quantite:
                    type: integer
                    minimum: 1
                    description: Quantité à approvisionner
                    example: 10

    responses:
      201:
        description: Approvisionnement effectué avec succès
      400:
        description: Données invalides ou requête mal formulée
      401:
        description: Non autorisé – Token manquant ou invalide
      403:
        description: Accès interdit – Rôle insuffisant
      500:
        description: Erreur serveur lors du traitement
    """
    data = request.get_json(silent=True) or {}
    data["admin_id"] = get_jwt_identity()
    data = inject_magasin_id(data)
    erreur = _verifier_contenu(data)
    if erreur:
        return erreur
    response, status = approvisionner_livreur(data)
    return jsonify(response), status



@approvisionnement_bp.route("/approvisionnements", methods=["GET"])
@role_required("admin", "manager", "staff")
@jwt_required()
def get_approvisionner():
    """
    Récupérer la liste des approvisionnements
    ---
    tags:
      - Approvisionnement
    summary: Liste des approvisionnements
    description: >
      Cette route permet aux utilisateurs ayant le rôle **admin**, **manager** ou **staff** de récupérer
      l’historique des approvisionnements effectués. Le système filtre automatiquement les résultats
      selon l'identité de l'utilisateur connecté via le token JWT.

    parameters:
      - in: header
        name: Authorization
        required: true
        type: string
        description: Jeton JWT au format **Bearer &lt;token&gt;**
        default: Bearer

    responses:
      200:
        description: Liste des approvisionnements récupérée avec succès
        schema:
          type: object
          properties:
            approvisionnements:
              type: array
              items:
                type: object
                properties:
                  _id:
                    type: string
                    example: "662c0d4f6b5a4a1f25facc2b"
                  livreur_id:
                    type: string
                    example: "662b9cf48f5b4a1e24688d20"
                  produits:
                    type: array
                    items:
                      type: object
                      properties:
                        produit_id:
                          type: string
                          example: "662b9da88f5b4a1e24688d21"
                        quantite:
                          type: integer
                          example: 10
                  date:
                    type: string
                    format: date-time
                    example: "2025-07-11T10:30:00Z"
      401:
        description: Non autorisé – Token manquant ou invalide
      403:
        description: Accès interdit – Rôle insuffisant
      500:
        description: Erreur serveur lors de la récupération des approvisionnements
    """
    response, status = get_approvisionner_service(_magasin_courant())
    return jsonify(response), status

@approvisionnement_bp.route("/approvisionnements/corbeille", methods=["GET"])
@role_required("admin", "manager")
@jwt_required()
def get_corbeille_approvisionnement():
    """
    Récupérer les approvisionnements supprimés (Corbeille)
    """
    response, status = get_corbeille_approvisionner_service(_magasin_courant())
    return jsonify(response), status

@approvisionnement_bp.route("/approvisionnements/corbeille/<string:approvisionnement_id>/restaurer", methods=["POST"])
@role_required("admin", "manager")
@permission_requise("stock.approvisionner")
@jwt_required()
def restaurer_approvisionnement(approvisionnement_id):
    """
    Restaurer un approvisionnement supprimé
    """
    erreur = _verifier_approvisionnement(approvisionnement_id, in_corbeille=True)
    if erreur:
        return erreur
    response, status = restaurer_approvisionnement_service(approvisionnement_id)
    return jsonify(response), status

@approvisionnement_bp.route("/approvisionnements/corbeille/<string:approvisionnement_id>/definitive", methods=["DELETE"])
@role_required("admin", "manager")
@permission_requise("stock.approvisionner")
@jwt_required()
def delete_permanently_approvisionnement(approvisionnement_id):
    """
    Supprimer définitivement un approvisionnement de la corbeille
    """
    erreur = _verifier_approvisionnement(approvisionnement_id, in_corbeille=True)
    if erreur:
        return erreur
    response, status = supprimer_definitivement_approvisionnement_service(approvisionnement_id)
    return jsonify(response), status



# Suppression d'un approvisionnement
@approvisionnement_bp.route("/approvisionnements/<string:approvisionnement_id>", methods=["DELETE"])
@role_required("admin", "manager", "staff")
@permission_requise("stock.approvisionner")
@jwt_required()
def delete_approvisionnement(approvisionnement_id):
    """
    Supprimer un approvisionnement
    ---
    tags:
      - Approvisionnement
    description: "Permet à un admin, manager ou staff de supprimer un approvisionnement."
    parameters:
      - in: path
        name: approvisionnement_id
        required: true
        type: string
        description: "ID de l’approvisionnement à supprimer"
      - in: header
        name: Authorization
        required: true
        type: string
        description: "Jeton JWT au format **Bearer &lt;token&gt;**"
        default: Bearer
    responses:
      200:
        description: "Approvisionnement supprimé avec succès"
      404:
        description: "Approvisionnement non trouvé"
    """
    erreur = _verifier_approvisionnement(approvisionnement_id, in_corbeille=False)
    if erreur:
        return erreur
    response, status = delete_approvisionnement_service(approvisionnement_id)
    return jsonify(response), status
  
  

# détails d'un approvisionnement
@approvisionnement_bp.route("/approvisionnements/<string:approvisionnement_id>", methods=["GET"])
@role_required("admin", "manager", "staff", "livreur")
@jwt_required()
def get_approvisionnement_details(approvisionnement_id):
    """
    Détails d’un approvisionnement
    ---
    tags:
      - Approvisionnement
    summary: Détails d’un approvisionnement spécifique
    description: >
      Cette route permet de récupérer les détails complets d’un approvisionnement à partir de son identifiant.
      Accessible aux rôles : **admin**, **manager**, **staff**, **livreur**.

    parameters:
      - name: approvisionnement_id
        in: path
        required: true
        type: string
        description: ID de l’approvisionnement à consulter (24 caractères)
        example: "662b9c2c8f5b4a1e24688d1c"
      - in: header
        name: Authorization
        required: true
        type: string
        description: Jeton JWT au format **Bearer &lt;token&gt;**
        default: Bearer

    responses:
      200:
        description: Approvisionnement trouvé avec succès
        schema:
          type: object
          properties:
            _id:
              type: string
              example: "662b9c2c8f5b4a1e24688d1c"
            livreur_id:
              type: string
              example: "662b9cf48f5b4a1e24688d20"
            produits:
              type: array
              items:
                type: object
                properties:
                  produit_id:
                    type: string
                    example: "662b9da88f5b4a1e24688d21"
                  quantite:
                    type: integer
                    example: 10
            date:
              type: string
              format: date-time
              example: "2025-07-11T10:30:00Z"
      404:
        description: Approvisionnement non trouvé
      401:
        description: Non autorisé – Token manquant ou invalide
      403:
        description: Accès interdit – Rôle insuffisant
      500:
        description: Erreur serveur lors de la récupération des détails
    """
    erreur = _verifier_approvisionnement(approvisionnement_id)
    if erreur:
        return erreur
    # Le service renvoie (données, code) : on renvoie un objet JSON et le vrai code.
    response, status = get_approvisionnement_details_service(approvisionnement_id)
    return jsonify(response), status

# modification d'un approvisionnement
@approvisionnement_bp.route("/approvisionnements/<string:approvisionnement_id>", methods=["PUT"])
@role_required("admin", "manager")
@permission_requise("stock.approvisionner")
@jwt_required()
def update_approvisionnement(approvisionnement_id):
    """
    Modifier un approvisionnement
    ---
    tags:
      - Approvisionnement
    summary: Modifier un approvisionnement existant
    description: >
      Permet de modifier les quantités ou les produits d'un approvisionnement.
      Cette opération mettra à jour automatiquement le stock du livreur et du magasin en calculant
      les différences (deltas) entre les anciennes et les nouvelles quantités.
    
    parameters:
      - name: approvisionnement_id
        in: path
        required: true
        type: string
        description: ID de l’approvisionnement à modifier
      - in: header
        name: Authorization
        required: true
        type: string
        description: Jeton JWT au format **Bearer &lt;token&gt;**
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            livreur_id:
              type: string
            produits:
              type: array
              items:
                type: object
                properties:
                  produit_id:
                    type: string
                  quantite:
                    type: integer
    responses:
      200:
        description: Approvisionnement modifié avec succès
      400:
        description: Données invalides (ex: stock insuffisant)
      404:
        description: Approvisionnement introuvable
    """
    erreur = _verifier_approvisionnement(approvisionnement_id, in_corbeille=False)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    data["admin_id"] = get_jwt_identity()
    data = inject_magasin_id(data)
    erreur = _verifier_contenu(data)
    if erreur:
        return erreur
    response, status = update_approvisionnement_service(approvisionnement_id, data)
    return jsonify(response), status
