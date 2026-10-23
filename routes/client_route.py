from flask import Blueprint, request, jsonify, send_file, current_app
from schemas.client_schema import CreateClientSchema, UpdateClientSchema
from utils.inject_magasin_id import inject_magasin_id
from utils.decorators import role_required
from utils.decorators import permission_requise
from services.permissions_service import droit_courant
from datetime import datetime
import os
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from services.export_files_services import export_clients_to_excel
from services.client_service import (
    create_client_service,
    get_all_clients_service,
    get_client_by_id_service,
    update_client_service,
    delete_client_service,
    get_clients_livreur,
    get_meilleurs_clients_livreur,
    get_meilleurs_clients_total,
    get_clients_magasins,
    get_clients_with_debt_by_livreur,
    get_clients_with_debt_by_magasin,
    get_debt_statistics,
    get_client_debt_details_service,
    ajouter_montant_caisse_service,
    retirer_montant_caisse_service,
    restaurer_client_service,
    dette_client,
)


def _message_erreurs(errors):
    """Erreurs de validation lisibles : « email : Not a valid email address. »"""
    return " ".join(f"{champ} : {' '.join(map(str, msgs)) if isinstance(msgs, list) else msgs}"
                    for champ, msgs in errors.items())
from services.livraison_service import get_client_livraisons_service
from services import compte_client_service
from models.user_model import UserModel


client_bp = Blueprint("client", __name__, url_prefix="/clients")


def _client_du_magasin(client_id, corbeille=False):
    """Client accessible à l'utilisateur, ou (None, réponse 404).

    Un client n'est visible que depuis son magasin (le staff voit tout) :
    sans ce contrôle, n'importe quel compte pouvait lire, modifier ou
    supprimer le client d'un autre magasin en connaissant son identifiant.
    """
    from bson import ObjectId
    from models.client_model import ClientModel

    introuvable = (None, (jsonify({"error": "Client introuvable", "message": "Client introuvable"}), 404))
    if not client_id or not ObjectId.is_valid(str(client_id)):
        return introuvable
    query = {"_id": ObjectId(str(client_id)), "deleted_at": {"$exists": corbeille}}
    client = ClientModel.get_collection().find_one(query)
    if not client:
        return introuvable
    if get_jwt().get("role") != "staff":
        try:
            magasin_id = str(inject_magasin_id({})["magasin_id"])
        except Exception:
            magasin_id = None
        if str(client.get("magasin_id")) != (magasin_id or ""):
            return introuvable
    return client, None


@client_bp.route("/create", methods=["POST"])
@jwt_required()
@role_required("admin", "manager", "livreur", "caissier")
def create_client():
    """
    Créer un nouveau client
    ---
    tags:
      - Clients
    summary: Création d'un nouveau client
    description: Cette route permet aux utilisateurs autorisés (admin, manager, livreur) de créer un nouveau client en fournissant les informations requises via un formulaire.

    consumes:
      - multipart/form-data

    parameters:
      - in: formData
        name: nom
        type: string
        required: true
        description: Nom complet du client (au moins 2 caractères)
      - in: formData
        name: telephone
        type: string
        required: true
        description: Numéro de téléphone du client (au moins 8 chiffres)
      - in: formData
        name: email
        type: string
        required: false
        description: Adresse email du client (optionnelle)
      - in: formData
        name: ville
        type: string
        required: true
        description: Ville du client
      - in: formData
        name: quartier
        type: string
        required: true
        description: Quartier ou zone de résidence
      - in: formData
        name: magasin_id
        type: string
        required: true
        description: ID du magasin associé au client
      - in: formData
        name: photo_profil
        type: file
        required: false
        description: Photo de profil du client (format image)
      - in: formData
        name: montant_en_caisse
        type: number
        required: false
        description: Montant initial en caisse pour ce client

    responses:
      201:
        description: Client créé avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: Client créé avec succès
            client_id:
              type: string
              example: 64b8fae5e3b9ab9c8dbf8f67
      400:
        description: Données invalides (champs requis manquants ou mal formatés)
      401:
        description: Non autorisé – Jeton JWT manquant ou invalide
      500:
        description: Erreur interne lors de la création du client
    """
    data = dict(request.form)
    data = inject_magasin_id(data)
    created_by = get_jwt_identity()
    data["created_by"] = created_by  # Ajoute l'ID de l'utilisateur qui crée le client
    file = request.files.get("photo_profil")
    # Le formulaire envoyait la photo sous « image » : on accepte les deux noms.
    file = file or request.files.get("image")
    # Dette d'avant Lumistock, saisie dès la création (facultative).
    dette = {cle: data.pop(cle, None) for cle in ("dette_ouverture", "dette_ouverture_date", "dette_ouverture_note")}
    montant_dette = None
    if dette["dette_ouverture"] not in (None, ""):
        try:
            montant_dette = float(dette["dette_ouverture"])
        except (TypeError, ValueError):
            return jsonify({"message": "Dette d'ouverture invalide."}), 400
        if montant_dette < 0:
            return jsonify({"message": "La dette d'ouverture ne peut pas être négative."}), 400
    if get_jwt().get("role") == "caissier":
        montant_dette = None  # la caisse n'enregistre pas de dettes
    errors = CreateClientSchema().validate(data)
    if errors:
        return jsonify({"message": _message_erreurs(errors), "errors": errors}), 400
    result, status = create_client_service(data, photo_file=file)
    if status == 201 and montant_dette and result.get("client_id"):
        from models.client_model import ClientModel
        try:
            compte_client_service.definir_dette_ouverture(
                ClientModel.get_client_by_id(result["client_id"]), montant_dette,
                dette["dette_ouverture_date"] or None, dette["dette_ouverture_note"], created_by)
        except compte_client_service.ErreurCompte as e:
            return jsonify({"message": str(e)}), e.statut
    return jsonify(result), status



