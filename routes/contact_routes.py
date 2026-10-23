# routes/contact_routes.py
"""Formulaire de contact du site public (POST /contact) et boîte de réception
du staff (super-admin) : /contact/messages…"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from models.user_model import UserModel
from services import contact_service
from utils.decorators import role_required
from utils.rate_limit import limite_atteinte, piege_rempli

contact_bp = Blueprint("contact", __name__, url_prefix="/contact")


def _erreur(e):
    corps = {"message": str(e)}
    if getattr(e, "champ", None):
        corps["errors"] = {e.champ: [str(e)]}
    return jsonify(corps), getattr(e, "status", 400)


@contact_bp.route("", methods=["POST"])
def envoyer_message():
    """
    Envoyer un message à l'équipe Lumistock (public)
    ---
    tags:
      - Contact
    description: >
      Body : {nom, email, telephone?, entreprise?, sujet, message, consentement: true}.
      sujet : demonstration, tarifs, inscription, support, partenariat, autre.
    responses:
      201:
        description: Message enregistré
      400:
        description: Champ invalide
      429:
        description: Trop de messages depuis cette connexion
    """
    data = request.get_json(silent=True) or {}
    # Anti-robots : champ invisible (rempli = robot) et limite par adresse IP.
    if piege_rempli(data.pop("site_web_societe", None)):
        return jsonify({"message": "Message refusé."}), 400
    ip = request.remote_addr or "inconnue"
    if limite_atteinte(f"contact-ip:{ip}", 5, 60 * 60):
        return jsonify({"message": "Vous avez envoyé plusieurs messages récemment. Réessayez dans une heure."}), 429
    try:
        contact_service.creer(data, ip)
    except contact_service.ErreurContact as e:
        return _erreur(e)
    return jsonify({"message": "Merci, votre message a bien été envoyé. Nous vous répondrons par email."}), 201


@contact_bp.route("/messages", methods=["GET"])
@jwt_required()
@role_required("staff")
def lister_messages():
    return jsonify(contact_service.lister(request.args.get("statut"), request.args.get("q"))), 200


@contact_bp.route("/messages/non-lus", methods=["GET"])
@jwt_required()
@role_required("staff")
def compter_non_lus():
    return jsonify({"non_lus": contact_service.non_lus()}), 200


@contact_bp.route("/messages/<message_id>", methods=["GET"])
@jwt_required()
@role_required("staff")
def lire_message(message_id):
    try:
        return jsonify(contact_service.ouvrir(message_id)), 200
    except contact_service.ErreurContact as e:
        return _erreur(e)


@contact_bp.route("/messages/<message_id>", methods=["PATCH"])
@jwt_required()
@role_required("staff")
def modifier_message(message_id):
    """Body : {statut?, note?, lu?}."""
    try:
        return jsonify(contact_service.modifier(message_id, request.get_json(silent=True) or {}, get_jwt_identity())), 200
    except contact_service.ErreurContact as e:
        return _erreur(e)


@contact_bp.route("/messages/<message_id>/repondre", methods=["POST"])
@jwt_required()
@role_required("staff")
def repondre_message(message_id):
    """Body : {texte}. Envoie la réponse par email au visiteur."""
    staff = UserModel.get_user_by_id(get_jwt_identity())
    try:
        return jsonify(contact_service.repondre(message_id, (request.get_json(silent=True) or {}).get("texte"), staff)), 200
    except contact_service.ErreurContact as e:
        return _erreur(e)


@contact_bp.route("/messages/<message_id>", methods=["DELETE"])
@jwt_required()
@role_required("staff")
def supprimer_message(message_id):
    try:
        contact_service.supprimer(message_id)
    except contact_service.ErreurContact as e:
        return _erreur(e)
    return jsonify({"message": "Message supprimé."}), 200
