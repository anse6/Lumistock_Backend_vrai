from flask import Blueprint, request, jsonify
import json
from flask_jwt_extended import jwt_required, get_jwt_identity
from schemas.magasin_schema import CreateMagasinSchema, MagasinUpdateSchema, blank_to_none
from schemas.register_magasin_schema import RegisterMagasinSchema
from services.magasin_service import (
  create_magasin, 
  update_magasin_service, 
  get_magasin_service, 
  delete_magasin_service,
  delete_magasin_definitively_service,
  get_all_magasin_service,
  toggle_magasin_status_service,
  register_magasin_service
  )

from utils.decorators import staff_required, admin_required, role_required
from utils.decorators import permission_requise
from utils.inject_magasin_id import inject_magasin_id
from models.user_model import UserModel
from models.magasin_model import MagasinModel
from bson import ObjectId

magasin_bp = Blueprint("magasin", __name__, url_prefix="/magasin")

def _service_response(result):
    return jsonify(result), result.get("status", 500)


def _can_access_magasin(magasin_id):
    """Staff may inspect any store; all other roles are tenant-scoped."""
    user = UserModel.get_user_by_id(get_jwt_identity())
    if not user:
        return False
    return user.get("role") == "staff" or str(user.get("magasin_id")) == str(magasin_id)

# Créer un magasin uniqment par le staff
@magasin_bp.route("/create", methods=["POST"])
@jwt_required()
@staff_required
def create():
    """
    Créer un nouveau magasin avec sa licence et les informations de l’admin.
    ---
    tags:
      - Magasins
    consumes:
      - multipart/form-data
    parameters:
      - in: header
        name: Authorization
        required: true
        schema:
          type: string
        example: "Bearer votre.jwt.token"
      - in: formData
        name: denomination
        type: string
        required: true
        example: "Super Marché Wouri"
      - in: formData
        name: pays
        type: string
        required: true
        example: "Cameroun"
      - in: formData
        name: ville
        type: string
        required: true
        example: "Douala"
      - in: formData
        name: quartier
        type: string
        required: true
        example: "Akwa"
      - in: formData
        name: email
        type: string
        required: true
        example: "contact@wouri.com"
      - in: formData
        name: activité
        type: string
        required: true
        example: "Alimentation générale"
      - in: formData
        name: nombre_manager
        type: integer
        required: true
        example: 2
      - in: formData
        name: nombre_livreur
        type: integer
        required: true
        example: 10
      - in: formData
        name: admin_name
        type: string
        required: true
        example: Nguefack
      - in: formData
        name: admin_first_name
        type: string
        required: true
        example: Marius
      - in: formData
        name: admin_email
        type: string
        required: true
        description: >
          Email de l'administrateur. Aucun mot de passe : il reçoit un lien
          d'invitation pour choisir le sien. Facultatifs : admin_telephone,
          admin_sexe, admin_date_naissance, admin_fonction.
        example: marius.nguefack@exemple.com
      - in: formData
        name: image
        type: file
        required: false
        description: Logo ou image du magasin (format image)
    responses:
      200:
        description: Magasin créé avec succès
      400:
        description: Données manquantes ou invalides
      401:
        description: Non autorisé (staff uniquement)
    """
    try:
        data = request.form.to_dict()
        logo_file = request.files.get("image")

        # Administrateur envoyé à plat (admin_name, admin_email…), comme à
        # l'inscription publique. L'ancien format (objet JSON « admin ») est
        # encore accepté et converti.
        if "admin" in data:
            try:
                ancien = json.loads(data.pop("admin"))
            except (json.JSONDecodeError, TypeError):
                return jsonify({"errors": {"admin": ["Format JSON invalide"]}}), 400
            if not isinstance(ancien, dict):
                return jsonify({"errors": {"admin": ["Format JSON invalide"]}}), 400
            for cle, valeur in ancien.items():
                data.setdefault(f"admin_{cle}", valeur)


        # Valider les données
        schema = CreateMagasinSchema()
        data = blank_to_none(data, schema)
        errors = schema.validate(data)
        if errors:
            return jsonify({"errors": errors}), 400

        # Appeler le service
        result = create_magasin(data, logo_file)
        return jsonify(result), result.get("status", 200)

    except Exception as e:
        return jsonify({"message": f"Erreur serveur : {str(e)}"}), 500

