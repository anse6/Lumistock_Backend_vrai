# routes/livraison_routes.py
import os
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity  # Import jwt_required and get_jwt_identity
from services.livraison_service import (
  creer_livraison, 
  creer_livraison_by_manager, 
  modifier_livraison_by_manager_service,
  modifier_livraison_by_livreur_service,
  get_livraison_livreur,
  get_livraisons_recentes_par_livreur,
  details_livraison_service,
  ajouter_paiement_service,
  get_livraisons_filtrees_par_livreur,
  get_livraisons_recentes_total,
  get_livraison_managers,
  supprimer_livraison_service,
  rembourser_service,
  accorder_ristourne_service,
  valider_ristourne_service,
  refuser_abandon_service,
  abandonner_reste_service,
  demander_abandon_service,
  get_livraisons_par_methode_paiement_service,
  restaurer_livraison_service,
  supprimer_definitivement_livraison_service
)

from services.retour_service import (
  creer_retour_livraison
)
from models.user_model import UserModel
from services.export_files_services import export_livraisons_to_excel
from utils.decorators import role_required, livreur_required  # Import the role_required decorator
from utils.decorators import permission_requise
from utils.inject_magasin_id import inject_magasin_id  # Import the inject_magasin_id function
from models.livraison_model import LivraisonModel

livraison_bp = Blueprint("livraison", __name__)


from bson import ObjectId
from flask_jwt_extended import get_jwt


def _magasin_courant():
    try:
        return str(inject_magasin_id({})["magasin_id"])
    except Exception:
        return None


@livraison_bp.route("/livraisons/tva", methods=["GET"])
@jwt_required()
def tva_magasin():
    """TVA applicable aux ventes du magasin courant : pays, code ISO et taux (%)."""
    from services.tva_service import tva_du_magasin
    return jsonify(tva_du_magasin(_magasin_courant())), 200


def _verifier_livraison(livraison_id, in_corbeille=None, manager_seulement=False):
    """Contrôle d'accès à une livraison.

    - le staff accède à toutes les livraisons ;
    - admin/manager : uniquement les livraisons de leur magasin ;
    - gérant : uniquement ses propres livraisons.
    Une livraison inaccessible répond 404, comme si elle n'existait pas.
    :param manager_seulement: refuse une livraison faite par un gérant (stock du gérant).
    :return: None si l'accès est autorisé, sinon la réponse d'erreur.
    """
    from services.livraison_service import est_livraison_manager

    if not livraison_id or not ObjectId.is_valid(str(livraison_id)):
        return jsonify({"error": "Livraison introuvable"}), 404
    query = {"_id": ObjectId(str(livraison_id))}
    if in_corbeille is not None:
        query["deleted_at"] = {"$exists": in_corbeille}
    livraison = LivraisonModel.get_collection().find_one(query)
    if not livraison:
        return jsonify({"error": "Livraison introuvable"}), 404
    role = get_jwt().get("role")
    if role != "staff":
        if str(livraison.get("magasin_id")) != (_magasin_courant() or ""):
            return jsonify({"error": "Livraison introuvable"}), 404
        if role in ("livreur", "gerant") and str(livraison.get("livreur_id")) != str(get_jwt_identity()):
            return jsonify({"error": "Livraison introuvable"}), 404
    if manager_seulement and not est_livraison_manager(livraison):
        return jsonify({
            "error": "Cette livraison a été faite par un gérant depuis son propre stock : "
                     "elle doit être modifiée depuis le compte de ce gérant."
        }), 409
    return None


def _verifier_contenu(data, magasin_id):
    """Les produits et le client d'une livraison doivent appartenir au magasin."""
    from models.produit_model import ProduitModel
    from models.client_model import ClientModel

    for produit in (data or {}).get("produits") or []:
        if isinstance(produit, dict) and not ProduitModel.get_for_magasin(produit.get("produit_id"), magasin_id):
            return jsonify({"error": f"Article {produit.get('produit_id')} introuvable dans votre magasin."}), 400
    client_id = (data or {}).get("client_id")
    if client_id:
        client = ClientModel.get_client_by_id(client_id) if ObjectId.is_valid(str(client_id)) else None
        if not client or str(client.get("magasin_id")) != str(magasin_id):
            return jsonify({"error": "Client introuvable dans votre magasin."}), 400
    return None



# Création d'une livraison par un livreur

