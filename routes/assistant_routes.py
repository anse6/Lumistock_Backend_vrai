# routes/assistant_routes.py
"""Chat du site public avec Nansé, l'assistante Lumistock.

Visiteur (public, jeton secret dans l'en-tête X-Assistant-Jeton) :
  POST /assistant/conversations                   démarrer (nom, email)
  GET  /assistant/conversations/<id>?depuis=…     lire les nouveaux messages
  POST /assistant/conversations/<id>/messages     écrire

Staff (super-admin) :
  GET   /assistant/admin/conversations            boîte de réception
  GET   /assistant/admin/non-lus                  pastille du menu
  GET   /assistant/admin/conversations/<id>       détail (marque lu)
  POST  /assistant/admin/conversations/<id>/messages   répondre
  PATCH /assistant/admin/conversations/<id>       {statut: ia | fermee}
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from database import sans_transaction
from models.user_model import UserModel
from services import assistant_service as svc
from utils.decorators import role_required
from utils.rate_limit import limite_atteinte, piege_rempli

assistant_bp = Blueprint("assistant", __name__, url_prefix="/assistant")


def _erreur(e):
    corps = {"message": str(e)}
    if getattr(e, "champ", None):
        corps["errors"] = {e.champ: [str(e)]}
    return jsonify(corps), getattr(e, "status", 400)


def _jeton():
    return request.headers.get("X-Assistant-Jeton", "")


def _ip():
    return request.remote_addr or "inconnue"


# --------------------------------------------------------------------------- visiteur
@assistant_bp.route("/conversations", methods=["POST"])
def demarrer():
    """
    Démarrer une conversation avec Nansé (public)
    ---
    tags:
      - Assistant
    description: Body {nom, email, langue?, page?}. Renvoie l'identifiant et le jeton secret de la conversation.
    responses:
      201:
        description: Conversation créée
      429:
        description: Trop de conversations depuis cette connexion
    """
    data = request.get_json(silent=True) or {}
    if piege_rempli(data.pop("site_web_societe", None)):  # champ invisible rempli : robot
        return jsonify({"message": "Demande refusée."}), 400
    if limite_atteinte(f"assistant-ip:{_ip()}", 10, 60 * 60):
        return jsonify({"message": "Vous avez ouvert plusieurs conversations récemment. Réessayez plus tard."}), 429
    try:
        conv, jeton = svc.demarrer(data, _ip())
    except svc.ErreurAssistant as e:
        return _erreur(e)
    return jsonify({**svc.serialiser_conversation(conv, []), "jeton": jeton}), 201


@assistant_bp.route("/conversations/<conversation_id>", methods=["GET"])
def lire(conversation_id):
    try:
        conv = svc.acceder(conversation_id, _jeton())
        return jsonify(svc.lire_visiteur(conv, request.args.get("depuis"))), 200
    except svc.ErreurAssistant as e:
        return _erreur(e)


@assistant_bp.route("/conversations/<conversation_id>/messages", methods=["POST"])
@sans_transaction  # le message du visiteur est gardé même si l'IA tarde ou échoue
def ecrire(conversation_id):
    try:
        conv = svc.acceder(conversation_id, _jeton())
    except svc.ErreurAssistant as e:
        return _erreur(e)
    if limite_atteinte(f"assistant-conv:{conversation_id}", 20, 5 * 60) or limite_atteinte(f"assistant-msg-ip:{_ip()}", 60, 60 * 60):
        return jsonify({"message": "Vous écrivez très vite ! Patientez un instant avant d'envoyer un autre message."}), 429
    try:
        conv, nouveaux = svc.envoyer_visiteur(conv, (request.get_json(silent=True) or {}).get("texte"))
    except svc.ErreurAssistant as e:
        return _erreur(e)
    return jsonify({**svc.serialiser_conversation(conv), "nouveaux": [svc.serialiser_message(m) for m in nouveaux]}), 201


# --------------------------------------------------------------------------- staff
@assistant_bp.route("/admin/conversations", methods=["GET"])
@jwt_required()
@role_required("staff")
def lister():
    return jsonify(svc.lister(request.args.get("statut"), request.args.get("q"))), 200


@assistant_bp.route("/admin/non-lus", methods=["GET"])
@jwt_required()
@role_required("staff")
def non_lus():
    return jsonify({"non_lus": svc.non_lus()}), 200


@assistant_bp.route("/admin/conversations/<conversation_id>", methods=["GET"])
@jwt_required()
@role_required("staff")
def ouvrir(conversation_id):
    try:
        return jsonify(svc.ouvrir(conversation_id, request.args.get("depuis"))), 200
    except svc.ErreurAssistant as e:
        return _erreur(e)


@assistant_bp.route("/admin/conversations/<conversation_id>/messages", methods=["POST"])
@jwt_required()
@role_required("staff")
def repondre(conversation_id):
    staff = UserModel.get_user_by_id(get_jwt_identity())
    if not staff:
        return jsonify({"message": "Compte introuvable."}), 404
    try:
        return jsonify(svc.repondre(conversation_id, (request.get_json(silent=True) or {}).get("texte"), staff)), 201
    except svc.ErreurAssistant as e:
        return _erreur(e)


@assistant_bp.route("/admin/conversations/<conversation_id>", methods=["PATCH"])
@jwt_required()
@role_required("staff")
def changer_statut(conversation_id):
    try:
        return jsonify(svc.changer_statut(conversation_id, (request.get_json(silent=True) or {}).get("statut"))), 200
    except svc.ErreurAssistant as e:
        return _erreur(e)