# Route publique pour l'auto-inscription
@magasin_bp.route("/register", methods=["POST"])
def register():
    """
    S'inscrire publiquement pour créer un magasin.
    Le magasin sera créé en mode inactif et devra être validé par le staff.
    ---
    tags:
      - Magasins
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            denomination:
              type: string
            pays:
              type: string
            ville:
              type: string
            quartier:
              type: string
            email:
              type: string
            activité:
              type: string
            admin_name:
              type: string
            admin_first_name:
              type: string
            admin_email:
              type: string
    responses:
      201:
        description: Inscription réussie, en attente de validation
      400:
        description: Données invalides
    """
    try:
        # silent=True : une requête multipart (avec logo) ne doit pas lever 415.
        data = request.get_json(silent=True)
        if not data:
            data = request.form.to_dict()

        logo_file = request.files.get("image")

        # Anti-robots : champ invisible pour un humain (rempli = robot) et
        # limite d'inscriptions par adresse IP.
        from utils.rate_limit import limite_atteinte
        if str(data.pop("site_internet_societe", "") or "").strip():
            return jsonify({"message": "Inscription refusée."}), 400
        if limite_atteinte(f"inscription-ip:{request.remote_addr or 'inconnue'}", 5, 60 * 60):
            return jsonify({"message": "Trop d'inscriptions depuis cette connexion. Réessayez dans une heure."}), 429
        if isinstance(data.get("accepte_conditions"), str):
            data["accepte_conditions"] = data["accepte_conditions"].lower() in ("true", "1", "on", "oui")

        # Valider les données
        schema = RegisterMagasinSchema()
        data = blank_to_none(data, schema)
        errors = schema.validate(data)
        if errors:
            return jsonify({"errors": errors}), 400

        # Appeler le service d'inscription
        result = register_magasin_service(data, logo_file)
        return jsonify(result), result.get("status", 201)

    except Exception as e:
        return jsonify({"message": f"Erreur lors de l'inscription : {str(e)}"}), 500



# Mettre à jour les données d'un magasin (staff et admin)
@magasin_bp.route('/update/<string:magasin_id>', methods=['PUT'])
@jwt_required()
@role_required("staff", "admin", "manager")
def update_magasin(magasin_id):
    """
    Met à jour un magasin selon les permissions de l'utilisateur connecté.
    ---
    tags:
      - Magasins
    parameters:
      - name: magasin_id
        in: path
        required: true
        type: string
      - name: body
        in: body
        required: true
        schema:
          properties:
            denomination:
              type: string
            pays:
              type: string
            ville:
              type: string
            quartier:
              type: string
            email:
              type: string
            activité:
              type: string
            licence:
              type: object
              properties:
                max_managers:
                  type: integer
                max_livreurs:
                  type: integer
    security:
      - bearerAuth: []
    responses:
      200:
        description: Magasin mis à jour
      403:
        description: Accès non autorisé
    """
    user_id = get_jwt_identity()
    user = UserModel.get_user_by_id(user_id)
    if not user:
        return jsonify({"message": "Utilisateur non trouvé"}), 404
    # La fiche du magasin est modifiée par l'admin (ou le staff), et par le
    # manager si l'admin lui a donné ce droit (Paramètres > Accès).
    from services.permissions_service import a_le_droit
    if user["role"] not in ["admin", "staff", "manager"] or not a_le_droit(user, "magasin.modifier"):
        return jsonify({"message": "Vous n'avez pas le droit de modifier les informations du magasin."}), 403
    # Récupérer les données de la requête
   
    update_data = request.get_json(silent=True)
    logo_file = None
    if update_data is None:
        update_data = request.form.to_dict()
        logo_file = request.files.get("image")
        if isinstance(update_data.get("licence"), str):
            try:
                update_data["licence"] = json.loads(update_data["licence"])
            except json.JSONDecodeError:
                return jsonify({"errors": {"licence": ["Format JSON invalide"]}}), 400
    if not update_data:
        return jsonify({"errors": {"body": ["Aucune donnée à mettre à jour"]}}), 400
    schema = MagasinUpdateSchema()
    update_data = blank_to_none(update_data, schema)
    errors = schema.validate(update_data)
    if errors:
        return jsonify({"errors": errors}), 400

    if user["role"] != "staff" and not _can_access_magasin(magasin_id):
        return jsonify({"message": "Accès non autorisé pour ce magasin."}), 403

    return _service_response(update_magasin_service(magasin_id, update_data, user["role"], logo_file))
  
  
  
  
  #récupérer un magasin
@magasin_bp.route('/', methods=['GET'])

