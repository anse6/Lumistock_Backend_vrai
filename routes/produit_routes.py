# routes/produit_routes.py
from datetime import datetime
import json
import os
from werkzeug.exceptions import BadRequest
from utils.decorators import admin_required, manager_required
from utils.decorators import permission_requise
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask import Blueprint, request, jsonify, current_app, send_file
from werkzeug.utils import secure_filename
from utils.decorators import role_required
from utils.inject_magasin_id import inject_magasin_id
from schemas.produit_schema import CreateProduitSchema, UpdateProduitSchema
from models.user_model import UserModel
from models.produit_model import ProduitModel
from services.produit_service import (
    create_produit_service,
    get_produits_by_magasin_service,
    update_produit_service,
    delete_produit_service,
    get_produit_by_id_service,
    get_mouvements_stock_service,
    get_meilleurs_produits_livreur,
    get_meilleurs_produits_total,
    approvisionner_produit_service,
    manage_expired_product_service,
    restaurer_produit_service,
    get_all_magasin_mouvements_service,
    get_produit_by_barcode_service,
    generate_barcode_service,
    associer_code_barre_service,
    generer_codes_manquants_service,
    supprimer_definitivement_produit_service,
    solder_avarie_service,
)
from services.import_files_services import import_produits_from_excel
from services.export_files_services import export_produits_to_excel
from services.stats_staff_service import get_suggestions_service
from services.stock_livreur_service import get_stock_livreur_avec_details
from schemas.produit_schema import CreateProduitSchema, UpdateProduitSchema, ExpiredProductSchema, SolderAvarieSchema

produit_bp = Blueprint("produit", __name__)


def _produit_du_magasin(produit_id, in_corbeille=False):
    """Charge un produit du magasin de l'utilisateur connecté.

    Retourne (produit, None) ou (None, réponse d'erreur). Un produit d'un
    autre magasin répond 404, comme s'il n'existait pas, pour ne rien
    révéler de son existence.
    """
    try:
        magasin_id = inject_magasin_id({})["magasin_id"]
    except Exception:
        return None, (jsonify({"error": "Aucun magasin n'est associé à cet utilisateur."}), 403)
    produit = ProduitModel.get_for_magasin(produit_id, magasin_id, in_corbeille=in_corbeille)
    if not produit:
        message = "Produit introuvable dans la corbeille" if in_corbeille else "Produit non trouvé"
        return None, (jsonify({"error": message}), 404)
    return produit, None






# ---------------------------------------------------------
# ROUTES CORBEILLE POUR PRODUITS
# ---------------------------------------------------------