@livraison_bp.route("/livraisons", methods=["POST"])
@jwt_required()
@role_required("livreur")
def create_livraison():
    """
    Créer une nouvelle livraison
    ---
    tags:
      - Livraison
    summary: Création d'une livraison par un livreur
    description: >
      Cette route permet à un livreur de créer une livraison pour un client donné.  
      Le corps de la requête peut contenir des produits avec ou sans conditionnements.  
      Le `livreur_id` est automatiquement injecté via le token JWT.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT (format **Bearer &lt;token&gt;**)
        default: Bearer
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - client_id
            - produits
            - statut_paiement
          properties:
            client_id:
              type: string
              description: ID du client (24 caractères)
              example: "685549298446e72ca186781f"
            produits:
              type: array
              description: Liste des produits livrés (avec ou sans conditionnements)
              items:
                type: object
                properties:
                  produit_id:
                    type: string
                    description: ID du produit
                    example: "686bc0063dd604cf9e7849d6"
                  conditionnement_id:
                    type: string
                    description: ID du conditionnement (si utilisé)
                    example: "686f805719b879317c76c977"
                  quantite_conditionnement:
                    type: integer
                    description: Quantité livrée sous forme de conditionnement
                    example: 2
                  quantite:
                    type: integer
                    description: Quantité unitaire
                    example: 5
                  prix:
                    type: number
                    description: Prix unitaire
                    example: 1000
            statut_paiement:
              type: string
              enum: ["non payé", "payé", "partiellement payé"]
              example: "payé"
            statut_livraison:
              type: string
              enum: ["en cours", "livrée", "annulée"]
              default: "en cours"
              example: "en cours"
            date_commande:
              type: string
              format: date-time
              example: "2025-04-25T10:00:00"
            date_livraison:
              type: string
              format: date-time
              example: "2025-04-26T12:00:00"
            montant_paye:
              type: number
              example: 5000.0
            relicat:
              type: number
              example: 1000.0
            magasin_id:
              type: string
              description: ID du magasin (24 caractères)
              example: "662c0cbf6b5a4a1f25facc22"

          example:
            client_id: "685549298446e72ca186781f"
            produits:
              - produit_id: "686bc0063dd604cf9e7849d6"
                conditionnement_id: "686f805719b879317c76c977"
                quantite_conditionnement: 2
              - produit_id: "686bc0063dd604cf9e7849d6"
                quantite: 5
                prix: 1000
            statut_paiement: "payé"

    responses:
      201:
        description: Livraison créée avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Livraison créée avec succès"
            livraison_id:
              type: string
              example: "662d4f6a6b5a4a1f25face10"
      400:
        description: Données invalides ou incomplètes
      401:
        description: Non autorisé – Token manquant ou invalide
      403:
        description: Accès refusé – Rôle non autorisé (réservé aux livreurs)
      500:
        description: Erreur serveur lors de la création
    """
    data = request.get_json()
    if not data:
        return jsonify({"message": "Aucune donnée fournie"}), 400
        
    data = inject_magasin_id(data)
    data["livreur_id"] = get_jwt_identity()
    erreur = _verifier_contenu(data, data["magasin_id"])
    if erreur:
        return erreur

    result, status_code = creer_livraison(data)
    return jsonify(result), status_code

  

@livraison_bp.route("/admin_or_manager/livraisons", methods=["POST"])
@role_required("manager", "admin")
@jwt_required()
def create_livraison_by_manager():
    """
    Créer une livraison par un manager ou un administrateur
    ---
    tags:
      - Livraison
    summary: Création d'une livraison (Manager/Admin)
    description: >
      Cette route permet à un manager ou un administrateur de créer une livraison  
      en spécifiant les détails de la commande, le client, le livreur, les produits, le paiement, etc.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT (format **Bearer &lt;token&gt;**)
        default: Bearer
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - client_id
            - livreur_id
            - produits
            - statut_paiement
            - date_livraison
          properties:
            client_id:
              type: string
              description: ID du client (24 caractères)
              example: "662c0d4f6b5a4a1f25facc2b"
            livreur_id:
              type: string
              description: ID du livreur affecté à la livraison (24 caractères)
              example: "662c0cbf6b5a4a1f25facc29"
            produits:
              type: array
              description: Liste des produits livrés
              items:
                type: object
                properties:
                  produit_id:
                    type: string
                    description: ID du produit
                    example: "662c0e1e6b5a4a1f25facc2c"
                  conditionnement_id:
                    type: string
                    description: ID du conditionnement utilisé (si applicable)
                    example: "686f805719b879317c76c977"
                  quantite_conditionnement:
                    type: integer
                    example: 2
                  quantite:
                    type: integer
                    example: 5
                  prix:
                    type: number
                    example: 3000
              example:
                - produit_id: "662c0e1e6b5a4a1f25facc2c"
                  quantite: 5
                  prix: 3000
                - produit_id: "662c0e1e6b5a4a1f25facc2c"
                  conditionnement_id: "686f805719b879317c76c977"
                  quantite_conditionnement: 2
            statut_paiement:
              type: string
              enum: ["non payé", "payé", "partiellement payé"]
              example: "payé"
            statut_livraison:
              type: string
              enum: ["en cours", "livrée", "annulée"]
              example: "en cours"
            date_commande:
              type: string
              format: date-time
              example: "2025-04-25T10:00:00"
            date_livraison:
              type: string
              format: date-time
              example: "2025-04-26T12:00:00"
            montant_paye:
              type: number
              example: 5000
            relicat:
              type: number
              example: 1000
            magasin_id:
              type: string
              description: ID du magasin d’où part la livraison
              example: "662c0cbf6b5a4a1f25facc22"

    responses:
      201:
        description: Livraison créée avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Livraison créée avec succès"
            livraison_id:
              type: string
              example: "662d4f6a6b5a4a1f25face10"
      400:
        description: Données invalides ou manquantes
      401:
        description: Non autorisé – Token manquant ou invalide
      403:
        description: Accès refusé – Réservé aux rôles manager/admin
      500:
        description: Erreur interne lors de la création de la livraison
    """
    data = request.get_json(silent=True) or {}
    data = inject_magasin_id(data)
    data["manager_id"] = get_jwt_identity()
    erreur = _verifier_contenu(data, data["magasin_id"])
    if erreur:
        return erreur
    result, status_code = creer_livraison_by_manager(data)
    return jsonify(result), status_code


