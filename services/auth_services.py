# services/auth_services.py
"""Connexion, sessions et mots de passe.

Principes de sécurité :
- la connexion répond toujours « Email ou mot de passe incorrect » (on ne
  révèle pas si un compte existe) et limite les tentatives : 5 mots de passe
  erronés verrouillent le compte 15 minutes, et une même IP est freinée ;
- aucun mot de passe n'est envoyé par email : les comptes reçoivent un lien
  d'invitation, les oublis un lien de réinitialisation, tous à usage unique ;
- changer de mot de passe ferme toutes les sessions ouvertes du compte ;
- la déconnexion révoque réellement les tokens (liste en base).
"""
from datetime import datetime

from bson import ObjectId
from flask import current_app, jsonify, request
from flask_jwt_extended import create_access_token, create_refresh_token, decode_token

from models.connexion_model import ConnexionModel
from models.magasin_model import MagasinModel
from models.revoked_token_model import RevokedTokenModel
from models.user_model import UserModel
from utils import email as email_utils
from utils.error_handler import handle_error
from utils.jwt_token import (
    DUREES_AFFICHEES, LienInvalide, generate_account_token, verify_account_token,
)
from utils.passwords import erreur_robustesse
from utils.rate_limit import limite_atteinte, reinitialiser

ROLES_MAGASIN = {"admin", "manager", "livreur", "gerant", "caissier"}
MESSAGE_IDENTIFIANTS = "Email ou mot de passe incorrect."

# Limites par adresse IP (le verrouillage par compte est en base).
LOGIN_IP_MAX, LOGIN_IP_FENETRE = 50, 15 * 60
FORGOT_MAX, FORGOT_FENETRE = 5, 15 * 60

# Hash d'un mot de passe quelconque : quand l'email est inconnu, on effectue
# quand même une vérification bcrypt pour que le temps de réponse ne trahisse
# pas l'existence du compte.
_HASH_FACTICE = None


def _hash_factice():
    global _HASH_FACTICE
    if _HASH_FACTICE is None:
        _HASH_FACTICE = UserModel.mot_de_passe_inutilisable()
    return _HASH_FACTICE


def _trop_de_tentatives(attente):
    minutes = max(1, (attente + 59) // 60)
    return jsonify({
        "error": f"Trop de tentatives. Réessayez dans {minutes} minute{'s' if minutes > 1 else ''}."
    }), 429


def _claims(user):
    return {"role": user["role"], "tv": int(user.get("token_version", 0) or 0)}


def creer_tokens(user):
    """Access + refresh token d'un compte (durées : configuration JWT_*)."""
    identity = str(user["_id"])
    return (
        create_access_token(identity=identity, additional_claims=_claims(user)),
        create_refresh_token(identity=identity, additional_claims=_claims(user)),
    )


# Fonction de connexion de l'utilisateur
def login_user(email, password):
    email = (email or "").strip().lower()
    ip = request.remote_addr or "inconnue"

    attente = limite_atteinte(f"login-ip:{ip}", LOGIN_IP_MAX, LOGIN_IP_FENETRE)
    if attente:
        return _trop_de_tentatives(attente)

    user = UserModel.find_by_email(email) if email else None
    if not user:
        UserModel.check_password({"password": _hash_factice()}, password)
        return handle_error(MESSAGE_IDENTIFIANTS, 401)

    if UserModel.est_verrouille(user):
        attente = int((user["locked_until"] - datetime.utcnow()).total_seconds())
        return _trop_de_tentatives(attente)

    if not UserModel.check_password(user, password):
        UserModel.enregistrer_echec_connexion(user)
        return handle_error(MESSAGE_IDENTIFIANTS, 401)
    UserModel.reinitialiser_echecs_connexion(user)

    erreur, magasin = _controle_compte(user)
    if erreur:
        return erreur

    # Double authentification activée : le mot de passe ne suffit pas, le
    # serveur demande le code de l'application (étape /auth/login/2fa).
    from services import deux_facteurs_service
    if deux_facteurs_service.est_active(user["_id"]):
        return jsonify({
            "deux_facteurs_requis": True,
            "jeton_2fa": _serialiseur_2fa().dumps({"u": str(user["_id"]), "tv": int(user.get("token_version", 0) or 0)}),
            "expire_dans": DUREE_JETON_2FA,
        }), 200

    return _ouvrir_session(user, magasin, ip)


def _controle_compte(user):
    """(réponse d'erreur, None) ou (None, magasin) : compte et magasin actifs.

    L'état du compte et du magasin n'est révélé qu'au titulaire du bon mot de passe."""
    if not user.get("is_active", False):
        return handle_error("Compte inactif. Veuillez contacter votre administrateur.", 403), None
    magasin = None
    if user["role"] in ROLES_MAGASIN:
        magasin = MagasinModel.get_magasin_by_id(user.get("magasin_id"))
        if not magasin:
            return handle_error("Magasin introuvable pour cet utilisateur.", 403), None
        if not magasin.get("is_active", False):
            return handle_error("Magasin inactif. Veuillez contacter votre administrateur ou l'équipe de la plateforme", 403), None
    return None, magasin


def _ouvrir_session(user, magasin, ip):
    access_token, refresh_token = creer_tokens(user)

    ConnexionModel.create_connexion({
        "user_id": user["_id"],
        "email": user["email"],
        "role": user["role"],
        "magasin_id": user.get("magasin_id"),
        "timestamp": datetime.utcnow(),
        "ip_address": ip,
    })

    data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "role": user["role"],
        "email": user["email"],
        "id": str(user["_id"]),
    }
    if magasin:
        data.update({
            "denomination_magasin": magasin["denomination"],
            "magasin_id": str(magasin["_id"]),
            "logo_magasin": f"{request.host_url}uploads/images/{magasin['logo']}" if magasin.get("logo") else None,
        })
    return jsonify(data), 200


