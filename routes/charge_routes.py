# routes/charge_routes.py

from datetime import datetime

from flask import Blueprint, request, jsonify, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required
from marshmallow import ValidationError

from models.charge_model import ChargeModel
from schemas.charge_schema import CreateChargeSchema, PayerChargeSchema, UpdateChargeSchema
from services import compta_service
from services.cloture_service import PeriodeVerrouillee, verifier_dates
from utils import pieces_jointes
from utils.decorators import role_required
from utils.inject_magasin_id import inject_magasin_id

charge_bp = Blueprint("charge_bp", __name__)


def _magasin():
    return inject_magasin_id({})["magasin_id"]


def _json(charge):
    """Charge sérialisable, avec son compte SYSCOHADA."""
    c = dict(charge)
    c["_id"] = str(c["_id"])
    c["magasin_id"] = str(c.get("magasin_id"))
    c["statut"] = c.get("statut") or "payee"
    c["montant_tva"] = float(c.get("montant_tva") or 0)
    c["recurrence"] = c.get("recurrence") or "aucune"
    if c["statut"] == "payee" and not c.get("date_paiement"):
        c["date_paiement"] = c.get("date_charge")
    # Le nom du fichier sur le disque reste côté serveur.
    c["pieces_jointes"] = [{k: v for k, v in p.items() if k != "fichier"} for p in c.get("pieces_jointes") or []]
    for champ in ("date_charge", "date_paiement", "date_echeance", "created_at", "updated_at"):
        if isinstance(c.get(champ), datetime):
            c[champ] = c[champ].isoformat()
    libelle, compte, rubrique = compta_service.categorie_charge(c.get("categorie"))
    c["compte"] = compte
    return c


