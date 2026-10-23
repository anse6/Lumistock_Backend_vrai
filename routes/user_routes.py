from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from utils.decorators import admin_required, staff_required
from utils.decorators import permission_requise
from utils.inject_magasin_id import inject_magasin_id
from utils.decorators import role_required
from models.user_model import UserModel
# Imports are handled locally in route functions to avoid circular dependencies and ensure availability
from bson.objectid import ObjectId
from flask_jwt_extended import jwt_required, get_jwt_identity
import os
from werkzeug.utils import secure_filename


user_bp = Blueprint('user_routes', __name__, url_prefix='/users')


def _verifier_acces_utilisateur(user_id, ecriture=False):
    """Contrôle qu'un utilisateur connecté peut consulter ou gérer le compte `user_id`.

    - le staff gère tous les comptes de la plateforme ;
    - les autres rôles n'accèdent qu'aux comptes de leur propre magasin
      (un compte d'un autre magasin répond 404, comme s'il n'existait pas) ;
    - un manager ne peut pas modifier, supprimer ou réinitialiser un admin.

    :return: None si l'accès est autorisé, sinon la réponse d'erreur à renvoyer.
    """
    if not ObjectId.is_valid(user_id):
        return jsonify({"message": "ID d'utilisateur invalide."}), 400
    courant = UserModel.get_user_by_id(get_jwt_identity())
    if not courant:
        return jsonify({"message": "Utilisateur connecté introuvable."}), 403
    cible = UserModel.get_user_by_id(user_id)
    if not cible:
        return jsonify({"message": "Utilisateur non trouvé."}), 404
    if courant.get("role") == "staff":
        return None
    magasin_courant = courant.get("magasin_id")
    if not magasin_courant or str(cible.get("magasin_id")) != str(magasin_courant):
        return jsonify({"message": "Utilisateur non trouvé."}), 404
    if ecriture and courant.get("role") == "manager" and cible.get("role") == "admin":
        return jsonify({"message": "Un manager ne peut pas modifier un compte administrateur."}), 403
    return None


def _service_response(result):
    return jsonify(result), result.get("status", 200)


def _profil_demande(data, role):
    """Profil détaillé envoyé avec la création : (profil, None) ou (None, réponse d'erreur)."""
    from services.profil_utilisateur import ProfilInvalide, nettoyer_profil
    try:
        return nettoyer_profil(data, role), None
    except ProfilInvalide as e:
        return None, (jsonify({"error": str(e)}), 400)


def _enregistrer_profil(reponse, statut, profil):
    """Après une création réussie, enregistre le profil détaillé du nouveau compte."""
    if statut in (200, 201) and profil and isinstance(reponse, dict) and reponse.get("user_id"):
        UserModel.update_user(reponse["user_id"], profil)
@user_bp.route('/create_staff', methods=['POST'])
@jwt_required()
@staff_required
def create_staff():
    """
    Créer un compte staff
    ---
    tags:
      - Authentification
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - name
            - first_name
            - email
          properties:
            name:
              type: string
              example: "Nguefack"
            first_name:
              type: string
              example: "Marius"
            email:
              type: string
              example: "marius@gmail.com"
    responses:
      200:
        description: Compte staff créé
      400:
        description: Données invalides
    """
    # Réservé à un staff connecté. Le tout premier staff se crée sur le
    # serveur avec `python create_staff.py`. Le nouveau staff reçoit un lien
    # d'invitation pour choisir son mot de passe.
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    first_name = data.get('first_name')
    email = data.get('email')
    
    if not email or not name or not first_name:
        return jsonify({"error": "Nom, prénom et email sont requis."}), 400
    from services.user_service import create_staff_account
    return create_staff_account(name, first_name, email)


# Créer un admin

@user_bp.route('/create_admin', methods=['POST'])
@jwt_required()
@staff_required
def create_admin():
    """
    Créer un compte admin pour un magasin
    ---
    tags:
      - Authentification
    security:
      - BearerAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - name
            - first_name
            - email
            - magasin_id
          properties:
            name:
              type: string
              example: "Kamtchou"
            first_name:
              type: string
              example: "Yannick"
            email:
              type: string
              example: "yannick@admin.com"
            magasin_id:
              type: string
              example: "magasin123"
    responses:
      200:
        description: Compte admin créé
      400:
        description: Données manquantes
    """
    data = request.get_json()
    data = inject_magasin_id(data)
    name = data.get('name')
    first_name = data.get('first_name')
    email = data.get('email')
    magasin_id = data.get('magasin_id')
    
    if not email or not magasin_id:
        return jsonify({"error": "Email et magasin_id sont requis."}), 400
    from services.user_service import create_admin_account
    return create_admin_account(name, first_name, email, magasin_id)