@livraison_bp.route("/admin_or_manager/livraisons/<string:livraison_id>", methods=["PUT"])
@role_required("manager", "admin")
@permission_requise("ventes.modifier")
@jwt_required()
def modifier_livraison_by_manager(livraison_id):
    """
    Modifier une livraison existante (Manager/Admin)
    ---
    tags:
      - Livraison
    summary: Modification d'une livraison (Manager/Admin)
    description: >
      Permet à un manager ou administrateur de modifier les produits, le statut, 
      le paiement, etc. d'une livraison. Gère la restitution du stock si besoin.
    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT (format **Bearer &lt;token&gt;**)
        default: Bearer
      - name: livraison_id
        in: path
        type: string
        required: true
        description: Identifiant de la livraison
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            produits:
              type: array
              description: Liste des produits livrés (si modifiés)
            statut_paiement:
              type: string
            statut_livraison:
              type: string
            montant_paye:
              type: number
            dette_ancienne:
              type: number
    responses:
      200:
        description: Livraison modifiée avec succès
      400:
        description: Données invalides
      404:
        description: Livraison introuvable
      500:
        description: Erreur interne
    """
    erreur = _verifier_livraison(livraison_id, in_corbeille=False, manager_seulement=True)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    erreur = _verifier_contenu(data, _magasin_courant())
    if erreur:
        return erreur
    result, status_code = modifier_livraison_by_manager_service(livraison_id, data)
    return jsonify(result), status_code

@livraison_bp.route("/livraisons/<string:livraison_id>", methods=["PUT"])
@role_required("livreur")
@permission_requise("ventes.modifier")
@jwt_required()
def modifier_livraison_by_livreur(livraison_id):
    """
    Modifier une livraison existante (Livreur)
    ---
    tags:
      - Livraison
    summary: Modification d'une livraison (Livreur)
    description: >
      Permet à un livreur de modifier les produits, le statut, 
      le paiement, etc. d'une livraison. Gère la restitution du stock dans son inventaire propre.
    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT (format **Bearer &lt;token&gt;**)
        default: Bearer
      - name: livraison_id
        in: path
        type: string
        required: true
        description: Identifiant de la livraison
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            produits:
              type: array
              description: Liste des produits livrés (si modifiés)
            statut_paiement:
              type: string
            statut_livraison:
              type: string
            montant_paye:
              type: number
            dette_ancienne:
              type: number
    responses:
      200:
        description: Livraison modifiée avec succès
      400:
        description: Données invalides
      404:
        description: Livraison introuvable
      500:
        description: Erreur interne
    """
    erreur = _verifier_livraison(livraison_id, in_corbeille=False)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    erreur = _verifier_contenu(data, _magasin_courant())
    if erreur:
        return erreur
    result, status_code = modifier_livraison_by_livreur_service(livraison_id, data)
    return jsonify(result), status_code


# Récupère les livraison d'un livreur

@livraison_bp.route("/livraisons", methods=["GET"])
@jwt_required()
@livreur_required
def get_livraison():
    """
    Récupérer les livraisons d'un livreur
    ---
    tags:
      - Livraison
    summary: Liste des livraisons du livreur connecté
    description: Retourne toutes les livraisons associées au livreur authentifié via son token JWT.
    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Token JWT de l'utilisateur (au format **Bearer &lt;token&gt;**)
        default: Bearer 
    responses:
      200:
        description: Liste des livraisons récupérées avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "64faabb10c5e1a0034c0ab12"
              produit:
                type: string
                example: "Bouteille d'eau"
              quantite:
                type: integer
                example: 25
              date_livraison:
                type: string
                format: date
                example: "2024-08-10"
              statut:
                type: string
                example: "livrée"
      401:
        description: Accès refusé – Token manquant, invalide ou l'utilisateur n'est pas un livreur
    """
    livreur_id = get_jwt_identity()
    return get_livraison_livreur(livreur_id)

    
    
    
    
# Retourne les livraisons récentes d'un livreur
@livraison_bp.route("/livraisons/recente", methods=["GET"])
@jwt_required()
@livreur_required
def get_livraison_recente():
    """
    Récupérer les livraisons récentes d'un livreur
    ---
    tags:
      - Livraison
    summary: Liste des livraisons récentes du livreur connecté
    description: Retourne les livraisons récentes associées au livreur authentifié via son token JWT.
    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Token JWT de l'utilisateur (format **Bearer <token>**)
        default: Bearer 
    responses:
      200:
        description: Liste des livraisons récentes récupérées avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "64faabb10c5e1a0034c0ab12"
              produit:
                type: string
                example: "Colis express"
              quantite:
                type: integer
                example: 10
              date_livraison:
                type: string
                format: date-time
                example: "2025-07-10T14:30:00Z"
              statut:
                type: string
                example: "en cours"
      401:
        description: Accès refusé – Token manquant, invalide ou utilisateur non autorisé
    """
    livreur_id = get_jwt_identity()
    return get_livraisons_recentes_par_livreur(livreur_id)

  
  
  

