from flask import Blueprint, request, jsonify
from services.auth_services import (
    login_user,
    login_deux_facteurs,
    refresh_user_token,
    logout_service,
    logout_all_service,
    send_reset_email_service,
    verifier_lien_service,
    reset_password_service,
    change_user_password_service,
)
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from schemas.auth_schema import LoginSchema, ResetPasswordSchema, ChangePasswordSchema
from marshmallow import ValidationError
from database import sans_transaction

auth_bp = Blueprint('auth_bp', __name__)


@auth_bp.route('/activate_account/<token>', methods=['POST'])
def activate_user_account(token):
    """
    Ancien lien d'activation (désactivé)
    ---
    tags:
      - Authentification
    description: >
      Les comptes ne s'activent plus par ce lien : il permettait à un compte
      désactivé de se réactiver lui-même. Un nouveau compte reçoit un lien
      d'invitation (/definir-mot-de-passe/<token>) pour choisir son mot de passe.
    responses:
      410:
        description: Lien obsolète
    """
    return jsonify({"error": "Ce lien n'est plus valide. Utilisez le lien d'invitation reçu par email "
                             "ou « Mot de passe oublié »."}), 410


# Route permettant l'authentification d'un utilisateur

@auth_bp.route('/login', methods=['POST'])
@sans_transaction  # le compteur d'échecs doit être enregistré malgré la réponse 401
def login():
    """
    Connexion d’un utilisateur
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
            - email
            - password
          properties:
            email:
              type: string
              example: "user@example.com"
            password:
              type: string
              example: "password123"
    responses:
      200:
        description: Connexion réussie
      400:
        description: Email ou mot de passe manquant
    """
    data = request.get_json(silent=True) or {}
    if not data.get('email') or not data.get('password'):
        return jsonify({"error": "Email et mot de passe sont requis."}), 400

    if isinstance(data.get('email'), str):
        data['email'] = data['email'].strip().lower()
    try:
        data = LoginSchema().load(data)
    except ValidationError:
        # Même réponse qu'un mauvais mot de passe : rien n'est révélé.
        return jsonify({"error": "Email ou mot de passe incorrect."}), 401

    return login_user(data["email"], data["password"])


@auth_bp.route('/login/2fa', methods=['POST'])
@sans_transaction  # le compteur d'essais doit être enregistré malgré la réponse 401
def login_2fa():
    """
    Seconde étape de la connexion (double authentification)
    ---
    tags:
      - Authentification
    description: >
      Body : {"jeton_2fa": "<reçu de /auth/login>", "code": "123456"}. Le code
      vient de l'application d'authentification, ou c'est un code de secours.
    responses:
      200:
        description: Connexion réussie (mêmes données que /auth/login)
      401:
        description: Code incorrect, déjà utilisé, ou délai dépassé
    """
    data = request.get_json(silent=True) or {}
    if not data.get("jeton_2fa") or not data.get("code"):
        return jsonify({"error": "Saisissez le code de votre application d'authentification."}), 400
    return login_deux_facteurs(data["jeton_2fa"], data["code"])


# ---------------------------------------------------------------------------
# Double authentification : réglages du compte connecté (facultative)
# ---------------------------------------------------------------------------
def _moi_2fa():
    from models.user_model import UserModel
    return UserModel.find_by_id(get_jwt_identity())


def _erreur_2fa(e):
    return jsonify({"error": str(e)}), getattr(e, "status", 400)


@auth_bp.route('/2fa', methods=['GET'])
@jwt_required()
def etat_2fa():
    """État de la double authentification du compte connecté."""
    from services import deux_facteurs_service
    return jsonify(deux_facteurs_service.etat(get_jwt_identity())), 200


@auth_bp.route('/2fa/initialiser', methods=['POST'])
@jwt_required()
def initialiser_2fa():
    """Nouveau secret à scanner (QR code) ; rien n'est actif avant /2fa/activer."""
    from services import deux_facteurs_service
    try:
        return jsonify(deux_facteurs_service.initialiser(_moi_2fa())), 200
    except deux_facteurs_service.ErreurDeuxFacteurs as e:
        return _erreur_2fa(e)