# Le staff peut activer un compte un utilisateur via son email
@user_bp.route('/activate-user/<string:email>', methods=['POST'])
@staff_required
@jwt_required()
def activate_user(email):
    """
    Activer un compte utilisateur via son email.
    ---
    tags:
      - Utilisateurs
    parameters:
      - name: email
        in: path
        type: string
        required: true
        description: Email de l'utilisateur à activer
    responses:
      200:
        description: Compte activé avec succès
      404:
        description: Utilisateur introuvable
    """
    # Le staff ne choisit plus le mot de passe : le titulaire reçoit un lien d'invitation.
    from services.user_service import activate_user_by_email
    result = activate_user_by_email(email)    
    return jsonify(result), result.get("status", 200)


# Le staff peut activer un compte un utilisateur via son id
@user_bp.route('/activate-user/<string:id>', methods=['PUT'])
@role_required("admin", "staff", "manager")
@permission_requise("equipe.gerer")
@jwt_required()
def activate_user_with_id(id):
    """
    Activer un compte utilisateur via son email.
    ---
    tags:
      - Utilisateurs
    parameters:
      - name: id
        in: path
        type: string
        required: true
        description: id de l'utilisateur à activer
    responses:
      200:
        description: Compte activé avec succès
      404:
        description: Utilisateur introuvable
    """
    if str(id) == str(get_jwt_identity()):
        return jsonify({"message": "Vous ne pouvez pas désactiver votre propre compte."}), 400
    erreur = _verifier_acces_utilisateur(id, ecriture=True)
    if erreur:
        return erreur
    from services.user_service import set_user_activation
    result = set_user_activation(id, activate=True) 
       
    return jsonify(result), result.get("status", 200)

# Le staff peut desactiver un compte un utilisateur via son id
@user_bp.route('/deactivate-user/<string:id>', methods=['PUT'])
@role_required("admin", "staff", "manager")
@permission_requise("equipe.gerer")
@jwt_required()
def deactivate_user_with_id(id):
    """
    Activer un compte utilisateur via son email.
    ---
    tags:
      - Utilisateurs
    parameters:
      - name: id
        in: path
        type: string
        required: true
        description: id de l'utilisateur à activer
    responses:
      200:
        description: Compte activé avec succès
      404:
        description: Utilisateur introuvable
    """
    if str(id) == str(get_jwt_identity()):
        return jsonify({"message": "Vous ne pouvez pas désactiver votre propre compte."}), 400
    erreur = _verifier_acces_utilisateur(id, ecriture=True)
    if erreur:
        return erreur
    from services.user_service import set_user_activation
    result = set_user_activation(id, activate=False) 
       
    return jsonify(result), result.get("status", 200)


# Création d'un gérant. L'ancienne URL reste disponible pendant la migration.
@user_bp.route('/create_gerant', methods=['POST'])
@user_bp.route('/create_livreur', methods=['POST'])
@jwt_required()
@role_required("admin","manager")
@permission_requise("equipe.gerer")
def create_livreur_route():
    """
    Créer un nouveau gérant pour un magasin.
    ---
    tags:
      - Utilisateurs
    parameters:
      - in: body
        name: body
        required: true
        description: Informations du livreur
        schema:
          type: object
          required:
            - name
            - first_name
            - email
            - magasin_id
          properties:
            name:
              type: string
              example: "Doe"
            first_name:
              type: string
              example: "John"
            email:
              type: string
              example: "john.doe@email.com"
            magasin_id:
              type: string
              example: "66325fd5379a7338c9cd51a1"
            nom_boutique:
              type: string
              example: "Ma Boutique"
    responses:
      200:
        description: Livreur créé avec succès
      400:
        description: Tous les champs sont requis
    """
    data = request.get_json()
    data = inject_magasin_id(data)
    name = data.get('name')
    first_name = data.get('first_name')
    email = data.get('email')
    magasin_id = data.get('magasin_id')
    nom_boutique = data.get('nom_boutique')
    
    if isinstance(data.get("boutique"), dict) and not nom_boutique:
        nom_boutique = (data["boutique"].get("nom") or "").strip()
    if not name or not first_name or not email or not magasin_id:
        return jsonify({"error": "Tous les champs sont requis"}), 400
    profil, erreur = _profil_demande(data, "gerant")
    if erreur:
        return erreur
    from services.user_service import create_livreur
    result = create_livreur(name, first_name, email, magasin_id, nom_boutique)
    if isinstance(result, tuple):
          data, status = result
    else:
        data, status = result, result.get("status", 200) if isinstance(result, dict) else 200
    _enregistrer_profil(data, status, profil)
    return jsonify(data), status