# Retourne les détails d'une livraison dont l'id est passé en paramètre
@livraison_bp.route("/livraisons/<string:livraison_id>", methods=["GET"])
@jwt_required()
@role_required("manager", "admin", "livreur", "staff")
def get_details_livraison_for_livreur(livraison_id):
    """
    Récupérer une livraison par son ID
    ---
    tags:
      - Livraison
    summary: Détails d'une livraison par ID
    description: Retourne les détails d'une livraison spécifique identifiée par son ID, accessible uniquement aux rôles manager, admin, livreur ou staff.
    parameters:
      - name: livraison_id
        in: path
        type: string
        required: true
        description: Identifiant unique de la livraison
        example: "64faabb10c5e1a0034c0ab12"
      - name: Authorization
        in: header
        type: string
        required: true
        description: Token JWT de l'utilisateur (format **Bearer <token>**)
        default: Bearer 
    responses:
      200:
        description: Détails de la livraison récupérés avec succès
        schema:
          type: object
          properties:
            _id:
              type: string
              example: "64faabb10c5e1a0034c0ab12"
            produit:
              type: string
              example: "Colis express"
            quantite:
              type: integer
              example: 5
            adresse_livraison:
              type: string
              example: "123 Rue Exemple, Ville"
            date_livraison:
              type: string
              format: date-time
              example: "2025-07-10T14:30:00Z"
            statut:
              type: string
              example: "en cours"
      400:
        description: ID de la livraison manquant ou invalide
      401:
        description: Accès refusé – Token manquant, invalide ou utilisateur non autorisé
      404:
        description: Livraison non trouvée
    """
    erreur = _verifier_livraison(livraison_id)
    if erreur:
        return erreur
    if livraison_id:
        #print("ID de la livraison :", livraison_id)
        pass
    livreur_id = get_jwt_identity()
    return details_livraison_service(livreur_id, livraison_id)

  
  
# Met à jour le montant de payé d'une livraison
@livraison_bp.route("/livraisons/ajouter-paiement/<string:livraison_id>", methods=["POST"])
@jwt_required()
@role_required("livreur", "manager", "admin", "staff")
def ajouter_paiement(livraison_id):
    """
    Met à jour le montant payé d’une livraison.
    ---
    tags:
      - Livraison
    summary: Ajouter ou mettre à jour le paiement d'une livraison
    description: Cette route permet de mettre à jour le montant payé pour une livraison spécifique, accessible aux rôles livreur, manager, admin et staff.
    parameters:
      - name: livraison_id
        in: path
        type: string
        required: true
        description: Identifiant unique de la livraison
        example: "64faabb10c5e1a0034c0ab12"
      - name: Authorization
        in: header
        type: string
        required: true
        description: Token JWT de l'utilisateur (format **Bearer <token>**)
        default: Bearer 
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - montant
          properties:
            montant:
              type: number
              format: float
              example: 150.75
              description: Montant payé à ajouter ou mettre à jour pour la livraison
    responses:
      200:
        description: Paiement ajouté/mis à jour avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Paiement enregistré avec succès"
            livraison_id:
              type: string
              example: "64faabb10c5e1a0034c0ab12"
            montant_paye:
              type: number
              format: float
              example: 150.75
      400:
        description: Paramètres invalides (livraison_id ou montant manquant/incorrect)
      401:
        description: Accès refusé – Token manquant, invalide ou utilisateur non autorisé
    """
    erreur = _verifier_livraison(livraison_id)
    if erreur:
        return erreur
    data = request.get_json()
    montant = data.get("montant")

    if not livraison_id or montant is None:
        return jsonify({"message": "livraison_id et montant sont requis"}), 400

    try:
        montant = float(montant)
    except ValueError:
        return jsonify({"message": "Le montant doit être un nombre valide"}), 400

    # (résultat, statut) : jsonify(*…) renvoyait l'erreur dans une liste avec
    # le statut 200, affichée comme un succès.
    resultat, statut = ajouter_paiement_service(
        livraison_id, montant, methode=data.get("methode"), par=get_jwt_identity())
    return jsonify(resultat), statut


