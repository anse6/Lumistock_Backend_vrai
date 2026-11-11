from flask import Blueprint, request, jsonify
from bson import ObjectId
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.fournisseur_model import FournisseurModel
from services.fournisseur_service import get_top_suppliers
from schemas.fournisseur_schema import FournisseurSchema, UpdateFournisseurSchema
from utils.inject_magasin_id import inject_magasin_id
from utils.decorators import permission_requise, role_required
from marshmallow import ValidationError

fournisseur_bp = Blueprint('fournisseur_bp', __name__)

@fournisseur_bp.route('/fournisseurs', methods=['POST'])
@jwt_required()
def create_fournisseur():
    try:
        data = request.get_json()
        
        # Injection du magasin_id depuis le token utilisateur
        data = inject_magasin_id(data)
        
        # Validation des données
        schema = FournisseurSchema()
        data = schema.load(data)
        
        result = FournisseurModel.create_fournisseur(data)
        
        if isinstance(result, tuple):
            return jsonify(result[0]), result[1]
            
        return jsonify(result), 201
        
    except ValidationError as err:
        return jsonify(err.messages), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@fournisseur_bp.route('/fournisseurs', methods=['GET'])
@jwt_required()
def get_fournisseurs():
    try:
        data = inject_magasin_id({})
        magasin_id = data.get("magasin_id")
        
        fournisseurs = FournisseurModel.get_all_by_magasin(magasin_id)
        from services.referentiels_service import compter_produits
        nombres = compter_produits(magasin_id, "fournisseur_id")

        # Conversion des ObjectIds en str pour le JSON
        for f in fournisseurs:
            f["_id"] = str(f["_id"])
            f["magasin_id"] = str(f["magasin_id"])
            f["nb_produits"] = nombres.get(f["_id"], 0)
            
        return jsonify(fournisseurs), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@fournisseur_bp.route('/fournisseurs/top-5', methods=['GET'])
@jwt_required()
def get_top_5_fournisseurs():
    try:
        data = inject_magasin_id({})
        magasin_id = data.get("magasin_id")
        
        top_fournisseurs = get_top_suppliers(magasin_id)
        
        return jsonify(top_fournisseurs), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def _magasin_courant():
    """Magasin de l'utilisateur connecté, ou None s'il n'en a pas."""
    try:
        return inject_magasin_id({})["magasin_id"]
    except Exception:
        return None


def _introuvable():
    # Un fournisseur d'un autre magasin répond comme s'il n'existait pas.
    return jsonify({"message": "Fournisseur non trouvé"}), 404


@fournisseur_bp.route('/fournisseurs/<fournisseur_id>', methods=['GET'])
@jwt_required()
def get_fournisseur(fournisseur_id):
    try:
        fournisseur = FournisseurModel.get_for_magasin(fournisseur_id, _magasin_courant())
        if not fournisseur:
            return _introuvable()

        fournisseur["_id"] = str(fournisseur["_id"])
        fournisseur["magasin_id"] = str(fournisseur["magasin_id"])
        return jsonify(fournisseur), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@fournisseur_bp.route('/fournisseurs/<fournisseur_id>', methods=['PUT'])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("produits.modifier")
def update_fournisseur(fournisseur_id):
    try:
        magasin_id = _magasin_courant()
        if not FournisseurModel.get_for_magasin(fournisseur_id, magasin_id):
            return _introuvable()
        data = UpdateFournisseurSchema().load(request.get_json(silent=True) or {})
        if not data:
            return jsonify({"message": "Aucune donnée à mettre à jour"}), 400
        if "nom" in data:
            data["nom"] = data["nom"].strip()
            if not data["nom"]:
                return jsonify({"message": "Le nom du fournisseur ne peut pas être vide."}), 400
            homonyme = FournisseurModel.get_collection().find_one(
                {"nom": data["nom"], "magasin_id": ObjectId(str(magasin_id))})
            if homonyme and str(homonyme["_id"]) != str(fournisseur_id):
                return jsonify({"message": "Un autre fournisseur de ce magasin porte déjà ce nom."}), 409

        FournisseurModel.update_fournisseur(fournisseur_id, magasin_id, data)
        return jsonify({"message": "Fournisseur mis à jour avec succès"}), 200

    except ValidationError as err:
        return jsonify(err.messages), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@fournisseur_bp.route('/fournisseurs/<fournisseur_id>', methods=['DELETE'])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("produits.modifier")
def delete_fournisseur(fournisseur_id):
    try:
        magasin_id = _magasin_courant()
        if not FournisseurModel.get_for_magasin(fournisseur_id, magasin_id):
            return _introuvable()
        if FournisseurModel.est_utilise(fournisseur_id):
            return jsonify({"message": "Ce fournisseur figure sur des factures ou des articles : "
                                       "il ne peut pas être supprimé."}), 409
        FournisseurModel.delete_fournisseur(fournisseur_id, magasin_id)
        return jsonify({"message": "Fournisseur supprimé avec succès"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