@charge_bp.route("/", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def create_charge():
    """
    Créer une nouvelle charge (dépense)
    ---
    tags:
      - Charges
    summary: Enregistrer une dépense, payée ou à payer
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [nom, montant, categorie]
          properties:
            nom: {type: string, example: "Loyer octobre"}
            montant: {type: number, example: 150000, description: Montant TTC}
            montant_tva: {type: number, example: 0, description: TVA récupérable comprise dans le montant}
            categorie: {type: string, example: "loyer"}
            date_charge: {type: string, format: date-time}
            fournisseur: {type: string}
            reference: {type: string, description: N° de facture du fournisseur}
            statut: {type: string, enum: [payee, a_payer]}
            date_paiement: {type: string, format: date-time}
            date_echeance: {type: string, format: date-time}
            mode_paiement: {type: string, example: "cash"}
            recurrence: {type: string, enum: [aucune, mensuelle, trimestrielle, annuelle]}
            description: {type: string}
    responses:
      201:
        description: Charge créée avec succès
      400:
        description: Erreur de validation
    """
    try:
        data = CreateChargeSchema().load(request.json or {})
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400
    try:
        data = inject_magasin_id(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 401

    try:
        verifier_dates(data["magasin_id"], data.get("date_charge") or datetime.now(), data.get("date_paiement"))
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    charge_id = ChargeModel.create_charge(data)
    return jsonify({"message": "Charge enregistrée avec succès", "charge_id": charge_id}), 201


@charge_bp.route("/", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_all_charges():
    """
    Récupérer toutes les charges du magasin
    ---
    tags:
      - Charges
    summary: Lister les dépenses
    responses:
      200:
        description: Liste des charges
    """
    try:
        magasin_id = _magasin()
    except Exception as e:
        return jsonify({"error": str(e)}), 401
    return jsonify([_json(c) for c in ChargeModel.get_all_by_magasin(magasin_id)]), 200


@charge_bp.route("/<charge_id>", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
def update_charge(charge_id):
    """
    Modifier une charge du magasin
    ---
    tags:
      - Charges
    summary: Modifier une dépense
    """
    try:
        data = UpdateChargeSchema().load(request.json or {}, partial=True)
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400
    magasin_id = _magasin()
    actuelle = ChargeModel.get_for_magasin(charge_id, magasin_id)
    if not actuelle:
        return jsonify({"error": "Charge non trouvée"}), 404
    try:
        verifier_dates(magasin_id, actuelle.get("date_charge"), actuelle.get("date_paiement"),
                       data.get("date_charge"), data.get("date_paiement"))
        updated_charge = ChargeModel.update_charge(charge_id, magasin_id, data)
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not updated_charge:
        return jsonify({"error": "Charge non trouvée"}), 404
    return jsonify({"message": "Charge mise à jour avec succès", "charge": _json(updated_charge)}), 200


@charge_bp.route("/<charge_id>/payer", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def payer_charge(charge_id):
    """
    Marquer une charge comme payée
    ---
    tags:
      - Charges
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            date_paiement: {type: string, format: date-time}
            mode_paiement: {type: string, example: "om"}
    """
    try:
        data = PayerChargeSchema().load(request.json or {})
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400
    try:
        verifier_dates(_magasin(), data.get("date_paiement") or datetime.now())
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    charge = ChargeModel.payer(charge_id, _magasin(), data.get("date_paiement"), data.get("mode_paiement"))
    if not charge:
        return jsonify({"error": "Charge non trouvée"}), 404
    return jsonify({"message": "Charge payée", "charge": _json(charge)}), 200


@charge_bp.route("/recurrentes/generer", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def generer_recurrentes():
    """
    Créer les charges récurrentes du mois (loyer, salaires…), à payer
    ---
    tags:
      - Charges
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            mois: {type: string, example: "2026-10", description: Mois visé (par défaut le mois en cours)}
    """
    mois = (request.get_json(silent=True) or {}).get("mois") or datetime.now().strftime("%Y-%m")
    try:
        annee, numero = (int(x) for x in str(mois).split("-")[:2])
        if not 1 <= numero <= 12:
            raise ValueError
    except ValueError:
        return jsonify({"error": "Mois invalide (format AAAA-MM)"}), 400
    try:
        verifier_dates(_magasin(), datetime(annee, numero, 1))
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    creees = compta_service.generer_charges_recurrentes(_magasin(), annee, numero)
    return jsonify({"message": f"{len(creees)} charge(s) récurrente(s) créée(s)", "charges": creees}), 201 if creees else 200


@charge_bp.route("/<charge_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
def delete_charge(charge_id):
    """
    Supprimer une charge du magasin
    ---
    tags:
      - Charges
    summary: Supprimer une dépense
    """
    magasin_id = _magasin()
    actuelle = ChargeModel.get_for_magasin(charge_id, magasin_id)
    if not actuelle:
        return jsonify({"error": "Charge non trouvée"}), 404
    try:
        verifier_dates(magasin_id, actuelle.get("date_charge"), actuelle.get("date_paiement"))
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    ChargeModel.delete_charge(charge_id, magasin_id)
    for piece in actuelle.get("pieces_jointes") or []:
        pieces_jointes.supprimer(piece)
    return jsonify({"message": "Charge supprimée avec succès"}), 200


# --------------------------------------------------------------------------- pièces justificatives
@charge_bp.route("/<charge_id>/pieces", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def ajouter_piece(charge_id):
    """
    Joindre une facture ou un reçu (PDF, PNG, JPG, WEBP ; 10 Mo maximum)
    ---
    tags:
      - Charges
    consumes: [multipart/form-data]
    parameters:
      - {in: formData, name: fichier, type: file, required: true}
    """
    magasin_id = _magasin()
    charge = ChargeModel.get_for_magasin(charge_id, magasin_id)
    if not charge:
        return jsonify({"error": "Charge non trouvée"}), 404
    if len(charge.get("pieces_jointes") or []) >= pieces_jointes.NOMBRE_MAX:
        return jsonify({"error": f"{pieces_jointes.NOMBRE_MAX} pièces au maximum par charge."}), 400
    try:
        piece = pieces_jointes.enregistrer(request.files.get("fichier"), get_jwt_identity())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    ChargeModel.get_collection().update_one({"_id": charge["_id"]}, {"$push": {"pieces_jointes": piece}})
    return jsonify({"message": "Pièce jointe ajoutée", "charge": _json(ChargeModel.get_for_magasin(charge_id, magasin_id))}), 201


@charge_bp.route("/<charge_id>/pieces/<piece_id>", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def lire_piece(charge_id, piece_id):
    """
    Télécharger une pièce jointe de la charge
    ---
    tags:
      - Charges
    """
    charge = ChargeModel.get_for_magasin(charge_id, _magasin())
    piece = next((p for p in (charge or {}).get("pieces_jointes") or [] if p.get("id") == piece_id), None)
    if not piece:
        return jsonify({"error": "Pièce jointe introuvable"}), 404
    return send_file(pieces_jointes.chemin(piece), mimetype=piece.get("type"), download_name=piece.get("nom"),
                     as_attachment=request.args.get("telecharger") == "1")


@charge_bp.route("/<charge_id>/pieces/<piece_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
def supprimer_piece(charge_id, piece_id):
    """
    Retirer une pièce jointe
    ---
    tags:
      - Charges
    """
    magasin_id = _magasin()
    charge = ChargeModel.get_for_magasin(charge_id, magasin_id)
    pieces = (charge or {}).get("pieces_jointes") or []
    piece = next((p for p in pieces if p.get("id") == piece_id), None)
    if not piece:
        return jsonify({"error": "Pièce jointe introuvable"}), 404
    ChargeModel.get_collection().update_one({"_id": charge["_id"]},
                                            {"$set": {"pieces_jointes": [p for p in pieces if p.get("id") != piece_id]}})
    pieces_jointes.supprimer(piece)
    return jsonify({"message": "Pièce jointe supprimée", "charge": _json(ChargeModel.get_for_magasin(charge_id, magasin_id))}), 200