# ---------------------------------------------------------------------------
# Double authentification : seconde étape de la connexion
# ---------------------------------------------------------------------------
DUREE_JETON_2FA = 5 * 60
CODE_2FA_MAX, CODE_2FA_FENETRE = 5, 5 * 60


def _serialiseur_2fa():
    # Jeton signé, distinct d'un JWT : il ne donne accès à aucune route.
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"] or current_app.config["JWT_SECRET_KEY"],
                                  salt="lumistock-connexion-2fa")


def login_deux_facteurs(jeton, code):
    from itsdangerous import BadSignature, SignatureExpired
    from services import deux_facteurs_service

    ip = request.remote_addr or "inconnue"
    try:
        contenu = _serialiseur_2fa().loads(str(jeton or ""), max_age=DUREE_JETON_2FA)
    except SignatureExpired:
        return handle_error("Le délai est dépassé : reconnectez-vous avec votre mot de passe.", 401)
    except BadSignature:
        return handle_error("Session de connexion invalide : reconnectez-vous.", 401)

    user = UserModel.find_by_id(contenu.get("u")) if UserModel.id_valide(contenu.get("u")) else None
    if not user or int(user.get("token_version", 0) or 0) != int(contenu.get("tv", -1)):
        return handle_error("Session de connexion invalide : reconnectez-vous.", 401)
    if UserModel.est_verrouille(user):
        attente = int((user["locked_until"] - datetime.utcnow()).total_seconds())
        return _trop_de_tentatives(attente)
    attente = limite_atteinte(f"login-2fa:{user['_id']}", CODE_2FA_MAX, CODE_2FA_FENETRE)
    if attente:
        return _trop_de_tentatives(attente)

    erreur, magasin = _controle_compte(user)
    if erreur:
        return erreur
    if not deux_facteurs_service.verifier(user["_id"], code):
        return handle_error("Code incorrect ou déjà utilisé.", 401)
    reinitialiser(f"login-2fa:{user['_id']}")
    return _ouvrir_session(user, magasin, ip)


# Fonction pour rafraîchir le token de l'utilisateur
def refresh_user_token(user_id):
    """Nouvel access token. Le refresh token a déjà été contrôlé (révocation,
    compte actif, version de sécurité) par le chargeur de blocklist."""
    if not user_id or not UserModel.id_valide(user_id):
        return jsonify({"error": "Identité non trouvée"}), 401
    user = UserModel.find_by_id(user_id)
    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 401
    access_token = create_access_token(identity=str(user["_id"]), additional_claims=_claims(user))
    return jsonify({"access_token": access_token}), 200


def logout_service(payload_courant, refresh_token=None):
    """Révoque le token de la requête et, s'il est fourni, le refresh token
    de la même session."""
    RevokedTokenModel.revoquer(payload_courant)
    if refresh_token:
        try:
            payload = decode_token(refresh_token, allow_expired=True)
        except Exception:
            payload = None
        # On ne révoque que les tokens du même compte.
        if payload and str(payload.get("sub")) == str(payload_courant.get("sub")):
            RevokedTokenModel.revoquer(payload)
    return jsonify({"message": "Déconnexion réussie."}), 200


