# routes/asset_routes.py

from datetime import datetime

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from marshmallow import ValidationError

from models.asset_model import TAUX_RESIDUEL, AssetModel
from schemas.asset_schema import CessionAssetSchema, CreateAssetSchema, UpdateAssetSchema
from services.cloture_service import PeriodeVerrouillee, verifier_dates
from utils.decorators import role_required
from utils.inject_magasin_id import inject_magasin_id

asset_bp = Blueprint("asset_bp", __name__)


def _magasin():
    return inject_magasin_id({})["magasin_id"]


def _json(a):
    """Immobilisation avec son plan d'amortissement à aujourd'hui."""
    maintenant = datetime.now()
    date_achat = a.get("date_achat")
    cession = AssetModel.date_cession(a)
    valeur_cedee = AssetModel.valeur_nette({**a, "date_cession": None}, cession) if cession else None
    return {
        "_id": str(a["_id"]),
        "nom": a.get("nom"),
        "valeur_achat": float(a.get("valeur_achat") or 0),
        "valeur_residuelle": float(a.get("valeur_residuelle") or 0),
        "date_achat": date_achat.isoformat() if isinstance(date_achat, datetime) else date_achat,
        "duree_vie_annees": a.get("duree_vie_annees"),
        "categorie": a.get("categorie"),
        "description": a.get("description"),
        "mode_paiement": a.get("mode_paiement") or "cash",
        "amortissement_mensuel": AssetModel.calculate_monthly_depreciation(a),
        "amortissement_annuel": round(AssetModel.base_amortissable(a) / int(a.get("duree_vie_annees") or 1), 2),
        "amortissements_cumules": round(AssetModel.amortissements_cumules(a, maintenant), 2),
        "valeur_nette": round(AssetModel.valeur_nette(a, maintenant), 2),
        "montant_tva": float(a.get("montant_tva") or 0),
        "residuelle_auto": bool(a.get("residuelle_auto")),
        "taux_residuel": TAUX_RESIDUEL.get(a.get("categorie"), TAUX_RESIDUEL["autre"]),
        "fournisseur": a.get("fournisseur") or "",
        "reference": a.get("reference") or "",
        "cession": {
            "date": cession.isoformat(), "prix": float(a.get("prix_cession") or 0),
            "mode_paiement": a.get("mode_paiement_cession") or "cash", "acheteur": a.get("acheteur") or "",
            "valeur_nette": round(valeur_cedee, 2),
            "plus_value": round(float(a.get("prix_cession") or 0) - valeur_cedee, 2),
        } if cession else None,
    }


@asset_bp.route("/", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def create_asset():
    """ Enregistrer une immobilisation (matériel, véhicule, mobilier…) """
    try:
        data = CreateAssetSchema().load(request.json or {})
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400
    try:
        data = inject_magasin_id(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 401

    try:
        verifier_dates(data["magasin_id"], data.get("date_achat") or datetime.now())
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    try:
        asset_id = AssetModel.create_asset(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"message": "Actif enregistré avec succès", "asset_id": asset_id}), 201


@asset_bp.route("/", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_all_assets():
    """ Lister les immobilisations du magasin, leur amortissement et leur valeur nette """
    try:
        magasin_id = _magasin()
    except Exception as e:
        return jsonify({"error": str(e)}), 401
    return jsonify([_json(a) for a in AssetModel.get_all_by_magasin(magasin_id)]), 200


@asset_bp.route("/<asset_id>", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
def update_asset(asset_id):
    """ Modifier une immobilisation du magasin """
    try:
        data = UpdateAssetSchema().load(request.json or {}, partial=True)
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400
    magasin_id = _magasin()
    actuel = AssetModel.get_for_magasin(asset_id, magasin_id)
    if not actuel:
        return jsonify({"error": "Actif non trouvé"}), 404
    try:
        # Changer la valeur ou la durée modifie les amortissements déjà passés.
        if any(k in data for k in ("valeur_achat", "valeur_residuelle", "date_achat", "duree_vie_annees", "categorie")):
            verifier_dates(magasin_id, actuel.get("date_achat"), data.get("date_achat"))
        updated_asset = AssetModel.update_asset(asset_id, magasin_id, data)
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not updated_asset:
        return jsonify({"error": "Actif non trouvé"}), 404
    return jsonify({"message": "Actif mis à jour avec succès", "asset": _json(updated_asset)}), 200


@asset_bp.route("/<asset_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
def delete_asset(asset_id):
    """ Supprimer une immobilisation du magasin """
    magasin_id = _magasin()
    actuel = AssetModel.get_for_magasin(asset_id, magasin_id)
    if not actuel:
        return jsonify({"error": "Actif non trouvé"}), 404
    try:
        verifier_dates(magasin_id, actuel.get("date_achat"))
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    AssetModel.delete_asset(asset_id, magasin_id)
    return jsonify({"message": "Actif supprimé avec succès"}), 200



@asset_bp.route("/taux-residuels", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def taux_residuels():
    """ Valeur résiduelle par défaut (en % de la valeur d'achat) de chaque catégorie """
    return jsonify(TAUX_RESIDUEL), 200


@asset_bp.route("/<asset_id>/cession", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def ceder_asset(asset_id):
    """ Enregistrer la vente (ou la mise au rebut, prix 0) d'une immobilisation """
    try:
        data = CessionAssetSchema().load(request.json or {})
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400
    magasin_id = _magasin()
    actuel = AssetModel.get_for_magasin(asset_id, magasin_id)
    if not actuel:
        return jsonify({"error": "Actif non trouvé"}), 404
    if actuel.get("date_cession"):
        return jsonify({"error": "Cette immobilisation est déjà cédée."}), 400
    try:
        verifier_dates(magasin_id, data["date_cession"])
        cede = AssetModel.ceder(asset_id, magasin_id, data["date_cession"], data["prix_cession"],
                                data.get("mode_paiement"), data.get("acheteur"))
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"message": "Cession enregistrée", "asset": _json(cede)}), 200


@asset_bp.route("/<asset_id>/cession", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
def annuler_cession_asset(asset_id):
    """ Annuler la cession d'une immobilisation """
    magasin_id = _magasin()
    actuel = AssetModel.get_for_magasin(asset_id, magasin_id)
    if not actuel or not actuel.get("date_cession"):
        return jsonify({"error": "Cession introuvable"}), 404
    try:
        verifier_dates(magasin_id, actuel.get("date_cession"))
    except PeriodeVerrouillee as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"message": "Cession annulée", "asset": _json(AssetModel.annuler_cession(asset_id, magasin_id))}), 200
