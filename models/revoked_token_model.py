# models/revoked_token_model.py
"""Tokens de connexion révoqués à la déconnexion.

La liste est en base (et non plus en mémoire) : elle survit aux redémarrages
et est partagée par tous les workers. L'identifiant d'une ligne est dérivé du
``jti`` du token, ce qui rend la vérification à chaque requête directe.
"""
import hashlib
from datetime import datetime, timezone

from extensions import db


class RevokedTokenModel:

    @staticmethod
    def get_collection():
        return db.tables.revoked_tokens

    @staticmethod
    def _id(jti):
        # 24 caractères hexadécimaux : le format d'identifiant des tables.
        return hashlib.sha256(str(jti).encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def revoquer(payload):
        """Révoque le token décrit par ``payload`` (claims JWT décodés)."""
        jti = payload.get("jti")
        if not jti:
            return
        expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc).replace(tzinfo=None) \
            if payload.get("exp") else None
        RevokedTokenModel.get_collection().update_one(
            {"_id": RevokedTokenModel._id(jti)},
            {"$setOnInsert": {
                "jti": jti,
                "user_id": str(payload.get("sub") or ""),
                "type": payload.get("type"),
                "expires_at": expires_at,
                "created_at": datetime.utcnow(),
            }},
            upsert=True,
        )

    @staticmethod
    def est_revoque(jti):
        return bool(jti) and RevokedTokenModel.get_collection().find_one(
            {"_id": RevokedTokenModel._id(jti)}) is not None

    @staticmethod
    def purger_expires():
        """Supprime les révocations de tokens déjà expirés (devenues inutiles)."""
        return RevokedTokenModel.get_collection().delete_many(
            {"expires_at": {"$lt": datetime.utcnow()}}).deleted_count
