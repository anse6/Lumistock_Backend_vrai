# routes/support_routes.py
"""Support interne entre les administrateurs de magasin et le staff Lumistock.

Mêmes routes pour les deux côtés (le service limite un administrateur à ses
propres conversations) :
  GET   /support/conversations?statut=&q=        liste
  GET   /support/non-lus                          pastille du menu
  POST  /support/conversations                    {sujet, texte, admin_id (staff seulement)}
  GET   /support/conversations/<id>?depuis=       détail (marque lu)
  POST  /support/conversations/<id>/messages      {texte}
  PATCH /support/conversations/<id>               {statut: ouverte | fermee}
Staff seulement :
  GET   /support/admins?q=                        administrateurs à qui écrire
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from models.user_model import UserModel
from services import support_service as svc
from utils.decorators import role_required
from utils.rate_limit import limite_atteinte

support_bp = Blueprint("support", __name__, url_prefix="/support")


def _erreur(e):
    corps = {"message": str(e)}
    if getattr(e, "champ", None):
        corps["errors"] = {e.champ: [str(e)]}
    return jsonify(corps), getattr(e, "status", 400)


def _moi():
    return UserModel.get_user_by_id(get_jwt_identity())


@support_bp.route("/conversations", methods=["GET"])
@jwt_required()
@role_required("admin", "staff")
def lister():
    """
    Conversations de support (admin : les siennes ; staff : toutes)
    ---
    tags:
      - Support
    responses:
      200:
        description: Liste et compteurs
    """
    return jsonify(svc.lister(_moi(), request.args.get("statut"), request.args.get("q"))), 200


@support_bp.route("/non-lus", methods=["GET"])
@jwt_required()
@role_required("admin", "staff")
def non_lus():
    return jsonify({"non_lus": svc.non_lus(_moi())}), 200


@support_bp.route("/admins", methods=["GET"])
@jwt_required()
@role_required("staff")
def admins():
    return jsonify({"admins": svc.admins(request.args.get("q"))}), 200


@support_bp.route("/conversations", methods=["POST"])
@jwt_required()
@role_required("admin", "staff")
def creer():
    user = _moi()
    if limite_atteinte(f"support-creer:{user['_id']}", 20, 60 * 60):
        return jsonify({"message": "Vous avez ouvert beaucoup de conversations récemment. Réessayez plus tard."}), 429
    try:
        return jsonify(svc.creer(user, request.get_json(silent=True) or {})), 201
    except svc.ErreurSupport as e:
        return _erreur(e)


@support_bp.route("/conversations/<conversation_id>", methods=["GET"])
@jwt_required()
@role_required("admin", "staff")
def ouvrir(conversation_id):
    try:
        return jsonify(svc.ouvrir(conversation_id, _moi(), request.args.get("depuis"))), 200
    except svc.ErreurSupport as e:
        return _erreur(e)


@support_bp.route("/conversations/<conversation_id>/messages", methods=["POST"])
@jwt_required()
@role_required("admin", "staff")
def ecrire(conversation_id):
    user = _moi()
    if limite_atteinte(f"support-msg:{user['_id']}", 60, 10 * 60):
        return jsonify({"message": "Vous écrivez très vite ! Patientez un instant."}), 429
    try:
        return jsonify(svc.ecrire(conversation_id, user, (request.get_json(silent=True) or {}).get("texte"))), 201
    except svc.ErreurSupport as e:
        return _erreur(e)


@support_bp.route("/conversations/<conversation_id>", methods=["PATCH"])
@jwt_required()
@role_required("admin", "staff")
def changer_statut(conversation_id):
    try:
        return jsonify(svc.changer_statut(conversation_id, _moi(), (request.get_json(silent=True) or {}).get("statut"))), 200
    except svc.ErreurSupport as e:
        return _erreur(e)