@role_required("admin", "staff", "manager", "livreur", "gerant")
@jwt_required()
def get_info_magasin():
    """
    Récupérer un magasin par son ID.
    ---
    tags:
      - Magasins
    parameters:
      - name: magasin_id
        in: path
        required: true
        type: string
    responses:
      200:
        description: Magasin trouvé
      404:
        description: Magasin non trouvé
    """
    try:
        data = inject_magasin_id({})
    except Exception:
        return jsonify({"message": "Aucun magasin n'est associé à cet utilisateur."}), 403
    magasin_id = data.get("magasin_id")
    if not magasin_id:
        return jsonify({"message": "ID de magasin manquant"}), 400
    return _service_response(get_magasin_service(magasin_id))
      
      
  #récupérer un magasin par son id
@magasin_bp.route('/<string:magasin_id>', methods=['GET'])
@role_required("admin", "staff", "manager", "livreur", "gerant")
@jwt_required()
def get_info_magasin_details(magasin_id):
    """
    Récupérer un magasin par son ID.
    ---
    tags:
      - Magasins
    parameters:
      - name: magasin_id
        in: path
        required: true
        type: string
    responses:
      200:
        description: Magasin trouvé
      404:
        description: Magasin non trouvé
    """
    if not magasin_id:
        return jsonify({"message": "ID de magasin manquant"}), 400
    user = UserModel.get_user_by_id(get_jwt_identity())
    if not user:
        return jsonify({"message": "Accès non autorisé pour ce magasin."}), 403
    is_staff = user.get("role") == "staff"
    if not is_staff and str(user.get("magasin_id")) != str(magasin_id):
        return jsonify({"message": "Accès non autorisé pour ce magasin."}), 403
    # Seul le staff consulte les magasins archivés (onglet « Archivés »).
    return _service_response(get_magasin_service(magasin_id, include_archived=is_staff))



# ---------------------------------------------------------------------------
# Méthode de valorisation des stocks (CMUP par défaut, FIFO, LIFO)
# ---------------------------------------------------------------------------
@magasin_bp.route('/parametres/stock', methods=['GET'])
@jwt_required()
@role_required("admin", "manager", "livreur", "gerant")
def get_parametres_stock():
    """
    Méthode de valorisation des stocks du magasin de l'utilisateur.
    ---
    tags:
      - Magasins
    responses:
      200:
        description: Méthode active et liste des méthodes disponibles
    """
    from services import valorisation_service
    try:
        magasin_id = inject_magasin_id({})["magasin_id"]
    except Exception:
        return jsonify({"message": "Aucun magasin n'est associé à cet utilisateur."}), 403
    from services import numeros_serie_service
    return jsonify({
        "methode": valorisation_service.get_methode(magasin_id),
        "methode_par_defaut": valorisation_service.METHODE_PAR_DEFAUT,
        "methodes": valorisation_service.lister_methodes(),
        "suivi_numeros_serie": numeros_serie_service.actif(magasin_id),
    }), 200


@magasin_bp.route('/parametres/stock', methods=['PUT'])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("stock.methode")
def update_parametres_stock():
    """
    Choisir la méthode de valorisation des stocks (CMUP, FIFO ou LIFO).
    La nouvelle méthode s'applique immédiatement à toutes les sorties suivantes
    (livraisons, avaries, ajustements) ; les sorties passées ne sont pas modifiées.
    ---
    tags:
      - Magasins
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            methode:
              type: string
              enum: [CMUP, FIFO, LIFO]
    responses:
      200:
        description: Méthode enregistrée
      400:
        description: Méthode inconnue
    """
    from services import valorisation_service
    try:
        magasin_id = inject_magasin_id({})["magasin_id"]
    except Exception:
        return jsonify({"message": "Aucun magasin n'est associé à cet utilisateur."}), 403
    from services import numeros_serie_service
    data = request.get_json(silent=True) or {}
    reponse = {}
    try:
        if "suivi_numeros_serie" in data:
            actif = numeros_serie_service.definir_actif(magasin_id, data["suivi_numeros_serie"])
            reponse.update({"suivi_numeros_serie": actif, "message": (
                "Numéros de série activés : cochez « Suivi par numéro de série » sur les articles concernés."
                if actif else "Numéros de série désactivés.")})
        if "methode" in data or not reponse:
            methode = valorisation_service.set_methode(magasin_id, data.get("methode"))
            reponse.update({"methode": methode,
                            "message": f"Méthode de valorisation : {valorisation_service.METHODES[methode]['nom']}."})
    except ValueError as error:
        return jsonify({"message": str(error)}), 400
    return jsonify(reponse), 200