# Créer un caissier
@user_bp.route('/create_caissier', methods=['POST'])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("equipe.gerer")
def create_caissier_route():
    """
    Créer un caissier pour le magasin de l'utilisateur connecté.
    ---
    tags:
      - Utilisateurs
    description: >
      Le caissier vend au comptoir depuis le stock du magasin et ne voit que ses
      propres ventes. Il reçoit un lien d'invitation par email pour choisir son mot
      de passe. Le nombre de caissiers est limité par la licence du magasin.
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [name, first_name, email]
          properties:
            name: {type: string, example: "Ngono"}
            first_name: {type: string, example: "Alice"}
            email: {type: string, example: "alice@magasin.cm"}
            telephone: {type: string, example: "+237690000000"}
    responses:
      201:
        description: Caissier créé, invitation envoyée
      400:
        description: Champ manquant, email déjà utilisé ou limite de licence atteinte
    """
    data = inject_magasin_id(request.get_json(silent=True) or {})
    name = (data.get('name') or '').strip()
    first_name = (data.get('first_name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    if not name or not first_name or not email:
        return jsonify({"error": "Le nom, le prénom et l'email sont requis."}), 400
    profil, erreur = _profil_demande(data, "caissier")
    if erreur:
        return erreur
    from services.user_service import create_caissier
    result, status = create_caissier(name, first_name, email, data['magasin_id'], profil.get("telephone"))
    _enregistrer_profil(result, status, profil)
    return jsonify(result), status


# Créer un manager
@user_bp.route('/create_manager', methods=['POST'])
@jwt_required()
@role_required("admin","manager")
@permission_requise("equipe.gerer")
def create_manager_route():
    """
    Créer un nouveau manager pour un magasin.
    ---
    tags:
      - Utilisateurs
    parameters:
      - in: body
        name: body
        required: true
        description: Informations du manager
        schema:
          type: object
          required:
            - name
            - first_name
            - email
            - magasin_id
          properties:
            name:
              type: string
              example: "Smith"
            first_name:
              type: string
              example: "Anna"
            email:
              type: string
              example: "anna.smith@email.com"
            magasin_id:
              type: string
              example: "66325fd5379a7338c9cd51a2"
    responses:
      200:
        description: Manager créé avec succès
      400:
        description: Tous les champs sont requis
    """
    data = request.get_json()
    data = inject_magasin_id(data)
    name = data.get('name')
    first_name = data.get('first_name')
    email = data.get('email')
    magasin_id = data.get('magasin_id')
    if not name or not first_name or not email or not magasin_id:
        return jsonify({"error": "Tous les champs sont requis"}), 400
    profil, erreur = _profil_demande(data, "manager")
    if erreur:
        return erreur

    from services.user_service import create_manager
    result = create_manager(name, first_name, email, magasin_id)
    if isinstance(result, tuple):
          data, status = result
    else:
        data, status = result, result.get("status", 201) if isinstance(result, dict) else 201
    _enregistrer_profil(data, status, profil)
    return jsonify(data), status

# Route pour obtenir les informations sur un utilisateur
@user_bp.route('/me', methods=['GET'])
@jwt_required()
def get_current_user():
    """
    Obtenir les informations de l'utilisateur connecté.
    ---
    tags:
      - Utilisateurs
    security:
      - bearerAuth: []
    responses:
      200:
        description: Informations de l'utilisateur récupérées avec succès
        schema:
          type: object
          properties:
            _id:
              type: string
              example: "661f518c1f48b44f4df8a394"
            name:
              type: string
              example: "Doe"
            first_name:
              type: string
              example: "John"
            email:
              type: string
              example: "john.doe@email.com"
            role:
              type: string
              example: "admin"
            avatar:
              type: string
              example: "/uploads/avatars/661f518c1f48b44f4df8a394_avatar.png"
      401:
        description: Token JWT manquant ou invalide
      404:
        description: Utilisateur non trouvé
    """
    user_id = get_jwt_identity()
    user = UserModel.get_user_by_id(user_id)

    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404

    user['_id'] = str(user['_id'])
    return jsonify(user), 200


# Droits du compte connecté (pour adapter les écrans à ce que l'admin a choisi).
@user_bp.route('/me/permissions', methods=['GET'])
@jwt_required()
def get_my_permissions():
    from services.permissions_service import droits_de, utilisateur_courant
    user = utilisateur_courant()
    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    return jsonify({"role": user.get("role"), "permissions": droits_de(user)}), 200


# Double authentification d'un membre qui a perdu son téléphone et ses codes.
@user_bp.route('/<string:user_id>/deux-facteurs', methods=['DELETE'])
@jwt_required()
@role_required("admin", "staff", "manager")
@permission_requise("equipe.gerer")
def reinitialiser_deux_facteurs(user_id):
    erreur = _verifier_acces_utilisateur(user_id, ecriture=True)
    if erreur:
        return erreur
    from services import deux_facteurs_service
    deux_facteurs_service.desactiver(user_id)
    return jsonify({"message": "Double authentification désactivée pour ce compte. "
                               "La personne pourra la réactiver depuis ses paramètres."}), 200


UPLOAD_FOLDER = 'uploads/avatars'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Créer le dossier s'il n'existe pas
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Route permettant d'uploader un avatar
@user_bp.route('/upload_avatar', methods=['POST'])
@jwt_required()
def upload_avatar():
    """
    Upload d’un avatar pour l'utilisateur connecté.
    ---
    tags:
      - Utilisateurs
    consumes:
      - multipart/form-data
    security:
      - bearerAuth: []
    parameters:
      - name: avatar
        in: formData
        type: file
        required: true
        description: Image de l'avatar (jpg, png, gif, jpeg)
    responses:
      200:
        description: Avatar mis à jour avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: Avatar mis à jour
            avatar_url:
              type: string
              example: /uploads/avatars/661f518c1f48b44f4df8a394_avatar.png
      400:
        description: Requête invalide ou fichier non autorisé
      401:
        description: Token JWT manquant ou invalide
    """
    current_user_id = get_jwt_identity()
    if 'avatar' not in request.files:
        return jsonify({"error": "Aucun fichier envoyé"}), 400
    
    file = request.files['avatar']
    if file.filename == '':
        return jsonify({"error": "Nom de fichier vide"}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        path = os.path.join(UPLOAD_FOLDER, f"{current_user_id}_{filename}")
        file.save(path)

        # Mise à jour du user en base
        avatar_url = f"/{path}"  # Si serveur local. Pour un CDN, stocker l'URL
        UserModel.update_avatar(user_id=current_user_id, avatar_url=avatar_url)

        return jsonify({"message": "Avatar mis à jour", "avatar_url": avatar_url}), 200
    
    return jsonify({"error": "Format de fichier non autorisé"}), 400


# Récupérer tous les gérants du magasin. L'URL historique est conservée.
@user_bp.route('/gerants', methods=['GET'])
@user_bp.route('/livreurs', methods=['GET'])
@role_required("admin","manager")
def get_all_livreurs():
    from services.user_service import get_livreur_service
    return get_livreur_service()
    
    
# Récupérer un utilisateur par son ID
@user_bp.route('/<string:user_id>', methods=['GET'])
@jwt_required()
@role_required("admin", "staff", "manager", "livreur")
def get_user_by_id(user_id):
    """
    Récupérer les informations d'un utilisateur par son ID.
    ---
    tags:
      - Utilisateurs
    parameters:
      - name: user_id
        in: path
        type: string
        required: true
        description: ID de l'utilisateur à récupérer
    responses:
      200:
        description: Informations de l'utilisateur récupérées avec succès
      404:
        description: Utilisateur non trouvé
    """
    erreur = _verifier_acces_utilisateur(user_id)
    if erreur:
        return erreur
    from services.user_service import get_user_by_id_service
    return _service_response(get_user_by_id_service(user_id))
  
# Supprimer un utilisateur par son ID
@user_bp.route('/<string:user_id>', methods=['DELETE'])
@jwt_required()
@role_required("admin", "staff", "manager")
@permission_requise("equipe.gerer")
def delete_user(user_id):
    """
    Supprimer un utilisateur par son ID.
    ---
    tags:
      - Utilisateurs
    parameters:
      - name: user_id
        in: path
        type: string
        required: true
        description: ID de l'utilisateur à supprimer
    responses:
      200:
        description: Utilisateur supprimé avec succès
      404:
        description: Utilisateur non trouvé
    """
    erreur = _verifier_acces_utilisateur(user_id, ecriture=True)
    if erreur:
        return erreur
    from services.user_service import delete_user_service
    return _service_response(delete_user_service(user_id))
  
  
# Mettre à jour un utilisateur
@user_bp.route('/<string:user_id>', methods=['PUT'])
@jwt_required()
@role_required("admin", "staff", "manager")
def update_user(user_id):
  from services.permissions_service import droit_courant
  if str(user_id) != str(get_jwt_identity()) and not droit_courant("equipe.gerer"):
      return jsonify({"message": "Vous n'avez pas le droit « Gérer l'équipe ». Demandez-le à l'administrateur du magasin."}), 403
  erreur = _verifier_acces_utilisateur(user_id, ecriture=True)
  if erreur:
      return erreur
  from services.user_service import update_user_service
  return _service_response(update_user_service(user_id, data=request.get_json(silent=True) or {}))


# Récupérer tous les utilisateurs
@user_bp.route('/', methods=['GET'])
@jwt_required()
@role_required("admin", "staff", "manager")
def get_all_users():
    """
    Récupérer tous les utilisateurs.
    ---
    tags:
      - Utilisateurs
    responses:
      200:
        description: Liste de tous les utilisateurs récupérée avec succès
      500:
        description: Erreur lors de la récupération des utilisateurs
    """
    from services.user_service import get_all_users_service
    return get_all_users_service()
  

# Réinitialiser le mot de passe d'un utilisateur
@user_bp.route('/reset-password/<user_id>', methods=['POST'])
@jwt_required()
@role_required("admin", "staff", "manager")
@permission_requise("equipe.gerer")
def reset_password(user_id):
    """
    Réinitialiser le mot de passe d'un utilisateur.
    ---
    tags:
      - Utilisateurs
    description: >
      L'ancien mot de passe est désactivé, les sessions du compte sont
      fermées et l'utilisateur reçoit un lien (30 min) pour en choisir un
      nouveau. Aucun mot de passe n'est envoyé par email.
    parameters:
      - in: path
        name: user_id
        required: true
        type: string
    responses:
      200:
        description: Lien de réinitialisation envoyé
      400:
        description: Compte sans email ou désactivé
      502:
        description: Email non envoyé (rien n'est modifié)
      404:
        description: Utilisateur non trouvé
    """
    erreur = _verifier_acces_utilisateur(user_id, ecriture=True)
    if erreur:
        return erreur
    data = request.get_json(silent=True) or {}
    from services.user_service import reset_password_service
    return reset_password_service(user_id, data)


@user_bp.route('/<user_id>/invitation', methods=['POST'])
@jwt_required()
@role_required("admin", "staff", "manager")
@permission_requise("equipe.gerer")
def renvoyer_invitation(user_id):
    """
    Renvoyer le lien d'invitation d'un compte
    ---
    tags:
      - Utilisateurs
    description: >
      Pour un compte qui n'a pas encore choisi son mot de passe (email
      d'invitation perdu ou expiré). Aucun mot de passe n'est envoyé.
    parameters:
      - in: path
        name: user_id
        required: true
        type: string
    responses:
      200:
        description: Invitation renvoyée
      400:
        description: Compte désactivé ou mot de passe déjà choisi
      502:
        description: Email non envoyé
    """
    erreur = _verifier_acces_utilisateur(user_id, ecriture=True)
    if erreur:
        return erreur
    from services.user_service import renvoyer_invitation_service
    return renvoyer_invitation_service(user_id)


@user_bp.route('/settings/barcode', methods=['PATCH'])
@jwt_required()
def toggle_barcode_mode():
    """
    Basculer le mode code-barres pour l'utilisateur connecté
    ---
    tags:
      - Utilisateurs
    summary: Activer/Désactiver le mode code-barres
    description: Permet à l'utilisateur de basculer entre le mode manuel et le mode lecteur de code-barres.
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            barcode_mode:
              type: boolean
              example: true
    responses:
      200:
        description: Paramètre mis à jour
    """
    user_id = get_jwt_identity()
    data = request.get_json() or {}
    mode = data.get('barcode_mode')
    
    from services.user_service import toggle_barcode_mode_service
    result, status_code = toggle_barcode_mode_service(user_id, mode)
    return jsonify(result), status_code