@auth_bp.route('/2fa/activer', methods=['POST'])
@jwt_required()
def activer_2fa():
    """Body : {"code": "123456"}. Renvoie les codes de secours, une seule fois."""
    from services import deux_facteurs_service
    data = request.get_json(silent=True) or {}
    try:
        codes = deux_facteurs_service.activer(_moi_2fa(), data.get("code"))
    except deux_facteurs_service.ErreurDeuxFacteurs as e:
        return _erreur_2fa(e)
    return jsonify({"message": "Double authentification activée.", "codes_secours": codes,
                    **deux_facteurs_service.etat(get_jwt_identity())}), 200


def _confirmer_identite(data, avec_code):
    """Mot de passe (et code si demandé) : None si tout est bon, sinon la réponse d'erreur."""
    from models.user_model import UserModel
    from services import deux_facteurs_service
    from utils.rate_limit import limite_atteinte

    user = _moi_2fa()
    if limite_atteinte(f"2fa-reglages:{get_jwt_identity()}", 10, 15 * 60):
        return jsonify({"error": "Trop de tentatives. Réessayez plus tard."}), 429
    if not UserModel.check_password(user, data.get("password")):
        return jsonify({"error": "Mot de passe incorrect."}), 401
    if avec_code and not deux_facteurs_service.verifier(user["_id"], data.get("code")):
        return jsonify({"error": "Code incorrect ou déjà utilisé."}), 401
    return None


@auth_bp.route('/2fa/desactiver', methods=['POST'])
@jwt_required()
def desactiver_2fa():
    """Body : {"password": "...", "code": "123456 ou code de secours"}."""
    from services import deux_facteurs_service
    data = request.get_json(silent=True) or {}
    if not deux_facteurs_service.est_active(get_jwt_identity()):
        return jsonify({"error": "La double authentification n'est pas activée."}), 409
    erreur = _confirmer_identite(data, avec_code=True)
    if erreur:
        return erreur
    deux_facteurs_service.desactiver(get_jwt_identity())
    return jsonify({"message": "Double authentification désactivée.",
                    **deux_facteurs_service.etat(get_jwt_identity())}), 200


@auth_bp.route('/2fa/codes-secours', methods=['POST'])
@jwt_required()
def regenerer_codes_2fa():
    """Body : {"password": "..."}. Nouveaux codes de secours (les anciens ne marchent plus)."""
    from services import deux_facteurs_service
    data = request.get_json(silent=True) or {}
    erreur = _confirmer_identite(data, avec_code=False)
    if erreur:
        return erreur
    try:
        codes = deux_facteurs_service.regenerer_codes(get_jwt_identity())
    except deux_facteurs_service.ErreurDeuxFacteurs as e:
        return _erreur_2fa(e)
    return jsonify({"codes_secours": codes, **deux_facteurs_service.etat(get_jwt_identity())}), 200


@auth_bp.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    """
    Rafraîchir le token d'accès
    ---
    tags:
      - Authentification
    summary: Rafraîchissement du token JWT
    description: Génère un nouveau token d'accès à partir d'un token de rafraîchissement valide.
    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: Token de rafraîchissement JWT au format **Bearer &lt;token&gt;**
        default: Bearer 
    responses:
      200:
        description: Nouveau token d'accès généré
        schema:
          type: object
          properties:
            access_token:
              type: string
              example: eyJ0eXAiOiJKV1QiLCJhbGciOi...
      401:
        description: Token invalide ou expiré
    """
    identity = get_jwt_identity()
    user_id = identity.get("user_id") if isinstance(identity, dict) else identity
    return refresh_user_token(user_id)