# Supprimer un magasin
# ---------------------------------------------------------------------------
# Droits d'accès par rôle (Paramètres > Accès)
# ---------------------------------------------------------------------------
def _magasin_des_droits():
    from services.permissions_service import utilisateur_courant
    user = utilisateur_courant()
    if not user or not user.get("magasin_id"):
        return None, None
    return user, MagasinModel.get_collection().find_one({"_id": ObjectId(str(user["magasin_id"]))})


@magasin_bp.route('/permissions', methods=['GET'])
@jwt_required()
@role_required("admin", "manager")
def get_permissions():
    """Catalogue des droits et rôles qui les ont dans ce magasin."""
    from services.permissions_service import configuration
    user, magasin = _magasin_des_droits()
    if not magasin:
        return jsonify({"message": "Aucun magasin n'est associé à cet utilisateur."}), 403
    return jsonify({**configuration(magasin), "modifiable": user.get("role") == "admin"}), 200


@magasin_bp.route('/permissions', methods=['PUT'])
@jwt_required()
@role_required("admin")
def put_permissions():
    """Body : {"droits": {"produits.creer": {"manager": true}, ...}}."""
    from services.permissions_service import ErreurPermissions, enregistrer
    _, magasin = _magasin_des_droits()
    if not magasin:
        return jsonify({"message": "Aucun magasin n'est associé à cet utilisateur."}), 403
    try:
        config = enregistrer(magasin, (request.get_json(silent=True) or {}).get("droits"))
    except ErreurPermissions as e:
        return jsonify({"message": str(e)}), 400
    return jsonify({**config, "modifiable": True, "message": "Droits d'accès enregistrés."}), 200


@magasin_bp.route('/permissions', methods=['DELETE'])
@jwt_required()
@role_required("admin")
def reset_permissions():
    """Revient aux droits par défaut."""
    from services.permissions_service import reinitialiser
    _, magasin = _magasin_des_droits()
    if not magasin:
        return jsonify({"message": "Aucun magasin n'est associé à cet utilisateur."}), 403
    return jsonify({**reinitialiser(magasin), "modifiable": True, "message": "Droits par défaut rétablis."}), 200


@magasin_bp.route('/delete/<string:magasin_id>', methods=['DELETE'])
@jwt_required()
@role_required("staff")
def delete_magasin(magasin_id):
    """
    Supprimer un magasin par son ID.
    ---
    tags:
      - Magasins
    parameters:
      - in: header
        name: Authorization
        required: true
        schema:
          type: string
        example: "Bearer votre.jwt.token"
    
      - name: magasin_id
        in: path
        required: true
        type: string
    responses:
      200:
        description: Magasin supprimé avec succès
      404:
        description: Magasin non trouvé
    """
    user_id = get_jwt_identity()
    user = UserModel.get_user_by_id(user_id)
    
    if not user or user["role"] != "staff":
        return jsonify({"message": "Accès non autorisé"}), 403
    
    return _service_response(delete_magasin_service(magasin_id))


@magasin_bp.route('/delete/<string:magasin_id>/permanent', methods=['DELETE'])
@jwt_required()
@staff_required
def delete_magasin_definitively(magasin_id):
    """Supprimer définitivement un magasin déjà archivé."""
    return _service_response(delete_magasin_definitively_service(magasin_id))
      

# Récupérer tous les magasins
@magasin_bp.route('/all', methods=['GET'])
@jwt_required()
@role_required("staff")
def get_all_magasin():
    """
    Récupérer tous les magasins.
    ---
    tags:
      - Magasins
    parameters:
      - in: header
        name: Authorization
        required: true
        schema:
          type: string
        example: "Bearer votre.jwt.token"
    responses:
      200:
        description: Liste de tous les magasins
      500:
        description: Erreur serveur
    """
    try:
        include_archived = request.args.get("include_archived", "false").lower() == "true"
        return _service_response(get_all_magasin_service(include_archived=include_archived))
    except Exception as e:
        return jsonify({"message": f"Erreur lors de la récupération des magasins : {str(e)}"}), 500
      


@magasin_bp.route("<string:magasin_id>/toggle-status", methods=["PUT"])
@jwt_required()
@staff_required
def toggle_magasin_status_route(magasin_id):
    """
    Route pour activer ou désactiver un magasin.
    """
    return _service_response(toggle_magasin_status_service(magasin_id))