# Application de filtres sur les livraisons 
@livraison_bp.route("/livraisons/livreur/filtrees", methods=["GET"])
@jwt_required()
@livreur_required
def route_get_livraisons_filtrees():
    """
    Récupérer les livraisons filtrées du livreur connecté
    ---
    tags:
      - Livraison
    summary: Liste des livraisons filtrées pour le livreur authentifié
    description: >
      Permet de filtrer les livraisons selon plusieurs critères.
      Filtres disponibles (query parameters) :
      - status (ex: "livre", "en_attente")
      - client_id (ex: "12")
      - mode_paiement (ex: "partiellement payee", "payee", "non payee")
      - date (ex: "2025-05-26")
      - date_min (ex: "2025-05-01")
      - date_max (ex: "2025-05-26")
      - montant_min (ex: 200)
      - montant_max (ex: 500)

      Exemples d'utilisation en query string :
      ```
      ?status=livre
      ?client_id=12&mode_paiement=carte
      ?date_min=2025-05-01&date_max=2025-05-26&montant_min=100&montant_max=1000
      ```
    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: "Token JWT de l'utilisateur (format **Bearer <token>**)"
        default: Bearer 
      - name: status
        in: query
        type: string
        required: false
        description: 'Statut de la livraison (ex: "livre", "en_attente")'
      - name: client_id
        in: query
        type: string
        required: false
        description: 'ID du client associe'
      - name: mode_paiement
        in: query
        type: string
        required: false
        description: 'Mode de paiement (ex: "especes", "carte")'
      - name: date
        in: query
        type: string
        format: date
        required: false
        description: 'Date précise de la livraison (format YYYY-MM-DD)'
      - name: date_min
        in: query
        type: string
        format: date
        required: false
        description: 'Date minimale pour le filtre'
      - name: date_max
        in: query
        type: string
        format: date
        required: false
        description: 'Date maximale pour le filtre'
      - name: montant_min
        in: query
        type: number
        format: float
        required: false
        description: 'Montant minimum paye'
      - name: montant_max
        in: query
        type: number
        format: float
        required: false
        description: 'Montant maximum paye'
    responses:
      200:
        description: 'Livraisons filtrees recuperees avec succes'
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "64faabb10c5e1a0034c0ab12"
              produit:
                type: string
                example: "Colis express"
              quantite:
                type: integer
                example: 10
              date_livraison:
                type: string
                format: date-time
                example: "2025-07-10T14:30:00Z"
              statut:
                type: string
                example: "livre"
              montant_paye:
                type: number
                format: float
                example: 250.50
      401:
        description: 'Acces refuse – Token manquant, invalide ou utilisateur non autorise'
      500:
        description: 'Erreur serveur lors du traitement de la requete'
    """
    filters = request.args.to_dict()
    livreur_id = get_jwt_identity()
    try:
        result, status = get_livraisons_filtrees_par_livreur(livreur_id, filters)
        return jsonify(result), status
    except Exception as e:
        return jsonify({"message": str(e)}), 500


