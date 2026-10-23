# utils/decorators.py

from functools import wraps
from flask_jwt_extended import verify_jwt_in_request, get_jwt
from flask import jsonify


def _role_is_allowed(user_role, allowed_roles):
    """Accept the logistics-role rename during the `livreur` → `gerant` rollout."""
    if user_role in allowed_roles:
        return True
    return (
        user_role == "gerant" and "livreur" in allowed_roles
    ) or (
        user_role == "livreur" and "gerant" in allowed_roles
    )

def staff_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        claims = get_jwt()
        if claims.get("role") != "staff":
            return jsonify({"msg": "Accès réservé au staff"}), 403
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        claims = get_jwt()
        if claims.get("role") != "admin":
            return jsonify({"msg": "Accès réservé aux admins"}), 403
        return fn(*args, **kwargs)
    return wrapper

def manager_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        claims = get_jwt()
        if claims.get("role") != "manager":
            return jsonify({"msg": "Accès réservé aux managers"}), 403
        return fn(*args, **kwargs)
    return wrapper

def livreur_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        claims = get_jwt()
        if not _role_is_allowed(claims.get("role"), {"livreur", "gerant"}):
            return jsonify({"msg": "Accès réservé aux gérants"}), 403
        return fn(*args, **kwargs)
    return wrapper


def role_required(*roles):
    """
    Décorateur générique pour autoriser plusieurs rôles.
    Exemple : @role_required("admin", "staff", "livreur")
    """
    def wrapper(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            verify_jwt_in_request()
            claims = get_jwt()
            user_role = claims.get("role")

            if not _role_is_allowed(user_role, set(roles)):
                return jsonify({"msg": "Accès interdit pour le rôle actuel."}), 403

            return fn(*args, **kwargs)
        return decorated_view
    return wrapper


def permission_requise(cle):
    """Droit configurable par magasin (services/permissions_service.py).

    À placer sous @role_required, qui liste tous les rôles pouvant recevoir le
    droit : ce décorateur vérifie ensuite ce que l'admin du magasin a choisi.
    """
    def wrapper(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            from services.permissions_service import CATALOGUE, droit_courant

            verify_jwt_in_request()
            if not droit_courant(cle):
                libelle = CATALOGUE[cle]["libelle"]
                return jsonify({
                    "message": f"Vous n'avez pas le droit « {libelle} ». Demandez-le à l'administrateur du magasin.",
                    "msg": "Accès interdit pour le rôle actuel.",
                }), 403
            return fn(*args, **kwargs)
        return decorated_view
    return wrapper
