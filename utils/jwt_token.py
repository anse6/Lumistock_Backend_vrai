# utils/jwt_token.py
"""Liens envoyés par email : invitation et réinitialisation du mot de passe.

Chaque lien est un JWT signé avec SECRET_KEY et une audience propre aux liens
de compte : un token de connexion (signé avec JWT_SECRET_KEY, sans cette
audience) n'est jamais accepté ici, et inversement.

Usage unique : le token porte la « version de sécurité » du compte
(``token_version``). Elle est incrémentée dès que le mot de passe change ou que
le compte est désactivé : le lien utilisé, et tous les autres liens et
sessions ouverts, deviennent alors inutilisables. Émettre un lien ne
l'incrémente pas : sinon n'importe qui pourrait déconnecter un utilisateur en
demandant des réinitialisations à sa place.
"""

from datetime import datetime, timedelta, timezone

import jwt
from flask import current_app

AUDIENCE = "lumistock:compte"
DUREES = {
    "invitation": timedelta(hours=72),
    "reset": timedelta(minutes=30),
}
DUREES_AFFICHEES = {"invitation": "72 heures", "reset": "30 minutes"}


class LienInvalide(Exception):
    """Lien expiré, déjà utilisé, falsifié ou visant un compte inactif."""


def generate_account_token(user, purpose):
    """Crée un lien signé pour ``user`` (document utilisateur)."""
    if purpose not in DUREES:
        raise ValueError(f"Type de lien inconnu : {purpose}")
    maintenant = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["_id"]),
        "purpose": purpose,
        "tv": int(user.get("token_version", 0) or 0),
        "aud": AUDIENCE,
        "iat": maintenant,
        "exp": maintenant + DUREES[purpose],
    }
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm="HS256")


def verify_account_token(token, purposes):
    """Vérifie un lien et retourne le compte visé.

    :param purposes: types de lien acceptés, ex. ("reset", "invitation")
    :raises LienInvalide: si le lien ne peut pas être utilisé
    """
    from models.user_model import UserModel

    try:
        payload = jwt.decode(
            token, current_app.config["SECRET_KEY"], algorithms=["HS256"], audience=AUDIENCE,
            options={"require": ["sub", "exp", "aud", "purpose", "tv"]},
        )
    except jwt.ExpiredSignatureError:
        raise LienInvalide("Ce lien a expiré. Demandez-en un nouveau.")
    except jwt.InvalidTokenError:
        raise LienInvalide("Lien invalide.")

    if payload.get("purpose") not in purposes:
        raise LienInvalide("Lien invalide.")
    user = UserModel.find_by_id(payload["sub"]) if UserModel.id_valide(payload["sub"]) else None
    if not user or not user.get("is_active", False):
        raise LienInvalide("Lien invalide.")
    if int(user.get("token_version", 0) or 0) != payload["tv"]:
        raise LienInvalide("Ce lien a déjà été utilisé. Demandez-en un nouveau si nécessaire.")
    return user, payload["purpose"]


def generate_token(user_id, role, refresh=False):
    """Token de connexion pour un compte (scripts et tests)."""
    from flask_jwt_extended import create_access_token, create_refresh_token
    from models.user_model import UserModel

    user = UserModel.find_by_id(user_id) if UserModel.id_valide(user_id) else None
    claims = {"role": role, "tv": int((user or {}).get("token_version", 0) or 0)}
    if refresh:
        return create_refresh_token(identity=str(user_id), additional_claims=claims)
    return create_access_token(identity=str(user_id), additional_claims=claims)