def logout_all_service(user_id):
    """Ferme toutes les sessions du compte, sur tous les appareils."""
    if not UserModel.invalider_sessions(user_id):
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    return jsonify({"message": "Toutes vos sessions ont été fermées."}), 200


# ---------------------------------------------------------------------------
# Liens envoyés par email
# ---------------------------------------------------------------------------
def envoyer_lien_compte(user, type_lien, magasin_nom=None):
    """Envoie un lien d'invitation ou de réinitialisation à ``user``.

    :return: True si l'email est parti.
    """
    token = generate_account_token(user, type_lien)
    if magasin_nom is None and user.get("magasin_id"):
        magasin = MagasinModel.get_magasin_by_id(str(user["magasin_id"]))
        magasin_nom = (magasin or {}).get("denomination", "")
    return email_utils.send_lien_compte_email(
        user["email"], type_lien, token,
        prenom=user.get("first_name") or user.get("name") or "",
        role=user.get("role", ""), magasin=magasin_nom or "",
        duree=DUREES_AFFICHEES[type_lien],
    )


# Service pour reinitialiser le mot de passe
def send_reset_email_service(email):
    """Envoie un lien de réinitialisation. La réponse est identique que le
    compte existe ou non, et les demandes répétées sont freinées."""
    reponse = jsonify({"message": "Si cet email existe, un lien a été envoyé"}), 200
    email = (email or "").strip().lower()
    if not email:
        return reponse
    ip = request.remote_addr or "inconnue"
    if limite_atteinte(f"forgot-ip:{ip}", FORGOT_MAX * 4, FORGOT_FENETRE) \
            or limite_atteinte(f"forgot-email:{email}", FORGOT_MAX, FORGOT_FENETRE):
        return reponse
    user = UserModel.find_by_email(email)
    if user and user.get("is_active", False):
        envoyer_lien_compte(user, "reset")
    return reponse


def verifier_lien_service(token):
    """Indique au frontend si un lien est encore utilisable (sans le consommer)."""
    try:
        user, purpose = verify_account_token(token, ("reset", "invitation"))
    except LienInvalide as e:
        return jsonify({"valid": False, "error": str(e)}), 400
    return jsonify({"valid": True, "type": purpose, "email": user["email"],
                    "first_name": user.get("first_name", "")}), 200


def reset_password_service(token, new_password, confirm_password):
    """Définit le mot de passe via un lien d'invitation ou de réinitialisation.

    Le lien est consommé : la version de sécurité du compte change, ce qui
    invalide ce lien, les autres liens émis et toutes les sessions ouvertes.
    """
    if new_password != confirm_password:
        return jsonify({"error": "Les nouveaux mots de passe ne correspondent pas."}), 400
    try:
        user, _ = verify_account_token(token, ("reset", "invitation"))
    except LienInvalide as e:
        return jsonify({"error": str(e)}), 400
    erreur = erreur_robustesse(new_password, user.get("email"))
    if erreur:
        return jsonify({"error": erreur}), 400
    UserModel.set_password(str(user["_id"]), new_password)
    reinitialiser(f"forgot-email:{user['email']}")
    return jsonify({"message": "Mot de passe enregistré. Vous pouvez maintenant vous connecter."}), 200


# Fonction pour changer le mot de passe de l'utilisateur connecté
def change_user_password_service(user_id, old_password, new_password, confirm_password):
    if new_password != confirm_password:
        return jsonify({"error": "Les nouveaux mots de passe ne correspondent pas."}), 400
    if not UserModel.id_valide(user_id):
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    user = UserModel.find_by_id(user_id)
    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    if not UserModel.check_password(user, old_password):
        return jsonify({"error": "Mot de passe actuel incorrect"}), 401
    if old_password == new_password:
        return jsonify({"error": "Le nouveau mot de passe doit être différent de l'actuel."}), 400
    erreur = erreur_robustesse(new_password, user.get("email"))
    if erreur:
        return jsonify({"error": erreur}), 400
    UserModel.set_password(user_id, new_password)
    # Les autres sessions sont fermées ; la session courante reçoit de nouveaux tokens.
    user = UserModel.find_by_id(user_id)
    access_token, refresh_token = creer_tokens(user)
    return jsonify({
        "message": "Mot de passe modifié avec succès",
        "access_token": access_token,
        "refresh_token": refresh_token,
    }), 200