@produit_bp.route("/produits/corbeille", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_corbeille():
    """
    Récupérer les produits supprimés (Corbeille)
    """
    try:
        data = inject_magasin_id({})
        magasin_id = data["magasin_id"]
    except Exception as e:
        return jsonify({"error": str(e)}), 401
        
    from models.produit_model import ProduitModel
    produits = ProduitModel.get_corbeille_produits(magasin_id)
    # Serialize ObjectId to string
    for p in produits:
        p["_id"] = str(p["_id"])
        p["magasin_id"] = str(p["magasin_id"])
        if "categorie_id" in p and p["categorie_id"]:
            p["categorie_id"] = str(p["categorie_id"])
        else:
            p["categorie_id"] = None
        if "fournisseur_id" in p:
            p["fournisseur_id"] = str(p["fournisseur_id"])
    
    return jsonify(produits), 200

@produit_bp.route("/produits/corbeille/<produit_id>/restaurer", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("produits.supprimer")
def restaurer_produit(produit_id):
    """
    Restaurer un produit supprimé
    """
    _, erreur = _produit_du_magasin(produit_id, in_corbeille=True)
    if erreur:
        return erreur
    result, status_code = restaurer_produit_service(produit_id)
    return jsonify(result), status_code

@produit_bp.route("/produits/corbeille/<produit_id>/definitive", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("produits.supprimer")
def delete_permanently_produit(produit_id):
    """
    Supprimer définitivement un produit de la corbeille
    """
    _, erreur = _produit_du_magasin(produit_id, in_corbeille=True)
    if erreur:
        return erreur
    result, status_code = supprimer_definitivement_produit_service(produit_id)
    return jsonify(result), status_code

# Route pour créer un produit

@produit_bp.route("/produits", methods=["POST"])
@role_required("admin", "manager")
@permission_requise("produits.creer")
@jwt_required()
def create_produit():
    """
    Création d'un produit
    ---
    tags:
      - Produits
    consumes:
      - multipart/form-data
    parameters:
      - in: header
        name: Authorization
        required: true
        schema:
          type: string
        example: "Bearer votre.jwt.token"
        description: JWT token nécessaire pour l'autorisation
        default: Bearer
      - in: formData
        name: nom
        required: true
        type: string
        example: "Chocolat Noir 70%"
        description: Nom du produit
      - in: formData
        name: description
        required: false
        type: string
        example: "Tablette de chocolat noir bio 100g"
        description: Description détaillée du produit
      - in: formData
        name: prix
        required: true
        type: number
        format: float
        example: 2.99
        description: Prix unitaire du produit
      - in: formData
        name: quantite
        required: true
        type: integer
        example: 50
        description: Quantité initiale en stock
      - in: formData
        name: unite
        required: true
        type: string
        example: "unité"
        description: Unité de mesure (kg, litre, unité, etc.)
      - in: formData
        name: image
        required: false
        type: file
        description: Image du produit (format JPG/PNG)
      - in: formData
        name: categorie_id
        required: true
        type: string
        example: "605c72ef1532072e7c32ed47"
        description: ID de la catégorie associée
      - in: formData
        name: conditionnements
        required: false
        type: string
        example: '[{"nom": "Lot de 5", "facteur": 5, "prix_conditionnement": 12.99}]'
        description: JSON string des conditionnements disponibles
    responses:
      201:
        description: Produit créé avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Produit créé"
            produit_id:
              type: string
              example: "607f1f77bcf86cd799439011"
      400:
        description: Erreur de validation des données
        schema:
          type: object
          properties:
            errors:
              type: object
              example: {"nom": ["Ce champ est obligatoire"]}
      409:
        description: Un produit avec ce nom existe déjà dans ce magasin
        schema:
          type: object
          properties:
            error:
              type: string
              example: "Ce produit existe déjà dans ce magasin"
      401:
        description: Non autorisé (token invalide ou manquant)
      403:
        description: Accès refusé (rôle insuffisant)
    security:
      - JWT: []
    """
    try:
        data = request.form.to_dict()
        data = inject_magasin_id(data)
        data["admin_id"] = get_jwt_identity()  # nécessaire au contrôle du mode code-barre
        image_file = request.files.get("image")
        
        # Nettoyer conditionnements si présent sous forme de string JSON
        if "conditionnements" in data and isinstance(data["conditionnements"], str):
            try:
                data["conditionnements"] = json.loads(data["conditionnements"])
            except json.JSONDecodeError:
                data["conditionnements"] = []
        
        errors = CreateProduitSchema().validate(data)
        if errors:
            return jsonify({"errors": errors}), 400
        return create_produit_service(data, image_file)
    except BadRequest as e:
        return jsonify({"message": str(e)}), 400



# récupérer tous les produits d'un magasin
@produit_bp.route("/produits", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_produits_by_magasin():
    """
    Récupérer les produits du magasin connecté
    ---
    tags:
      - Produits
    summary: Obtenir la liste des produits avec filtres avancés
    description: >
      Récupère les produits du **magasin associé** à l'utilisateur authentifié.
      <br><br>
      **Fonctionnalités :**
      - Filtrage par nom (recherche partielle)
      - Filtrage par état de stock
      - Filtrage par catégorie
      - Filtrage par plage de prix
      - Filtrage par quantité disponible
      - Tri implicite par date de création (du plus récent au plus ancien)

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Token JWT (format `Bearer <votre_token>`)
        example: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
      - name: nom
        in: query
        type: string
        required: false
        description: Filtre par nom (insensible à la casse, recherche partielle)
        example: Chocolat
      - name: etat
        in: query
        type: string
        enum: [en stock, en rupture, en commande]
        required: false
        description: Filtre par état du stock
        example: en stock
      - name: categorie_nom
        in: query
        type: string
        required: false
        description: Filtre par nom exact de catégorie
        example: Epicerie
      - name: prix_min
        in: query
        type: number
        format: float
        required: false
        description: Prix unitaire minimum (inclus)
        example: 5.0
      - name: prix_max
        in: query
        type: number
        format: float
        required: false
        description: Prix unitaire maximum (inclus)
        example: 50.0
      - name: quantite_min
        in: query
        type: integer
        required: false
        description: Quantité minimum en stock (inclus)
        example: 10
      - name: quantite_max
        in: query
        type: integer
        required: false
        description: Quantité maximum en stock (inclus)
        example: 100
      - name: page
        in: query
        type: integer
        required: false
        description: Numéro de page (pour pagination, 1 par défaut)
        example: 1
      - name: limit
        in: query
        type: integer
        required: false
        description: Nombre d'éléments par page (10 par défaut, max 100)
        example: 20

    responses:
      200:
        description: Liste paginée des produits correspondants aux filtres
        schema:
          type: object
          properties:
            produits:
              type: array
              items:
                type: object
                properties:
                  _id:
                    type: string
                    example: "605c72ef1532072e7c32ed49"
                  nom:
                    type: string
                    example: "Chocolat Noir 70%"
                  description:
                    type: string
                    example: "Tablette 100g de chocolat bio"
                  prix:
                    type: number
                    format: float
                    example: 3.99
                  quantite:
                    type: integer
                    example: 45
                  etat:
                    type: string
                    example: "en stock"
                  image:
                    type: string
                    example: "chocolat_noir.jpg"
                  categorie:
                    type: object
                    properties:
                      _id:
                        type: string
                        example: "605c72ef1532072e7c32ed47"
                      nom:
                        type: string
                        example: "Epicerie sucrée"
                  conditionnements:
                    type: array
                    items:
                      type: object
                      properties:
                        nom:
                          type: string
                          example: "Lot de 5"
                        prix_conditionnement:
                          type: number
                          example: 18.0
                        economie:
                          type: number
                          example: -2.0
            pagination:
              type: object
              properties:
                page:
                  type: integer
                  example: 1
                total_pages:
                  type: integer
                  example: 3
                total_produits:
                  type: integer
                  example: 27
      400:
        description: Paramètres de filtre invalides
        schema:
          type: object
          properties:
            error:
              type: string
              example: "Le paramètre 'prix_max' doit être un nombre"
      401:
        description: |
          Non autorisé :
          - Token manquant/invalide
          - Rôle insuffisant
      404:
        description: Magasin non trouvé
      500:
        description: Erreur serveur
        schema:
          type: object
          properties:
            error:
              type: string
              example: "Erreur lors de la récupération des produits"

    security:
      - JWT: []
    """
    data = {}
    data = inject_magasin_id(data)

    magasin_id = data.get("magasin_id")
    filters = {
        "nom": request.args.get("nom"),
        "etat": request.args.get("etat"),
        "categorie_nom": request.args.get("categorie_nom"),
        "prix_min": request.args.get("prix_min", type=float),
        "prix_max": request.args.get("prix_max", type=float),
        "quantite_min": request.args.get("quantite_min", type=int),
        "quantite_max": request.args.get("quantite_max", type=int),
    }

    produits = get_produits_by_magasin_service(magasin_id, filters)

    for p in produits:
        p["_id"] = str(p["_id"])
        p["magasin_id"] = str(p["magasin_id"])
        p["categorie_id"] = str(p["categorie_id"]) if p.get("categorie_id") else None
    return jsonify(produits), 200

# Route pour récupérer tous les mouvements de stock d'un magasin (pour rapport annuel)
@produit_bp.route("/produits/mouvements/magasin", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_all_magasin_mouvements():
    """
    Récupère tous les mouvements de stock du magasin (Entrées/Sorties)
    ---
    tags:
      - Produits
    summary: Obtenir l'historique global des mouvements de stock
    description: >
      Cette route permet de récupérer l'ensemble des mouvements de stock (entrées, sorties, ventes, pertes, etc.)
      pour tous les produits du magasin. Utile pour les rapports de fin d'année.
    parameters:
      - in: query
        name: year
        type: integer
        example: 2024
        description: Année pour laquelle récupérer les mouvements (défaut année en cours)
      - in: query
        name: type
        type: string
        example: "vente"
        description: Filtrer par type de mouvement (ex: entrée, sortie, vente, perte, produit périmé, retour)
    responses:
      200:
        description: Liste des mouvements récupérée avec succès
      401:
        description: Non autorisé
    """
    try:
        data = inject_magasin_id({})
        magasin_id = data["magasin_id"]
    except Exception as e:
        return jsonify({"error": str(e)}), 401
        
    year = request.args.get("year", type=int)
    type_mouvement = request.args.get("type")
    
    result, status_code = get_all_magasin_mouvements_service(magasin_id, year, type_mouvement)
    return jsonify(result), status_code



# export des produits du manager sous formats excel
@produit_bp.route("/produits/export", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur", "staff")
def export_produits_excel():
    """
    Exporter les produits au format Excel
    ---
    tags:
      - Export
    summary: Export Excel des produits disponibles
    description: >
      Cette route permet d’exporter la liste des produits d’un magasin ou du stock d’un livreur (avec détails)
      en fonction de plusieurs filtres (nom, état, prix, quantité, etc.).
      Le fichier généré est retourné au format Excel (.xlsx).
    
    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT (format **Bearer &lt;token&gt;**)
        default: Bearer
      - in: query
        name: nom
        type: string
        required: false
        description: Nom du produit à filtrer
      - in: query
        name: etat
        type: string
        required: false
        description: État du produit (actif/inactif)
      - in: query
        name: categorie_nom
        type: string
        required: false
        description: Nom de la catégorie
      - in: query
        name: prix_min
        type: number
        required: false
        description: Prix minimum du produit
      - in: query
        name: prix_max
        type: number
        required: false
        description: Prix maximum du produit
      - in: query
        name: quantite_min
        type: integer
        required: false
        description: Quantité minimale en stock
      - in: query
        name: quantite_max
        type: integer
        required: false
        description: Quantité maximale en stock

    responses:
      200:
        description: Fichier Excel généré avec succès
        content:
          application/vnd.openxmlformats-officedocument.spreadsheetml.sheet:
            schema:
              type: string
              format: binary
      401:
        description: Non autorisé – Token manquant ou invalide
      403:
        description: Accès interdit – Rôle insuffisant
      500:
        description: Erreur lors de la génération ou de l’export du fichier
    """
    data = inject_magasin_id({})
    magasin_id = data.get("magasin_id")

    # Filtres
    filters = {
        "nom": request.args.get("nom"),
        "etat": request.args.get("etat"),
        "categorie_nom": request.args.get("categorie_nom"),
        "prix_min": request.args.get("prix_min", type=float),
        "prix_max": request.args.get("prix_max", type=float),
        "quantite_min": request.args.get("quantite_min", type=int),
        "quantite_max": request.args.get("quantite_max", type=int),
    }

    # vérifier le role de l'utilisateurs qui appelle cette route
    user_id = get_jwt_identity()
    user = UserModel.find_by_id(user_id)
    if user['role'] in ('livreur', 'gerant'):
        produits = get_stock_livreur_avec_details(user_id)
    else:
        produits = get_produits_by_magasin_service(magasin_id, filters)

    # Génération du fichier Excel dans un buffer
    buffer = export_produits_to_excel(produits)

    #  Sauvegarde locale dans un dossier temporaire
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"produits_{now}.xlsx"
    save_path = os.path.join(current_app.root_path, "exports")
    os.makedirs(save_path, exist_ok=True)
    full_path = os.path.join(save_path, filename)

    with open(full_path, "wb") as f:
        f.write(buffer.getvalue())

    # Retourne le fichier à l'utilisateur
    return send_file(
        full_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )



@produit_bp.route("/produits/magasin/", methods=["GET"])
@jwt_required()
@role_required("livreur")
def get_product_stock_livreur():
    """
    Récupérer le stock disponible pour un livreur
    ---
    tags:
      - Produits
    summary: Obtenir les produits disponibles pour livraison
    description: >
      Cette endpoint permet aux **livreurs** de récupérer la liste des produits disponibles
      dans leur magasin attitré, avec des informations spécifiques utiles pour la livraison.
      <br><br>
      **Inclus :**
      - Détails complets des produits
      - Localisation du stock
      - Quantités immédiatement disponibles
      - Informations sur les conditionnements

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Token JWT (format `Bearer <token>`)
        example: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
      - name: en_stock
        in: query
        type: boolean
        required: false
        description: Filtrer uniquement les produits disponibles (quantité > 0)
        default: true
      - name: tri
        in: query
        type: string
        enum: [nom, prix_croissant, prix_decroissant]
        required: false
        description: Critère de tri des résultats
        example: prix_croissant

    responses:
      200:
        description: Liste des produits disponibles pour livraison
        schema:
          type: object
          properties:
            magasin:
              type: object
              properties:
                nom:
                  type: string
                  example: "Supermarché Central"
                adresse:
                  type: string
                  example: "123 Rue Principale, Ville"
            produits:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: string
                    example: "507f1f77bcf86cd799439011"
                  nom:
                    type: string
                    example: "Eau Minérale 1L"
                  description:
                    type: string
                    example: "Pack de 6 bouteilles"
                  image_url:
                    type: string
                    example: "/uploads/images/eau_minerale.jpg"
                  prix_unitaire:
                    type: number
                    format: float
                    example: 1.20
                  quantite_disponible:
                    type: integer
                    example: 42
                  emplacement:
                    type: string
                    example: "Rayon 3, Étagère B"
                  conditionnements:
                    type: array
                    items:
                      type: object
                      properties:
                        nom:
                          type: string
                          example: "Pack 6"
                        prix:
                          type: number
                          example: 6.0
                        economie:
                          type: number
                          example: 1.2
      401:
        description: |
          Non autorisé :
          - Token invalide/expiré
          - Rôle incorrect (non livreur)
        schema:
          type: object
          properties:
            error:
              type: string
              example: "Authentification requise"
      404:
        description: |
          Ressource non trouvée :
          - Livreur non associé à un magasin
          - Aucun produit disponible
      500:
        description: Erreur serveur
        schema:
          type: object
          properties:
            error:
              type: string
              example: "Erreur lors de la récupération du stock"

    security:
      - JWT: []
    """
    livreur_id = get_jwt_identity()
    produits = get_stock_livreur_avec_details(livreur_id)
    return jsonify(produits), 200




#Mettre à jour les données d'un produit

@produit_bp.route("/produits/<produit_id>", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("produits.modifier")
def update_produit(produit_id):
  
    """
    Mettre à jour un produit existant
    ---
    tags:
      - Produits
    summary: Mise à jour partielle ou complète d'un produit
    description: |
      Permet de modifier les informations d'un produit existant.
      <br><br>
      **Fonctionnalités :**
      - Mise à jour partielle (seuls les champs fournis sont modifiés)
      - Modification de l'image du produit
      - Gestion des conditionnements
      - Historique des modifications (via admin_id)
      <br><br>
      **Rôles autorisés :** Admin, Manager, Livreur (avec restrictions)

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Token JWT (format `Bearer <token>`)
        example: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
      - name: produit_id
        in: path
        type: string
        required: true
        description: ID du produit à modifier
        example: "507f1f77bcf86cd799439011"
      - name: nom
        in: formData
        type: string
        required: false
        description: Nouveau nom du produit
        example: "Nouveau nom du produit"
      - name: description
        in: formData
        type: string
        required: false
        description: Nouvelle description
        example: "Nouvelle description plus détaillée"
      - name: prix
        in: formData
        type: number
        format: float
        required: false
        description: Nouveau prix unitaire
        example: 15.99
      - name: quantite
        in: formData
        type: integer
        required: false
        description: Nouvelle quantité en stock
        example: 150
      - name: image
        in: formData
        type: file
        required: false
        description: Nouvelle image du produit (laisser vide pour conserver l'actuelle)
      - name: conditionnements
        in: formData
        type: string
        required: false
        description: |
          JSON string des conditionnements mis à jour.
          Format: `[{"nom": "Lot", "facteur": 5, "prix_conditionnement": 45.0}]`
        example: '[{"nom": "Pack familial", "facteur": 3, "prix_conditionnement": 40.0}]'
      - name: categorie_id
        in: formData
        type: string
        required: false
        description: Nouvelle catégorie ID
        example: "605c72ef1532072e7c32ed47"

    responses:
      200:
        description: Produit mis à jour avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Produit mis à jour avec succès"
            produit_id:
              type: string
              example: "507f1f77bcf86cd799439011"
            modifications:
              type: array
              items:
                type: string
              example: ["nom", "prix"]
      400:
        description: |
          Erreurs de validation :
          - Données invalides
          - JSON des conditionnements mal formatté
        schema:
          type: object
          properties:
            errors:
              type: object
              example: {"prix": ["Doit être un nombre positif"]}
      401:
        description: Non autorisé (token invalide ou manquant)
      403:
        description: |
          Accès refusé :
          - Rôle insuffisant
          - Tentative de modification hors de son magasin (pour livreurs/managers)
      404:
        description: Produit non trouvé
      500:
        description: Erreur serveur lors de la mise à jour

    security:
      - JWT: []
    """
    _, erreur = _produit_du_magasin(produit_id)
    if erreur:
        return erreur
    try:
        data = request.form.to_dict()
        image_file = request.files.get("image")
        data['admin_id'] = get_jwt_identity()  # Ajout de l'ID de l'utilisateur qui met à jour le produit
        
        # Convertir conditionnements s’ils sont passés en JSON string
        if "conditionnements" in data and isinstance(data["conditionnements"], str):
            import json
            try:
                data["conditionnements"] = json.loads(data["conditionnements"])
            except json.JSONDecodeError:
                data["conditionnements"] = []
 
        errors = UpdateProduitSchema().validate(data)
        if errors:
            return jsonify({"errors": errors}), 400

        return  update_produit_service(produit_id, data, image_file)
        
        
      
    except BadRequest as e:
        return jsonify({"message": str(e)}), 400



# Supprimer un produit de la base de données
@produit_bp.route("/produits/<produit_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("produits.supprimer")
def delete_produit(produit_id):
    """
    Supprimer un produit par son ID
    ---
    tags:
      - Produits
    summary: Suppression d’un produit existant
    description: >
      Cette route permet aux **admins** ou **managers** de supprimer un produit
      à partir de son identifiant (`produit_id`).

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Jeton d'authentification JWT (format Bearer <token>)
        default: Bearer
      - name: produit_id
        in: path
        type: string
        required: true
        description: Identifiant unique du produit à supprimer
        example: 605c72ef1532072e7c32ed49

    responses:
      200:
        description: Produit supprimé avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: Produit supprimé
      404:
        description: Produit non trouvé
      401:
        description: Jeton d'authentification manquant ou invalide
      403:
        description: Rôle non autorisé
      500:
        description: Erreur serveur lors de la suppression
    """
    _, erreur = _produit_du_magasin(produit_id)
    if erreur:
        return erreur
    result, status_code = delete_produit_service(produit_id)
    return jsonify(result), status_code

 
 
  
# Avoir les détails d'un prodtuit

@produit_bp.route("/produits/<produit_id>", methods=["GET"])
@jwt_required()
@role_required("admin", "livreur", "manager")
def get_produit_by_id(produit_id):
    """
    Récupérer un produit par son ID
    ---
    tags:
      - Produits
    summary: Détails d’un produit
    description: >
      Cette route permet de récupérer les détails complets d’un produit à partir
      de son identifiant. Accessible uniquement aux utilisateurs ayant un rôle
      **admin**, **manager**, ou **livreur**.

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Jeton JWT au format Bearer <token>
        default: Bearer
      - name: produit_id
        in: path
        type: string
        required: true
        description: ID du produit à consulter
        example: 605c72ef1532072e7c32ed49

    responses:
      200:
        description: Produit récupéré avec succès
        schema:
          type: object
          properties:
            _id:
              type: string
              example: "605c72ef1532072e7c32ed49"
            nom:
              type: string
              example: "Produit Exemple"
            description:
              type: string
              example: "Description du produit"
            prix:
              type: number  
              format: float
              example: 19.99
            quantite:
              type: integer
              example: 100
            etat:
              type: string
              example: en stock
            categorie_id:
              type: string
              example: "605c72ef1532072e7c32ed47"
            magasin_id:
              type: string
              example: "605c72ef1532072e7c32ed48"
      401:
        description: Jeton manquant ou invalide
      403:
        description: Accès interdit pour ce rôle
      404:
        description: Produit non trouvé
      500:
        description: Erreur serveur inattendue
    """
    _, erreur = _produit_du_magasin(produit_id)
    if erreur:
        return erreur
    produit = get_produit_by_id_service(produit_id)
    if not produit:
        return jsonify({"message": "Produit non trouvé"}), 404
    produit["_id"] = str(produit["_id"])
    produit["magasin_id"] = str(produit["magasin_id"])
    produit["categorie_id"] = str(produit["categorie_id"]) if produit.get("categorie_id") else None
    return jsonify(produit), 200


# Récupérer tous les mouvements de stock d'un produit
@produit_bp.route("/produits/<produit_id>/mouvements", methods=["GET"])
@produit_bp.route("/<produit_id>/mouvements", methods=["GET"])  # ancien chemin, conservé pour compatibilité
@jwt_required()
@role_required("admin", "manager")
def get_mouvements_stock(produit_id):
    """
    Récupérer les mouvements de stock d'un produit.
    ---
    tags:
      - Produits
    parameters:
      - in: path
        name: produit_id
        required: true
        type: string
        description: ID du produit
      - in: query
        name: type
        required: false
        type: string
        description: Filtrer par type de mouvement
    responses:
      200:
        description: Liste des mouvements de stock
      404:
        description: Produit non trouvé ou aucun mouvement
    """
    _, erreur = _produit_du_magasin(produit_id)
    if erreur:
        return erreur
    type_mouvement = request.args.get("type")  # Filtre optionnel
    result, status = get_mouvements_stock_service(produit_id, type_mouvement)
    return jsonify(result), status




# Fonctions statistiques
"""
Retourne les produits les plus vendus par un livreur, classés par quantité totale 
livrée (du plus vendu au moins vendu) 
"""
@produit_bp.route("/produits/best/livreur/", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def get_best_product_per_livreur():
    """
    Produits les plus livrés par le livreur
    ---
    tags:
      - Produits
    summary: Produits les plus livrés
    description: >
      Retourne les produits les plus livrés par le livreur connecté, classés par
      **quantité totale livrée** (du plus vendu au moins vendu).<br>
      Accessible aux rôles : **admin**, **manager**, **livreur**.

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Jeton JWT d’authentification (format **Bearer &lt;token&gt;**)
        default: Bearer 

    responses:
      200:
        description: Liste des produits les plus livrés
        schema:
          type: array
          items:
            type: object
            properties:
              produit_id:
                type: string
                example: "60df5bf8f0b2f81d1c4c9b4a"
              nom:
                type: string
                example: "Savon antiseptique"
              quantite_totale:
                type: integer
                example: 125
      401:
        description: Accès non autorisé ou token manquant
      403:
        description: Rôle non autorisé
      500:
        description: Erreur interne
    """
    current_user_id = get_jwt_identity()
    best_products = get_meilleurs_produits_livreur(current_user_id)
    return jsonify(best_products), 200

    

@produit_bp.route("/produits/best/managers", methods=["GET"])
@role_required("admin", "manager", "livreur")
def get_best_product_total():
    """
    Obtenir les produits les plus vendus
    ---
    tags:
      - Produits
    summary: Statistiques des produits les plus vendus
    description: |
      Retourne les produits les plus vendus classés par quantité totale livrée.
      <br><br>
      **Fonctionnalités :**
      - Vue globale pour les administrateurs/managers
      - Filtrage par livreur spécifique
      - Liste des livreurs disponibles incluse dans la réponse
      <br><br>
      **Permissions :**
      - Admin/Manager : accès complet
      - Livreur : seulement ses propres statistiques

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Token JWT (format `Bearer <token>`)
        example: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
      - name: livreur_id
        in: query
        type: string
        required: false
        description: |
          Filtre par livreur spécifique (obligatoire pour les livreurs).
          Les managers/admins peuvent omettre ce paramètre pour une vue globale.
        example: "507f1f77bcf86cd799439011"
      - name: limit
        in: query
        type: integer
        required: false
        default: 10
        description: Nombre maximum de produits à retourner (max 50)
        example: 5
      - name: periode
        in: query
        type: string
        enum: [semaine, mois, trimestre, annee, tout]
        required: false
        default: mois
        description: Période d'analyse des ventes
        example: mois

    responses:
      200:
        description: Statistiques des meilleurs produits
        schema:
          type: object
          properties:
            best_products:
              type: array
              items:
                type: object
                properties:
                  produit_id:
                    type: string
                    example: "507f1f77bcf86cd799439012"
                  nom:
                    type: string
                    example: "Eau Minérale 1L"
                  image_url:
                    type: string
                    example: "/uploads/images/eau_minerale.jpg"
                  quantite_vendue:
                    type: integer
                    example: 150
                  pourcentage:
                    type: number
                    format: float
                    example: 25.5
                  chiffre_affaires:
                    type: number
                    format: float
                    example: 450.0
            livreurs:
              type: array
              items:
                type: object
                properties:
                  _id:
                    type: string
                    example: "507f1f77bcf86cd799439011"
                  nom_complet:
                    type: string
                    example: "Jean Dupont"
                  email:
                    type: string
                    example: "jean.dupont@example.com"
      400:
        description: |
          Requête invalide :
          - Livreur non spécifié (pour les livreurs)
          - Paramètres de filtre invalides
        schema:
          type: object
          properties:
            error:
              type: string
              example: "Un livreur_id doit être spécifié"
      401:
        description: Non autorisé (token invalide ou manquant)
      403:
        description: Accès refusé (rôle insuffisant)
      500:
        description: Erreur serveur
        schema:
          type: object
          properties:
            error:
              type: string
              example: "Erreur lors de la récupération des statistiques"

    security:
      - JWT: []
    """
  
    from services.user_service import get_livreur_service
    livreur_id = request.args.get("livreur_id", None)
    try:
        best_products = get_meilleurs_produits_total(livreur_id)
        # Appel au service de récuparation des livreurs
        livreur_result = get_livreur_service()
        if livreur_result["status"] != 200:
            raise Exception("Impossible de récupérer les livreurs")
        return jsonify({
            "best_products": best_products,
            "livreurs": livreur_result["livreurs"]
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Erreur interne : {str(e)}"}), 500

    
  
# Importer des produits depuis un fichier Excel
@produit_bp.route("/produits/import", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("produits.importer")
def import_produits():
    """
    Importer des produits depuis un fichier Excel
    ---
    tags:
      - Produits
    summary: Importation de produits par fichier Excel
    description: >
      Permet d'importer en masse des produits depuis un fichier Excel (`.xlsx`) 
      pour un magasin donné. Seuls les rôles **admin** et **manager** peuvent exécuter cette action.

    consumes:
      - multipart/form-data

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Jeton JWT d’authentification (format **Bearer &lt;token&gt;**)
        default: Bearer 
      - name: fichier
        in: formData
        type: file
        required: true
        description: >
          Fichier Excel à importer (`.xlsx`). 
          Le fichier doit contenir les colonnes : **Nom**, **Description**, **Prix**, **Prix d'achat**, **Unité**, **Quantité**, **Catégorie**.

    responses:
      200:
        description: Produits importés avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Importation réussie"
      400:
        description: Fichier ou données invalides
        schema:
          type: object
          properties:
            error:
              type: string
              example: "Aucun fichier fourni"
      500:
        description: Erreur interne lors de l'importation
        schema:
          type: object
          properties:
            error:
              type: string
              example: "Erreur lors de l'importation : Nom de la feuille introuvable"
    """
    fichier = request.files.get("fichier")
    if not fichier:
        return jsonify({"error": "Aucun fichier fourni"}), 400
    try:
        data = request.form.to_dict()
        data = inject_magasin_id(data)
        magasin_id = data.get("magasin_id")
        if not magasin_id:
            return jsonify({"error": "Magasin ID manquant"}), 400

        result, status_code = import_produits_from_excel(fichier, magasin_id)
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"error": f"Erreur lors de l'importation : {str(e)}"}), 500

  

@produit_bp.route("/produits/suggestions", methods=["GET"])
@role_required("admin", "manager", "livreur", "staff")
def suggere_images():
    """
    Suggérer des images pour un produit en fonction des mots-clés
    ---
    tags:
      - Produits
    summary: Suggestions d’images produit via IA ou recherche contextuelle
    description: >
      Retourne des images suggérées à partir du nom, de la description, de la catégorie ou des tags d’un produit.

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Jeton JWT d’authentification (format **Bearer &lt;token&gt;**)
        default: Bearer 
      - name: nom
        in: query
        type: string
        required: false
        description: Nom du produit
        example: Banane Bio
      - name: description
        in: query
        type: string
        required: false
        description: Description courte du produit
        example: "Fruits tropicaux riches en potassium"
      - name: categorie
        in: query
        type: string
        required: false
        description: Nom de la categorie du produit
        example: Fruits
      - name: tags
        in: query
        type: array
        items:
          type: string
        collectionFormat: multi
        required: false
        description: Liste de mots-cles lies au produit 

    responses:
      200:
        description: Images suggerees avec succes
        schema:
          type: object
          properties:
            images:
              type: array
              items:
                type: string
                example: "https://example.com/images/banane_bio.png"
      400:
        description: Paramètres insuffisants ou invalides
      500:
        description: Erreur serveur lors de la suggestion
    """
    nom = request.args.get("nom")
    description = request.args.get("description")
    categorie = request.args.get("categorie")
    tags = request.args.getlist("tags")  # /suggestions?tags=fruit&tags=bio

    images = get_suggestions_service(nom, categorie,description, tags)
    return {"images": images}, 200  
  

@produit_bp.route("/produits/<produit_id>/approvisionner", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("stock.approvisionner")
def approvisionner_produit(produit_id):
    """
    Approvisionner un produit
    ---
    tags:
      - Produits
    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Jeton JWT d’authentification (format **Bearer &lt;token&gt;**)
        default: Bearer
      - in: path
        name: produit_id
        required: true
        type: string
        description: ID du produit à approvisionner
        example: "605c72ef1532072e7c32ed49"
        
      - in: formData
        name: quantite
        required: true
        type: integer
        description: Quantité à ajouter au stock
        example: 50
    responses:
      200:
        description: Produit approvisionné avec succès
      404:
        description: Produit non trouvé
    """
    _, erreur = _produit_du_magasin(produit_id)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    data['admin_id'] = get_jwt_identity()
    try:
        quantite = int(data.get("quantite", 0))
        if quantite <= 0:
            return jsonify({"error": "La quantité doit être supérieure à 0"}), 400

        result, status_code = approvisionner_produit_service(produit_id, data)
        return jsonify(result), status_code
    except ValueError:
        return jsonify({"error": "Quantité invalide"}), 400
@produit_bp.route("/produits/<produit_id>/perime", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("stock.avaries")
def manage_expired_product(produit_id):
    """
    Enregistrer des produits comme périmés et les retirer du stock
    ---
    tags:
      - Produits
    summary: Retirer des produits périmés du stock
    description: >
      Cette route permet aux **administrateurs** ou **managers** de déclarer une quantité
      de produits comme périmés. Cette quantité sera déduite du stock du magasin
      et le mouvement sera tracé comme 'produit périmé'.
    parameters:
      - in: header
        name: Authorization
        required: true
        schema:
          type: string
        example: "Bearer votre.jwt.token"
      - in: path
        name: produit_id
        required: true
        type: string
        description: ID du produit concerné
      - in: body
        name: data
        required: true
        schema:
          type: object
          properties:
            quantite:
              type: integer
              example: 5
              description: Quantité de produits périmés à retirer
    responses:
      200:
        description: Produits périmés enregistrés avec succès
      400:
        description: Quantité invalide ou supérieure au stock actuel
      404:
        description: Produit non trouvé
    security:
      - JWT: []
    """
    _, erreur = _produit_du_magasin(produit_id)
    if erreur:
        return erreur
    try:
        data = request.get_json(silent=True) or {}
        errors = ExpiredProductSchema().validate(data)
        if errors:
            return jsonify({"errors": errors}), 400
        
        data['admin_id'] = get_jwt_identity()
        return manage_expired_product_service(produit_id, data)
        
    except BadRequest as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": f"Erreur serveur : {str(e)}"}), 500


@produit_bp.route("/produits/<produit_id>/solder", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("stock.soldes")
def solder_avarie(produit_id):
    """
    Mettre en solde des unités avariées d'un produit
    ---
    tags:
      - Produits
    description: >
      Les unités déclarées en avarie (hors vols) entrent dans un produit lié
      « Nom (soldé) », vendu au prix soldé et au coût 0 (la perte est déjà
      comptée à l'avarie). Le produit soldé se vend ensuite normalement.
    parameters:
      - in: path
        name: produit_id
        required: true
        type: string
      - in: body
        name: data
        required: true
        schema:
          type: object
          properties:
            quantite:
              type: integer
              example: 4
            prix_solde:
              type: number
              example: 1500
            note:
              type: string
    responses:
      200:
        description: Unités mises en solde
      400:
        description: Quantité ou prix invalide
      404:
        description: Produit non trouvé
      409:
        description: Unités déjà mises en solde entre-temps
    """
    _, erreur = _produit_du_magasin(produit_id)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    errors = SolderAvarieSchema().validate(data)
    if errors:
        return jsonify({"errors": errors, "error": "Données invalides"}), 400
    data["admin_id"] = get_jwt_identity()
    result, status_code = solder_avarie_service(produit_id, data)
    return jsonify(result), status_code


@produit_bp.route("/produits/<produit_id>/valorisation", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_valorisation_produit(produit_id):
    """
    Lots d'achat restants et valeur du stock d'un produit selon la méthode du magasin.
    ---
    tags:
      - Produits
    responses:
      200:
        description: Valorisation du produit
      404:
        description: Produit non trouvé
    """
    from services import valorisation_service
    produit, erreur = _produit_du_magasin(produit_id)
    if erreur:
        return erreur
    return jsonify(valorisation_service.etat_stock(produit_id, produit.get("magasin_id"))), 200


@produit_bp.route("/produits/code-barre/<barcode>", methods=["GET"])
@jwt_required()
def get_produit_by_barcode(barcode):
    """
    Récupérer un produit via son code-barres
    """
    try:
        data = inject_magasin_id({})
        magasin_id = data["magasin_id"]
    except Exception as e:
        return jsonify({"error": str(e)}), 401
        
    result, status_code = get_produit_by_barcode_service(barcode, magasin_id)
    return jsonify(result), status_code


@produit_bp.route("/produits/generate-barcode", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def generate_barcode():
    """
    Générer un code-barres interne pour un produit local
    """
    try:
        data = inject_magasin_id({})
        magasin_id = data["magasin_id"]
    except Exception as e:
        return jsonify({"error": str(e)}), 401
        
    result, status_code = generate_barcode_service(magasin_id)
    return jsonify(result), status_code


@produit_bp.route("/produits/<produit_id>/code-barre", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("produits.modifier")
def associer_code_barre(produit_id):
    """
    Associer un code-barre à un produit (ou à l'un de ses cartons)
    ---
    tags:
      - Produits
    description: >
      Enregistre le code lu par le lecteur. Avec conditionnement_id, le code
      est celui du carton : le scanner ajoutera un carton à la vente.
      Un code vide retire le code existant.
    parameters:
      - in: path
        name: produit_id
        required: true
        type: string
      - in: body
        name: data
        schema:
          type: object
          properties:
            code_barre:
              type: string
              example: "6001087003142"
            conditionnement_id:
              type: string
    responses:
      200: {description: Code enregistré}
      404: {description: Produit ou conditionnement non trouvé}
      409: {description: Code déjà utilisé dans le magasin}
    """
    produit, erreur = _produit_du_magasin(produit_id)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    result, status_code = associer_code_barre_service(
        produit_id, str(produit["magasin_id"]), data.get("code_barre"), data.get("conditionnement_id") or None
    )
    return jsonify(result), status_code


@produit_bp.route("/produits/codes-barres/generer", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("produits.modifier")
def generer_codes_manquants():
    """
    Générer un code interne (EAN-13 « 200… ») pour les produits choisis qui n'en ont pas
    ---
    tags:
      - Produits
    parameters:
      - in: body
        name: data
        schema:
          type: object
          properties:
            produit_ids:
              type: array
              items: {type: string}
    responses:
      200: {description: Codes générés}
    """
    try:
        magasin_id = inject_magasin_id({})["magasin_id"]
    except Exception:
        return jsonify({"error": "Aucun magasin n'est associé à cet utilisateur."}), 403
    data = request.get_json(silent=True) or {}
    ids = data.get("produit_ids")
    if not isinstance(ids, list):
        return jsonify({"error": "produit_ids doit être une liste"}), 400
    result, status_code = generer_codes_manquants_service(str(magasin_id), ids[:1000])
    return jsonify(result), status_code