@client_bp.route("/", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur", "staff", "caissier")
def get_clients():
    """
    Récupérer la liste de tous les clients
    ---
    tags:
      - Clients
    summary: Obtenir la liste des clients
    description: Cette route permet de récupérer la liste de tous les clients associés à un magasin spécifique selon les droits de l'utilisateur connecté.

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Jeton JWT de l'utilisateur (format **Bearer &lt;token&gt;**)
        default: Bearer

    responses:
      200:
        description: Liste des clients récupérée avec succès
        schema:
          type: object
          properties:
            clients:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: string
                    example: "64b8fae5e3b9ab9c8dbf8f67"
                  nom:
                    type: string
                    example: "Jean Dupont"
                  telephone:
                    type: string
                    example: "+237690123456"
                  email:
                    type: string
                    example: "jean@example.com"
                  ville:
                    type: string
                    example: "Yaoundé"
                  quartier:
                    type: string
                    example: "Bastos"
                  magasin_id:
                    type: string
                    example: "62de123abc12e123de123abc"
      401:
        description: Non autorisé – Jeton manquant ou invalide
      500:
        description: Erreur interne lors de la récupération des clients
    """
    data = {}
    data = inject_magasin_id(data)
    magasin_id = data.get("magasin_id")
    
    # Récupération de l'ordre de tri (par défaut -1 pour plus récent)
    sort_order = request.args.get("sort", "desc")
    sort_order = -1 if sort_order == "desc" else 1
    
    result = get_all_clients_service(magasin_id, sort_order)
    return jsonify(result), 200




# ---------------------------------------------------------
# ROUTES CORBEILLE POUR CLIENTS
# ---------------------------------------------------------

@client_bp.route("/corbeille", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_corbeille_clients_route():
    """
    Récupérer les clients supprimés (Corbeille)
    """
    try:
        data = inject_magasin_id({})
        magasin_id = data["magasin_id"]
    except Exception as e:
        return jsonify({"error": str(e)}), 401
        
    from models.client_model import ClientModel
    clients = ClientModel.get_corbeille_clients(magasin_id)
    # Sérialisation
    for c in clients:
        c["_id"] = str(c["_id"])
        c["magasin_id"] = str(c["magasin_id"])
    
    return jsonify(clients), 200

@client_bp.route("/corbeille/<client_id>/restaurer", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("clients.supprimer")
def restaurer_client(client_id):
    """
    Restaurer un client supprimé
    """
    _client, erreur = _client_du_magasin(client_id, corbeille=True)
    if erreur:
        return erreur
    result, status_code = restaurer_client_service(client_id)
    return jsonify(result), status_code

@client_bp.route("/<client_id>", methods=["GET"])
@jwt_required() 
@role_required("admin", "manager", "livreur", "staff")
def get_client(client_id):
    """
    Récupérer un client par ID
    ---
    tags:
      - Clients
    summary: Détails d’un client
    description: Cette route permet de récupérer les informations détaillées d’un client à partir de son identifiant unique.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT de l'utilisateur (format **Bearer &lt;token&gt;**)
        default: Bearer
      - in: path
        name: client_id
        type: string
        required: true
        description: ID unique du client à récupérer

    responses:
      200:
        description: Détails du client récupérés avec succès
        schema:
          type: object
          properties:
            id:
              type: string
              example: "64b8fae5e3b9ab9c8dbf8f67"
            nom:
              type: string
              example: "Jean Dupont"
            telephone:
              type: string
              example: "+237690123456"
            email:
              type: string
              example: "jean@example.com"
            ville:
              type: string
              example: "Douala"
            quartier:
              type: string
              example: "Akwa"
            magasin_id:
              type: string
              example: "62de123abc12e123de123abc"
      404:
        description: Client non trouvé
      401:
        description: Non autorisé – Jeton manquant ou invalide
      500:
        description: Erreur interne lors de la récupération du client
    """
    client, erreur = _client_du_magasin(client_id)
    if erreur:
        return erreur
    client = get_client_by_id_service(client_id)
    client["magasin_id"] = str(client.get("magasin_id"))
    client["dette"] = dette_client(client_id, client["magasin_id"])
    # Fiche complète pour le détail : date ISO et auteur de la création.
    if hasattr(client.get("created_at"), "isoformat"):
        client["created_at"] = client["created_at"].isoformat()
    auteur = UserModel.get_user_by_id(client["created_by"]) if client.get("created_by") else None
    client["created_by"] = str(client["created_by"]) if client.get("created_by") else None
    client["created_by_nom"] = f"{auteur.get('first_name', '')} {auteur.get('name', '')}".strip() if auteur else None
    client["par_defaut"] = client.get("nom") == "Autres"
    return jsonify(client), 200



#Mettre à jour un client

@client_bp.route("/<client_id>", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def update_client(client_id):
    """
    Mettre à jour les informations d’un client
    ---
    tags:
      - Clients
    summary: Modification d’un client
    description: Cette route permet de mettre à jour les informations d’un client existant. Seuls les champs envoyés seront modifiés.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT de l'utilisateur (format **Bearer &lt;token&gt;**)
        default: Bearer
      - in: path
        name: client_id
        type: string
        required: true
        description: Identifiant unique du client à mettre à jour
      - in: body
        name: body
        required: true
        description: Champs à mettre à jour (un ou plusieurs)
        schema:
          type: object
          properties:
            nom:
              type: string
              example: "Jean Paul"
            telephone:
              type: string
              example: "+237699123456"
            email:
              type: string
              example: "jean.paul@example.com"
            ville:
              type: string
              example: "Yaoundé"
            quartier:
              type: string
              example: "Nlongkak"

    responses:
      200:
        description: Client mis à jour avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: Client mis à jour
      400:
        description: Données invalides
      401:
        description: Non autorisé – Jeton manquant ou invalide
      404:
        description: Client non trouvé
      500:
        description: Erreur interne lors de la mise à jour
    """
    client, erreur = _client_du_magasin(client_id)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    errors = UpdateClientSchema().validate(data)
    if errors:
        return jsonify({"message": _message_erreurs(errors), "errors": errors}), 400
    result, status = update_client_service(client, data)
    return jsonify(result), status


# Supprimer un client par ID

@client_bp.route("/<client_id>", methods=["DELETE"])
@role_required("admin", "manager")
@permission_requise("clients.supprimer")
@jwt_required()
def delete_client(client_id):
    """
    Supprimer un client par ID
    ---
    tags:
      - Clients
    summary: Suppression d’un client
    description: Cette route permet de supprimer un client existant à partir de son identifiant unique. L’opération est réservée aux utilisateurs autorisés.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT de l'utilisateur (format **Bearer &lt;token&gt;**)
        default: Bearer
      - in: path
        name: client_id
        type: string
        required: true
        description: Identifiant unique du client à supprimer

    responses:
      200:
        description: Client supprimé avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: Client supprimé
      401:
        description: Non autorisé – Jeton manquant ou invalide
      404:
        description: Client non trouvé
      500:
        description: Erreur serveur lors de la suppression
    """
    client, erreur = _client_du_magasin(client_id)
    if erreur:
        return erreur
    result, status = delete_client_service(client)
    return jsonify(result), status

@client_bp.route("/<client_id>/ajouter-caisse", methods=["POST"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def ajouter_montant_caisse(client_id):
    """
    Ajouter un montant au compte (caisse) du client
    ---
    tags:
      - Clients
    summary: Créditer le compte d'un client
    description: Cette route permet d'ajouter une somme d'argent au montant en caisse d'un client. Cet argent pourra être utilisé pour payer de futures livraisons.
    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT de l'utilisateur
        default: Bearer
      - in: path
        name: client_id
        type: string
        required: true
        description: ID unique du client
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            montant:
              type: number
              example: 50000
    responses:
      200:
        description: Montant ajouté avec succès
      400:
        description: Montant invalide
      404:
        description: Client non trouvé
    """
    data = request.get_json(force=True)
    montant = data.get("montant")
    if montant is None:
        return jsonify({"error": "Le champ 'montant' est requis"}), 400
    _client, erreur = _client_du_magasin(client_id)
    if erreur:
        return erreur
    result, status_code = ajouter_montant_caisse_service(client_id, montant, data.get("mode_paiement") or "cash",
                                                         get_jwt_identity())
    return jsonify(result), status_code

@client_bp.route("/<client_id>/retirer-caisse", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("clients.retirer_avance")
def retirer_montant_caisse(client_id):
    """
    Retirer un montant du compte (caisse) du client
    """
    data = request.get_json(force=True)
    montant = data.get("montant")
    if montant is None:
        return jsonify({"error": "Le champ 'montant' est requis"}), 400
    _client, erreur = _client_du_magasin(client_id)
    if erreur:
        return erreur
    result, status_code = retirer_montant_caisse_service(client_id, montant, data.get("mode_paiement") or "cash",
                                                         get_jwt_identity())
    return jsonify(result), status_code
  
# Récupérer tous les clients d'un livreur avec le nombre de livraisons et le montant total

@client_bp.route("/livreur/", methods=["GET"])
@jwt_required()
@role_required("livreur")
def recuperer_clients_livreur():
    """
    Récupérer les clients d’un livreur avec statistiques
    ---
    tags:
      - Clients
    summary: Liste des clients assignés à un livreur
    description: Cette route retourne la liste des clients associés au livreur connecté, avec le nombre total de livraisons et le montant total généré pour chaque client.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT du livreur (format **Bearer &lt;token&gt;**)
        default: Bearer

    responses:
      200:
        description: Liste des clients récupérée avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              id:
                type: string
                example: "64b8fae5e3b9ab9c8dbf8f67"
              nom:
                type: string
                example: "Marie Mbarga"
              telephone:
                type: string
                example: "+237690112233"
              ville:
                type: string
                example: "Douala"
              quartier:
                type: string
                example: "Bonamoussadi"
              nombre_livraisons:
                type: integer
                example: 12
              montant_total:
                type: number
                format: float
                example: 258000.0
      401:
        description: Non autorisé – Jeton manquant ou invalide
      403:
        description: Accès refusé – Rôle non autorisé
      500:
        description: Erreur serveur lors de la récupération
    """
    current_user_id = get_jwt_identity()
    clients = get_clients_livreur(current_user_id)
    return jsonify(clients), 200

  

@client_bp.route("/manager/", methods=["GET"])
@jwt_required()
@role_required("manager", "admin")
def recuperer_clients_magasin():
    """
    Récupérer les clients du magasin avec filtres dynamiques
    ---
    tags:
      - Clients
    summary: Recherche des clients du magasin avec filtres
    description: Cette route permet aux managers et admins de récupérer la liste des clients d’un magasin en appliquant des filtres dynamiques sur plusieurs champs.

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Jeton JWT de l'utilisateur (format **Bearer &lt;token&gt;**)
        default: Bearer
      - name: nom
        in: query
        type: string
        required: false
        description: Filtrer par nom du client (recherche partielle possible)
      - name: telephone
        in: query
        type: string
        required: false
        description: Filtrer par numéro de téléphone
      - name: quartier
        in: query
        type: string
        required: false
        description: Filtrer par quartier
      - name: ville
        in: query
        type: string
        required: false
        description: Filtrer par ville
      - name: created_by_name
        in: query
        type: string
        required: false
        description: Filtrer par nom de la personne ayant créé le client

    responses:
      200:
        description: Liste filtrée des clients récupérée avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              id:
                type: string
                example: "64b8fae5e3b9ab9c8dbf8f67"
              nom:
                type: string
                example: "Pauline Ngono"
              telephone:
                type: string
                example: "+237699887766"
              ville:
                type: string
                example: "Yaoundé"
              quartier:
                type: string
                example: "Nkolndongo"
              created_by_name:
                type: string
                example: "Manager Jean"
      401:
        description: Non autorisé – Jeton manquant ou invalide
      403:
        description: Accès refusé – Rôle non autorisé
      500:
        description: Erreur serveur lors de la récupération des clients
    """
    filters = {
        "nom": request.args.get("nom"),
        "telephone": request.args.get("telephone"),
        "quartier": request.args.get("quartier"),
        "ville": request.args.get("ville"),
        "created_by_name": request.args.get("created_by_name"),
    }

    clients = get_clients_magasins(filters)
    return jsonify(clients), 200



@client_bp.route("/manager/export", methods=["GET"])
@jwt_required()
@role_required("manager", "admin", "livreur")
def export_clients_magasin_excel():
    """
    Exporter les clients du magasin avec leurs statistiques en Excel
    ---
    tags:
      - Export
      - Clients
    summary: Export Excel des clients du magasin avec filtres et stats
    description: Cette route permet d’exporter au format Excel la liste des clients d’un magasin, incluant leurs statistiques. Les managers, admins et livreurs peuvent utiliser cette fonctionnalité. Les filtres sont appliqués si spécifiés.

    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Jeton JWT de l'utilisateur (format **Bearer &lt;token&gt;**)
        default: Bearer
      - name: nom
        in: query
        type: string
        required: false
        description: Filtrer par nom du client (recherche partielle)
      - name: telephone
        in: query
        type: string
        required: false
        description: Filtrer par numéro de téléphone
      - name: quartier
        in: query
        type: string
        required: false
        description: Filtrer par quartier
      - name: ville
        in: query
        type: string
        required: false
        description: Filtrer par ville
      - name: created_by_name
        in: query
        type: string
        required: false
        description: Filtrer par nom de la personne ayant créé le client

    responses:
      200:
        description: Fichier Excel généré et téléchargé avec succès
        schema:
          type: file
      401:
        description: Non autorisé – Jeton manquant ou invalide
      403:
        description: Accès refusé – Rôle non autorisé
      500:
        description: Erreur serveur lors de l’exportation
    """
    filters = {
        "nom": request.args.get("nom"),
        "telephone": request.args.get("telephone"),
        "quartier": request.args.get("quartier"),
        "ville": request.args.get("ville"),
        "created_by_name": request.args.get("created_by_name"),
    }

    user_id = get_jwt_identity()
    user = UserModel.get_user_by_id(user_id)
        
    if user['role'] in ('livreur', 'gerant'):
        clients = get_clients_livreur(user_id)
    else:
        clients = get_clients_magasins(filters)

    
    # Génération du fichier Excel dans un Buffer
    # Fichier généré une seule fois et envoyé depuis la mémoire (il était
    # généré deux fois et une copie restait sur le serveur à chaque export).
    buffer = export_clients_to_excel(clients)
    buffer.seek(0)
    filename = f"clients_magasin_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )





# Récupérer tous les clients d'un livreur avec le nombre de livraisons et le montant total classé par ordre décroisssant
# Retourne les meilleur clients

@client_bp.route("best/livreur/", methods=["GET"])
@jwt_required()
@role_required("livreur")
def get_meilleurs_clients_livreurs():
    """
    Récupérer les meilleurs clients d’un livreur
    ---
    tags:
      - Clients
    summary: Liste des meilleurs clients d’un livreur
    description: Cette route retourne les clients d’un livreur avec leurs statistiques de nombre de livraisons et montant total, triés par performance.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT du livreur (format **Bearer &lt;token&gt;**)
        default: Bearer

    responses:
      200:
        description: Liste des meilleurs clients récupérée avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              id:
                type: string
                example: "64b8fae5e3b9ab9c8dbf8f67"
              nom:
                type: string
                example: "Sophie Nguimfack"
              telephone:
                type: string
                example: "+237690112233"
              nombre_livraisons:
                type: integer
                example: 25
              montant_total:
                type: number
                format: float
                example: 523500.0
      401:
        description: Non autorisé – Jeton manquant ou invalide
      403:
        description: Accès refusé – Rôle non autorisé
      500:
        description: Erreur serveur lors de la récupération des données
    """
    current_user_id = get_jwt_identity()
    clients = get_meilleurs_clients_livreur(current_user_id)
    return jsonify(clients), 200

  
@client_bp.route("/best/managers/", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_meilleurs_clients():
    """
    Récupérer les meilleurs clients avec statistiques globales
    ---
    tags:
      - Clients
    summary: Liste des meilleurs clients pour admin/manager
    description: Cette route retourne la liste des meilleurs clients selon le nombre de livraisons et le montant total, accessible aux admins et managers.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT de l'utilisateur (format **Bearer &lt;token&gt;**)
        default: Bearer

    responses:
      200:
        description: Liste des meilleurs clients récupérée avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              id:
                type: string
                example: "64b8fae5e3b9ab9c8dbf8f67"
              nom:
                type: string
                example: "Alice Tchoumi"
              telephone:
                type: string
                example: "+237699001122"
              nombre_livraisons:
                type: integer
                example: 40
              montant_total:
                type: number
                format: float
                example: 1023500.0
      401:
        description: Non autorisé – Jeton manquant ou invalide
      403:
        description: Accès refusé – Rôle non autorisé
      500:
        description: Erreur serveur lors de la récupération des données
    """
    current_user_id = get_jwt_identity()
    clients = get_meilleurs_clients_total()
    return jsonify(clients), 200


@client_bp.route("/debt/livreur/", methods=["GET"])
@jwt_required()
@role_required("livreur")
def get_clients_debt_livreur():
    """
    Récupérer les clients d'un livreur avec leur dette totale
    ---
    tags:
      - Clients
    summary: Liste des clients avec dette pour un livreur
    description: >
      Cette route retourne la liste des clients d'un livreur avec leur dette totale.
      La dette est calculée comme la différence entre le montant total et le montant payé
      pour toutes les livraisons du client. Seuls les clients avec une dette > 0 sont retournés.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT du livreur (format **Bearer &lt;token&gt;**)
        default: Bearer

    responses:
      200:
        description: Liste des clients avec dette récupérée avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              client_id:
                type: string
                example: "64b8fae5e3b9ab9c8dbf8f67"
              nom:
                type: string
                example: "Jean Dupont"
              telephone:
                type: string
                example: "+237690123456"
              ville:
                type: string
                example: "Yaoundé"
              quartier:
                type: string
                example: "Bastos"
              nombre_livraisons:
                type: integer
                example: 5
              montant_total:
                type: number
                format: float
                example: 150000.0
              montant_paye:
                type: number
                format: float
                example: 100000.0
              dette:
                type: number
                format: float
                example: 50000.0
      401:
        description: Non autorisé – Jeton manquant ou invalide
      403:
        description: Accès refusé – Rôle non autorisé
      500:
        description: Erreur serveur lors de la récupération des données
    """
    livreur_id = get_jwt_identity()
    clients = get_clients_with_debt_by_livreur(livreur_id)
    return jsonify(clients), 200


@client_bp.route("/debt/manager/", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_clients_debt_manager():
    """
    Récupérer tous les clients du magasin avec leur dette totale
    ---
    tags:
      - Clients
    summary: Liste des clients avec dette pour admin/manager
    description: >
      Cette route retourne la liste de tous les clients du magasin avec leur dette totale.
      La dette est calculée comme la différence entre le montant total et le montant payé
      pour toutes les livraisons du client. Seuls les clients avec une dette > 0 sont retournés.
      Les informations du livreur associé sont également incluses.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT de l'utilisateur (format **Bearer &lt;token&gt;**)
        default: Bearer

    responses:
      200:
        description: Liste des clients avec dette récupérée avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              client_id:
                type: string
                example: "64b8fae5e3b9ab9c8dbf8f67"
              nom:
                type: string
                example: "Marie Mbarga"
              telephone:
                type: string
                example: "+237690112233"
              ville:
                type: string
                example: "Douala"
              quartier:
                type: string
                example: "Bonamoussadi"
              nombre_livraisons:
                type: integer
                example: 8
              montant_total:
                type: number
                format: float
                example: 250000.0
              montant_paye:
                type: number
                format: float
                example: 180000.0
              dette:
                type: number
                format: float
                example: 70000.0
              livreur_id:
                type: string
                example: "64b8fae5e3b9ab9c8dbf8f68"
              livreur_nom:
                type: string
                example: "Kamga"
              livreur_prenom:
                type: string
                example: "Paul"
      401:
        description: Non autorisé – Jeton manquant ou invalide
      403:
        description: Accès refusé – Rôle non autorisé
      500:
        description: Erreur serveur lors de la récupération des données
    """
    data = {}
    data = inject_magasin_id(data)
    magasin_id = data.get("magasin_id")
    clients = get_clients_with_debt_by_magasin(magasin_id)
    return jsonify(clients), 200


@client_bp.route("/debt/top5/livreur/", methods=["GET"])
@jwt_required()
@role_required("livreur")
def get_top5_clients_debt_livreur():
    """
    Récupérer les 5 clients d'un livreur avec le plus de dettes
    ---
    tags:
      - Clients
    summary: Top 5 clients avec le plus de dettes (Livreur)
    responses:
      200:
        description: Top 5 récupéré avec succès
      401:
        description: Non autorisé
      403:
        description: Accès refusé
    """
    livreur_id = get_jwt_identity()
    clients = get_clients_with_debt_by_livreur(livreur_id)
    return jsonify(clients[:5]), 200


@client_bp.route("/debt/top5/manager/", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def get_top5_clients_debt_manager():
    """
    Récupérer les 5 clients du magasin avec le plus de dettes
    ---
    tags:
      - Clients
    summary: Top 5 clients avec le plus de dettes (Manager)
    responses:
      200:
        description: Top 5 récupéré avec succès
      401:
        description: Non autorisé
      403:
        description: Accès refusé
    """
    data = {}
    data = inject_magasin_id(data)
    magasin_id = data.get("magasin_id")
    
    # Gestion du rôle livreur
    from flask_jwt_extended import get_jwt
    claims = get_jwt()
    livreur_id = None
    if claims.get("role") in ("livreur", "gerant"):
        livreur_id = get_jwt_identity()
        
    clients = get_clients_with_debt_by_magasin(magasin_id, livreur_id=livreur_id)
    return jsonify(clients[:5]), 200


@client_bp.route("/debt/statistics/", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def get_debt_statistics_route():
    """
    Récupérer les statistiques de dette agrégées par période
    ---
    tags:
      - Clients
    summary: Statistiques de dette par période pour admin/manager
    description: >
      Cette route retourne les statistiques de dette totale du magasin, agrégées par période
      (jour, semaine, mois, année). Permet de filtrer par client et par plage de dates.
      Seuls les admins et managers peuvent accéder à cette route.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT de l'utilisateur (format **Bearer &lt;token&gt;**)
        default: Bearer
      - in: query
        name: period
        type: string
        required: true
        description: Type d'agrégation (day, week, month, year)
        enum: [day, week, month, year]
        default: day
      - in: query
        name: client_id
        type: string
        required: false
        description: ID du client pour filtrer les statistiques (optionnel)
      - in: query
        name: date_debut
        type: string
        required: false
        description: Date de début au format YYYY-MM-DD (optionnel)
      - in: query
        name: date_fin
        type: string
        required: false
        description: Date de fin au format YYYY-MM-DD (optionnel)

    responses:
      200:
        description: Statistiques de dette récupérées avec succès
        schema:
          type: object
          properties:
            period_type:
              type: string
              example: "month"
            magasin_id:
              type: string
              example: "62de123abc12e123de123abc"
            client_id:
              type: string
              example: null
            date_debut:
              type: string
              example: null
            date_fin:
              type: string
              example: null
            statistics:
              type: array
              items:
                type: object
                properties:
                  period:
                    type: string
                    example: "2026-02"
                  montant_total:
                    type: number
                    format: float
                    example: 500000.0
                  montant_paye:
                    type: number
                    format: float
                    example: 350000.0
                  dette:
                    type: number
                    format: float
                    example: 150000.0
                  nombre_livraisons:
                    type: integer
                    example: 15
            total_dette:
              type: number
              format: float
              example: 200000.0
            total_montant:
              type: number
              format: float
              example: 950000.0
            total_paye:
              type: number
              format: float
              example: 750000.0
      400:
        description: Paramètres invalides (period manquant ou invalide, format de date incorrect)
      401:
        description: Non autorisé – Jeton manquant ou invalide
      403:
        description: Accès refusé – Rôle non autorisé
      500:
        description: Erreur serveur lors de la récupération des statistiques
    """
    from datetime import datetime
    
    # Récupération des paramètres
    period = request.args.get("period")
    client_id = request.args.get("client_id")
    date_debut_str = request.args.get("date_debut")
    date_fin_str = request.args.get("date_fin")
    
    # Validation du paramètre period
    if not period:
        return jsonify({"error": "Le paramètre 'period' est requis"}), 400
    
    if period not in ['day', 'week', 'month', 'year']:
        return jsonify({"error": "Le paramètre 'period' doit être 'day', 'week', 'month' ou 'year'"}), 400
    
    # Conversion des dates si fournies
    date_debut = None
    date_fin = None
    
    if date_debut_str:
        try:
            date_debut = datetime.strptime(date_debut_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({"error": "Format de date_debut invalide. Utilisez YYYY-MM-DD"}), 400
    
    if date_fin_str:
        try:
            date_fin = datetime.strptime(date_fin_str, '%Y-%m-%d')
            # Ajouter 23:59:59 pour inclure toute la journée
            date_fin = date_fin.replace(hour=23, minute=59, second=59)
        except ValueError:
            return jsonify({"error": "Format de date_fin invalide. Utilisez YYYY-MM-DD"}), 400
    
    # Récupération du magasin_id
    data = {}
    data = inject_magasin_id(data)
    magasin_id = data.get("magasin_id")
    
    # Gestion du rôle livreur
    from flask_jwt_extended import get_jwt
    claims = get_jwt()
    livreur_id = None
    if claims.get("role") in ("livreur", "gerant"):
        livreur_id = get_jwt_identity()
    
    # Appel du service
    result = get_debt_statistics(
        magasin_id=magasin_id,
        period=period,
        client_id=client_id,
        date_debut=date_debut,
        date_fin=date_fin,
        livreur_id=livreur_id
    )
    
    # Enrichir la réponse avec les métadonnées
    response = {
        "period_type": period,
        "magasin_id": magasin_id,
        "client_id": client_id,
        "date_debut": date_debut_str,
        "date_fin": date_fin_str,
        **result
    }
    
    
    return jsonify(response), 200

  
@client_bp.route("/<client_id>/debt/details", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur", "staff")
def get_client_debt_details(client_id):
    """
    Récupérer l'historique détaillé des dettes d'un client
    ---
    tags:
      - Clients
    summary: Détails de la dette d'un client
    description: >
      Cette route retourne l'historique de toutes les livraisons non soldées pour un client spécifique,
      avec les détails des produits, dates, montants totaux et montants payés. 
      Permet de tracer l'origine de la dette totale d'un client.

    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT de l'utilisateur (format **Bearer &lt;token&gt;**)
        default: Bearer
      - in: path
        name: client_id
        type: string
        required: true
        description: ID unique du client

    responses:
      200:
        description: Historique récupéré avec succès
        schema:
          type: object
          properties:
            client_id:
              type: string
            client_nom:
              type: string
            total_dette:
              type: number
            historique:
              type: array
              items:
                type: object
      401:
        description: Non autorisé
      404:
        description: Client introuvable
      500:
        description: Erreur serveur
    """
    _client, erreur = _client_du_magasin(client_id)
    if erreur:
        return erreur
    data = {}
    data = inject_magasin_id(data)
    magasin_id = data.get("magasin_id")
    
    result = get_client_debt_details_service(client_id, magasin_id)
    return jsonify(result), 200


@client_bp.route("/<client_id>/livraisons", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur", "staff")
def get_client_livraisons(client_id):
    """
    Récupérer toutes les livraisons d'un client
    ---
    tags:
      - Clients
      - Livraison
    summary: Historique des livraisons d'un client
    description: Cette route permet de récupérer toutes les livraisons effectuées par un client spécifique, triées par date de création décroissante.
    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Jeton JWT de l'utilisateur
        default: Bearer
      - in: path
        name: client_id
        type: string
        required: true
        description: ID unique du client
    responses:
      200:
        description: Liste des livraisons récupérée avec succès
        schema:
          type: array
          items:
            type: object
            properties:
              id:
                type: string
                example: "64faabb10c5e1a0034c0ab12"
              montant_total:
                type: number
                example: 15000.0
              statut_paiement:
                type: string
                example: "payé"
              date_commande:
                type: string
                format: date-time
                example: "2024-03-16T10:00:00"
      401:
        description: Non autorisé
      404:
        description: Client non trouvé ou aucune livraison
    """
    _client, erreur = _client_du_magasin(client_id)
    if erreur:
        return erreur
    claims = get_jwt()
    role = claims.get("role")
    current_user_id = get_jwt_identity()
    
    livreur_id = current_user_id if role in ("livreur", "gerant") else None
    
    result, status_code = get_client_livraisons_service(client_id, livreur_id=livreur_id)
    return jsonify(result), status_code



# --------------------------------------------------------------------------- compte du client
def _est_gerant():
    return get_jwt().get("role") in ("livreur", "gerant")


@client_bp.route("/<client_id>/compte", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def compte_client(client_id):
    """Situation du client : dette d'ouverture, ventes impayées (les plus anciennes
    d'abord), total dû et avance sur son compte. Un gérant ne voit que ses ventes."""
    client, erreur = _client_du_magasin(client_id)
    if erreur:
        return erreur
    livreur_id = get_jwt_identity() if _est_gerant() else None
    return jsonify(compte_client_service.situation_client(client, livreur_id)), 200


@client_bp.route("/<client_id>/dette-ouverture", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def definir_dette_ouverture(client_id):
    """Dette d'avant Lumistock (cahier de crédit) : { montant, date_origine, note }.

    Un montant de 0 supprime la dette (si rien n'a encore été réglé). Un gérant
    peut la saisir pour un client qui n'en a pas, pas la modifier ensuite.
    """
    from services.client_service import NOM_CLIENT_PAR_DEFAUT

    client, erreur = _client_du_magasin(client_id)
    if erreur:
        return erreur
    if client.get("nom") == NOM_CLIENT_PAR_DEFAUT:
        return jsonify({"error": "Le client de passage ne peut pas avoir de dette."}), 400
    data = request.get_json(silent=True) or {}
    try:
        compte_client_service.definir_dette_ouverture(
            client, data.get("montant"), data.get("date_origine"), data.get("note"),
            get_jwt_identity(), peut_modifier=droit_courant("clients.dette_ouverture"))
    except compte_client_service.ErreurCompte as e:
        return jsonify({"error": str(e)}), e.statut
    livreur_id = get_jwt_identity() if _est_gerant() else None
    return jsonify({"message": "Dette d'ouverture enregistrée.",
                    "compte": compte_client_service.situation_client(client, livreur_id)}), 200


@client_bp.route("/<client_id>/reglements", methods=["POST"])
@jwt_required()
@role_required("admin", "manager", "livreur")
def regler_client(client_id):
    """Encaisse un règlement du client : { montant, methode }.

    L'argent est réparti automatiquement : dette d'ouverture, puis ventes
    impayées de la plus ancienne à la plus récente ; le surplus va sur son
    compte. Un gérant ne règle que ses propres ventes.
    """
    from models.client_model import ClientModel

    client, erreur = _client_du_magasin(client_id)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    livreur_id = get_jwt_identity() if _est_gerant() else None
    try:
        montant, affectations, avance = compte_client_service.regler(
            client, data.get("montant"), data.get("methode") or "cash", get_jwt_identity(), livreur_id)
    except compte_client_service.ErreurCompte as e:
        return jsonify({"error": str(e)}), e.statut
    message = f"Règlement de {montant:g} enregistré"
    if avance > 0:
        message += f" ; {avance:g} crédités sur le compte du client"
    return jsonify({
        "message": message,
        "montant": montant,
        "affectations": affectations,
        "avance_creditee": avance,
        "compte": compte_client_service.situation_client(ClientModel.get_client_by_id(client_id), livreur_id),
    }), 200