@auth_bp.route('/logout', methods=['POST'])
@jwt_required(verify_type=False)
def logout():
    """
    Déconnexion : révoque le token de la requête et le refresh token fourni.
    ---
    tags:
      - Authentification
    parameters:
      - in: body
        name: body
        required: false
        schema:
          type: object
          properties:
            refresh_token:
              type: string
    responses:
      200:
        description: Déconnexion réussie
    """
    data = request.get_json(silent=True) or {}
    return logout_service(get_jwt(), data.get("refresh_token"))


@auth_bp.route('/logout_all', methods=['POST'])
@jwt_required()
def logout_all():
    """
    Ferme toutes les sessions du compte, sur tous les appareils.
    ---
    tags:
      - Authentification
    responses:
      200:
        description: Sessions fermées
    """
    return logout_all_service(get_jwt_identity())


@auth_bp.route('/verify_link/<token>', methods=['GET'])
def verify_link(token):
    """
    Vérifie qu'un lien d'invitation ou de réinitialisation est encore utilisable.
    ---
    tags:
      - Authentification
    parameters:
      - in: path
        name: token
        required: true
        type: string
    responses:
      200:
        description: Lien valide (type invitation ou reset)
      400:
        description: Lien expiré, déjà utilisé ou invalide
    """
    return verifier_lien_service(token)


@auth_bp.route('/forgot_password', methods=['POST'])
def forgot_password():
    """
    Envoyer un email de réinitialisation de mot de passe.
    ---
    tags:
      - Authentification
    parameters:
      - in: body
        name: body
        required: true
        description: Email de l'utilisateur
        schema:
          type: object
          required:
            - email
          properties:
            email:
              type: string
              example: "utilisateur@example.com"
    responses:
      200:
        description: Email envoyé si l'utilisateur existe
    """
    data = request.get_json(silent=True) or {}
    email = data.get('email')
    return send_reset_email_service(email)


@auth_bp.route('/reset_password/<token>', methods=['POST'])
def reset_password(token):
    """
    Réinitialiser le mot de passe à l'aide d'un token.
    ---
    tags:
      - Authentification
    parameters:
      - in: path
        name: token
        required: true
        type: string
        description: Token de réinitialisation
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - new_password
            - confirm_password
          properties:
            new_password:
              type: string
              example: "nouveaumotdepasse123"
            confirm_password:
              type: string
              example: "nouveaumotdepasse123"
    responses:
      200:
        description: Mot de passe réinitialisé avec succès
      400:
        description: Token invalide ou mot de passe incorrect
    """
    data = request.get_json(silent=True) or {}
    # validation avec le schema marshmallow
    try:
        data = ResetPasswordSchema().load(data)
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400

    return reset_password_service(token, data["new_password"], data["confirm_password"])


# modifier son mot de passe
@auth_bp.route('/change_password', methods=['POST'])
@jwt_required()
def change_password():
    """
    Modifier le mot de passe de l'utilisateur connecté.
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
            - old_password
            - new_password
            - confirm_password
          properties:
            old_password:
              type: string
              example: "ancienmotdepasse123"
            new_password:
              type: string
              example: "nouveaumotdepasse123"
            confirm_password:
              type: string
              example: "nouveaumotdepasse123"
    responses:
      200:
        description: Mot de passe modifié avec succès
      400:
        description: Erreur dans les données envoyées ou mot de passe incorrect
    """
    data = request.get_json(silent=True) or {}
    identity = get_jwt_identity()
    user_id = identity.get("user_id") if isinstance(identity, dict) else identity
    
    old_password = data.get('old_password')
    new_password = data.get('new_password')
    confirm_password = data.get('confirm_password')
    
    if not old_password or not new_password or not confirm_password:
        return jsonify({"error": "Tous les champs sont requis."}), 400
    
    if new_password != confirm_password:
        return jsonify({"error": "Les nouveaux mots de passe ne correspondent pas."}), 400
    
    try:
        data = ChangePasswordSchema().load(data)
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400

    
    # Appel du service pour changer le mot de passe
    return change_user_password_service(user_id, data["old_password"], data["new_password"], data["confirm_password"])