# Exportations livraison d'un livreur sous format excel
@livraison_bp.route("/livraisons/export", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur", "staff")
def export_livraisons_excel():
    """
    Exporter les livraisons d'un livreur en Excel
    ---
    tags:
      - Livraison
    summary: Export des livraisons au format Excel
    description: >
      Cette route permet aux livreurs ou managers d'exporter leurs livraisons au format Excel (.xlsx).<br>
      Les paramètres de filtre sont facultatifs mais permettent de limiter les données exportées.

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: "Token JWT de l'utilisateur (format **Bearer <token>**)"
        default: Bearer 
      - name: statut_paiement
        in: query
        type: string
        required: false
        description: 'Statut du paiement (ex: "payee", "non payee", "partiellement payee")'
      - name: statut_livraison
        in: query
        type: string
        required: false
        description: 'Statut de la livraison (ex: "livre", "en_attente")'
      - name: date_commande
        in: query
        type: string
        format: date
        required: false
        description: 'Date de la commande (format "YYYY-MM-DD")'
      - name: client_nom
        in: query
        type: string
        required: false
        description: "Nom du client"

    responses:
      200:
        description: Fichier Excel généré avec succès
        schema:
          type: string
          format: binary
      401:
        description: Accès refusé – Token manquant ou invalide
      500:
        description: Erreur lors de l'exportation des données
    """
    livreur_id = get_jwt_identity()
    filters = {
        "statut_paiement": request.args.get("statut_paiement"),
        "statut_livraison": request.args.get("statut_livraison"),
        "etat": request.args.get("etat"),
        "date_commande": request.args.get("date_commande"),
        "client_nom": request.args.get("client_nom"),
    }

    user = UserModel.get_user_by_id(livreur_id)

    if user['role'] in ('livreur', 'gerant'):
        livraisons, _ = get_livraison_livreur(livreur_id, filters)
    else:
        livraisons = get_livraison_managers()

    excel_file = export_livraisons_to_excel(livraisons)

    return send_file(
        excel_file,
        as_attachment=True,
        download_name="livraisons.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )



# Retourne les livraisons récentes du magasin
@livraison_bp.route("/livraisons/recente/managers", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_livraison_recente_total():
    """
    Récupérer les livraisons récentes de tous le magasins
    ---
    tags:
      - Livraison
    summary: Liste des livraisons récentes pour les managers
    description: >
      Cette route permet aux utilisateurs avec les rôles **admin** ou **manager** d’obtenir la liste
      des livraisons récentes pour tous les magasins.

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: "Token JWT de l'utilisateur (format **Bearer <token>**)"
        default: Bearer 

    responses:
      200:
        description: Livraisons récentes récupérées avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "64fb3ad68e6d5e001234abcd"
              produit:
                type: string
                example: "Colis express"
              quantite:
                type: integer
                example: 7
              date_livraison:
                type: string
                format: date-time
                example: "2025-07-09T15:45:00Z"
              statut:
                type: string
                example: "livre"
              montant_paye:
                type: number
                format: float
                example: 430.75
      401:
        description: Accès refusé – Token manquant, invalide ou rôle non autorisé
      500:
        description: Erreur serveur lors du traitement de la requête
    """
    try:
        data = inject_magasin_id({})
        magasin_id = data.get("magasin_id")
    except Exception as e:
        return jsonify({"error": str(e)}), 401
        
    return get_livraisons_recentes_total(magasin_id)

  
  
# Retourne toutes les livraisons d'un magasin
@livraison_bp.route("/managers/livraisons", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_livraison_magasins():
    """
    Récupérer toutes les livraisons du magasin (admin/manager uniquement)
    ---
    tags:
      - Livraison
    summary: Liste complète des livraisons pour les managers
    description: >
      Cette route retourne toutes les livraisons enregistrées dans les différents magasins.
      Accessible uniquement par les utilisateurs avec le rôle **admin** ou **manager**.

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: "Token JWT de l'utilisateur (format **Bearer <token>**)"
        default: Bearer

    responses:
      200:
        description: Liste des livraisons récupérée avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "64fcde258e6d5e001234abcd"
              client_nom:
                type: string
                example: "Paul Tchoumba"
              produit:
                type: string
                example: "Smartphone XYZ"
              quantite:
                type: integer
                example: 3
              date_livraison:
                type: string
                format: date-time
                example: "2025-07-09T10:20:00Z"
              statut:
                type: string
                example: "en_attente"
              montant_total:
                type: number
                format: float
                example: 599.99
              montant_paye:
                type: number
                format: float
                example: 300.00
      401:
        description: Accès refusé – Token manquant ou rôle non autorisé
      500:
        description: Erreur interne lors de la récupération des données
    """
    try:
        data = inject_magasin_id({})
        magasin_id = data.get("magasin_id")
    except Exception as e:
        return jsonify({"error": str(e)}), 401
        
    return get_livraison_managers(magasin_id)

    
# Annule ou supprime une livraison
@livraison_bp.route("/livraisons/<string:livraison_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager", "staff", "livreur")
@permission_requise("ventes.annuler")
def annuler_livraison(livraison_id):
    """
    Supprimer ou annuler une livraison
    ---
    tags:
      - Livraison
    summary: Supprimer une livraison par son ID
    description: >
      Cette route permet d’annuler ou supprimer une livraison existante.
      Accessible uniquement aux utilisateurs autorisés (admin, manager, staff, livreur).

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: "Token JWT (format **Bearer <token>**)"
        default: Bearer
      - name: livraison_id
        in: path
        type: string
        required: true
        description: ID de la livraison à supprimer

    responses:
      200:
        description: Livraison annulée ou supprimée avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Livraison supprimée avec succès"
      400:
        description: Requête invalide ou ID manquant
      401:
        description: Non autorisé – Token manquant ou invalide
      404:
        description: Livraison non trouvée
      500:
        description: Erreur serveur
    """
    erreur = _verifier_livraison(livraison_id, in_corbeille=False)
    if erreur:
        return erreur
    result, status_code = supprimer_livraison_service(
        livraison_id, request.get_json(silent=True) or {}, get_jwt_identity())
    return jsonify(result), status_code

# Rembourser le reliquat d'une livraison
  
@livraison_bp.route("/livraisons/refund/<string:livraison_id>", methods=["POST"])
@jwt_required()
@role_required("livreur", "admin", "staff", "manager")
def refund_customer(livraison_id):
    """
    Rembourser le reliquat d'une livraison
    ---
    tags:
      - Livraison
    summary: Rembourser un client pour une livraison
    description: >
      Cette route permet de déclencher le remboursement du reliquat (somme à restituer)
      pour une livraison spécifique.

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: "Token JWT (format **Bearer <token>**)"
        default: Bearer
      - name: livraison_id
        in: path
        type: string
        required: true
        description: ID de la livraison à rembourser

    responses:
      200:
        description: Remboursement effectué avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Remboursement effectué"
      400:
        description: Données invalides ou livraison déjà remboursée
      401:
        description: Non autorisé
      404:
        description: Livraison non trouvée
      500:
        description: Erreur serveur
    """
    erreur = _verifier_livraison(livraison_id)
    if erreur:
        return erreur
    return rembourser_service(livraison_id, request.get_json(silent=True) or {}, get_jwt_identity())


# créer un retour de produit après livraison
  
@livraison_bp.route("/livraisons/retour", methods=["POST"])
@jwt_required()
@role_required("manager", "admin", "staff", "livreur")
def retour_produit_livraison():
    """
    Retourner les produits d'une livraison
    ---
    tags:
      - Livraison
    summary: Retour de produits après livraison
    description: >
      Cette route permet d'enregistrer un retour de produits suite à une livraison.<br>
      Le livreur ou le manager doit fournir un motif et la liste des produits retournés avec leur quantité.<br><br>
      
      Chaque produit retourné doit correspondre à une ligne exacte de la livraison (produit_id + conditionnement_id s’il est conditionné).<br>
      Si la quantité retournée dépasse la quantité livrée pour cette ligne, le retour est rejeté.<br><br>

      ✔️ En cas de succès, les montants de la livraison sont automatiquement ajustés :<br>
      - Quantité totale<br>
      - Montant total<br>
      - Montant payé (ajusté si nécessaire)<br>
      - Ristourne cumulée<br>

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: "Token JWT (format **Bearer <token>**)"
        default: Bearer
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - livraison_id
            - livreur_id
            - motif
            - produits
          properties:
            livraison_id:
              type: string
              example: "68752a30ec15f1aa6c26f0e9"
              description: ID de la livraison concernée
            livreur_id:
              type: string
              example: "687110c2bdcbb303ac3cfc26"
              description: ID du livreur effectuant le retour
            motif:
              type: string
              example: "Produits abîmés lors de la livraison"
              description: Raison du retour
            produits:
              type: array
              description: Liste des produits retournés
              items:
                type: object
                required:
                  - produit_id
                  - quantite
                  - is_conditionne
                properties:
                  produit_id:
                    type: string
                    example: "6871174d2a1946217d6e4dd4"
                    description: ID du produit retourné
                  quantite:
                    type: integer
                    example: 10
                    description: Quantité retournée
                  is_conditionne:
                    type: boolean
                    example: true
                    description: Indique si le produit est retourné sous forme conditionnée
                  conditionnement_id:
                    type: string
                    example: "68711cf5a15c8e658fce448e"
                    description: ID du conditionnement si le produit est conditionné (obligatoire si `is_conditionne = true`)

    responses:
      201:
        description: Retour enregistré avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Retour enregistré avec succès"
            retour_id:
              type: string
              example: "68798765321a84b1b85e88ac"
            ristourne:
              type: number
              example: 86333.33
              description: Montant total de la ristourne calculée pour les retours
      400:
        description: Données invalides ou incohérentes (ex quantité retournée > livrée)
      401:
        description: Non autorisé – Token JWT manquant ou invalide
      403:
        description: Accès interdit selon le rôle
      500:
        description: Erreur serveur lors du traitement
    """
    data = request.get_json()
    if not data or not data.get("livraison_id") or not data.get("produits"):
        return jsonify({"message": "Données manquantes"}), 400
    erreur = _verifier_livraison(data["livraison_id"], in_corbeille=False)
    if erreur:
        return erreur

    data["manager_id"] = get_jwt_identity()
    # Seuls l'admin et le manager décident d'une retenue différente de la
    # politique du magasin ; un livreur applique la politique telle quelle.
    if get_jwt().get("role") not in ("admin", "manager"):
        data.pop("retenue_pourcentage", None)
    return creer_retour_livraison(data)


    
# valider une ristourne suite à une promotion via action manager
@livraison_bp.route("/livraisons/valider-ristourne/<string:livraison_id>", methods=["POST"])
@jwt_required()
@role_required("manager", "admin")
@permission_requise("ventes.abandon_reste")
def valider_ristourne(livraison_id):
    """
    Valide une ristourne accordée par un livreur.
    ---
    tags:
      - Livraison
    parameters:
      - in: path
        name: livraison_id
        required: true
        type: string
        description: ID de la livraison
    responses:
      200:
        description: Ristourne validée, livraison mise à jour
      404:
        description: Livraison introuvable
    """
    erreur = _verifier_livraison(livraison_id)
    if erreur:
        return erreur
    result, status_code = valider_ristourne_service(livraison_id, get_jwt_identity())
    return jsonify(result), status_code
  
  
  
  # rejeter une ristourne suite à une promotion via action manager
@livraison_bp.route("/livraisons/rejeter-ristourne/<string:livraison_id>", methods=["POST"])
@jwt_required()
@role_required("manager", "admin")
@permission_requise("ventes.abandon_reste")
def rejeter_ristourne(livraison_id):
    """
    Rejette une ristourne accordée par un livreur.
    ---
    tags:
      - Livraison
    parameters:
      - in: path
        name: livraison_id
        required: true
        type: string
        description: ID de la livraison
    responses:
      200:
        description: Ristourne rejetée, livraison mise à jour
      404:
        description: Livraison introuvable
    """
    erreur = _verifier_livraison(livraison_id)
    if erreur:
        return erreur
    result, status_code = refuser_abandon_service(livraison_id, get_jwt_identity())
    return jsonify(result), status_code


@livraison_bp.route("/livraisons/<string:livraison_id>/abandonner-reste", methods=["POST"])
@jwt_required()
@role_required("manager", "admin", "livreur")
@permission_requise("ventes.abandon_reste")
def abandonner_reste(livraison_id):
    """Abandonne le reste impayé d'une vente : { motif }. Il devient une
    ristourne (le total baisse jusqu'au montant payé) et la vente est soldée."""
    erreur = _verifier_livraison(livraison_id, in_corbeille=False)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    result, status_code = abandonner_reste_service(livraison_id, get_jwt_identity(), data.get("motif"))
    return jsonify(result), status_code


@livraison_bp.route("/livraisons/<string:livraison_id>/demander-abandon", methods=["POST"])
@jwt_required()
@role_required("livreur")
def demander_abandon(livraison_id):
    """Un gérant demande l'abandon du reste impayé : { motif } ; un manager
    valide (valider-ristourne) ou refuse (rejeter-ristourne)."""
    erreur = _verifier_livraison(livraison_id, in_corbeille=False)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    from services.permissions_service import droit_courant
    if droit_courant("ventes.abandon_reste"):
        result, status_code = abandonner_reste_service(livraison_id, get_jwt_identity(), data.get("motif"))
    else:
        result, status_code = demander_abandon_service(livraison_id, get_jwt_identity(), data.get("motif"))
    return jsonify(result), status_code

@livraison_bp.route("/livraisons/accorder-ristourne/<string:livraison_id>", methods=["POST"])
@jwt_required()
@role_required("livreur", "manager", "admin")
def accorder_ristourne(livraison_id):
    """
    Permet à un livreur ou un manager d'accorder une ristourne sur une livraison existante.
    """
    erreur = _verifier_livraison(livraison_id)
    if erreur:
        return erreur
    data = request.get_json()
    montant = data.get("montant")

    if montant is None:
        return jsonify({"message": "Le montant de la ristourne est requis"}), 400

    current_user_id = get_jwt_identity()
    result, status_code = accorder_ristourne_service(current_user_id, livraison_id, montant)
    return jsonify(result), status_code

@livraison_bp.route("/livraisons/par-methode-paiement/<string:methode_paiement>", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "staff", "livreur")
def get_livraisons_par_methode_paiement(methode_paiement):
    """
    Récupérer les livraisons filtrées par méthode de paiement et période optionnelle.
    - Livreur : voit uniquement ses propres livraisons
    - Admin / Manager / Staff : voit toutes les livraisons
    Query params: ?periode=jour|semaine|mois|annee
    """
    current_user_id = get_jwt_identity()
    periode = request.args.get("periode", "") or None

    # Détecter le rôle de l'utilisateur
    user = UserModel.get_user_by_id(current_user_id)
    role = user.get("role", "") if user else ""

    # Si livreur → filtrer uniquement ses livraisons
    livreur_id = current_user_id if role in ("livreur", "gerant") else None

    try:
        data = inject_magasin_id({})
        magasin_id_val = data.get("magasin_id")
    except Exception as e:
        return jsonify({"error": str(e)}), 401

    result, status_code = get_livraisons_par_methode_paiement_service(
        methode_paiement, periode, livreur_id, magasin_id=magasin_id_val
    )
    return jsonify(result), status_code

# ---------------------------------------------------------
# ROUTES CORBEILLE
# ---------------------------------------------------------

@livraison_bp.route("/livraisons/corbeille", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_corbeille():
    """
    Récupérer les livraisons dans la corbeille (pour tout le magasin)
    """
    try:
        data = inject_magasin_id({})
        magasin_id = data["magasin_id"]
    except Exception as e:
        return jsonify({"error": str(e)}), 401
        
    livraisons = LivraisonModel.get_corbeille_livraisons(magasin_id)
    from services.livraison_service import serialize_livraison
    livraisons_triees = sorted(livraisons, key=lambda l: l.get("deleted_at", datetime.min), reverse=True)
    return jsonify([serialize_livraison(l) for l in livraisons_triees]), 200

@livraison_bp.route("/livraisons/corbeille/livreur", methods=["GET"])
@jwt_required()
@role_required("livreur")
def get_corbeille_livreur():
    """
    Récupérer les livraisons corbeille d'un livreur spécifique
    """
    livreur_id = get_jwt_identity()
    livraisons = LivraisonModel.get_corbeille_livraisons_livreur(livreur_id)
    from services.livraison_service import serialize_livraison
    livraisons_triees = sorted(livraisons, key=lambda l: l.get("deleted_at", datetime.min), reverse=True)
    return jsonify([serialize_livraison(l) for l in livraisons_triees]), 200

@livraison_bp.route("/livraisons/corbeille/<string:livraison_id>/restaurer", methods=["POST"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def restaurer_livraison(livraison_id):
    """
    Restaure une livraison depuis la corbeille.
    """
    erreur = _verifier_livraison(livraison_id, in_corbeille=True)
    if erreur:
        return erreur
    result, status_code = restaurer_livraison_service(livraison_id)
    return jsonify(result), status_code

@livraison_bp.route("/livraisons/corbeille/<string:livraison_id>/definitive", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def supprimer_definitivement_livraison(livraison_id):
    """
    Supprime définitivement une livraison de la corbeille.
    """
    erreur = _verifier_livraison(livraison_id, in_corbeille=True)
    if erreur:
        return erreur
    current_user_id = get_jwt_identity()
    from services.livraison_service import supprimer_definitivement_livraison_service
    result, status_code = supprimer_definitivement_livraison_service(livraison_id, current_user_id)
    return jsonify(result), status_code
