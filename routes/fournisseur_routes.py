from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.fournisseur_model import FournisseurModel
from services.fournisseur_service import get_top_suppliers
from schemas.fournisseur_schema import FournisseurSchema, UpdateFournisseurSchema
from utils.inject_magasin_id import inject_magasin_id
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
        
        # Conversion des ObjectIds en str pour le JSON
        for f in fournisseurs:
            f["_id"] = str(f["_id"])
            f["magasin_id"] = str(f["magasin_id"])
            
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

@fournisseur_bp.route('/fournisseurs/<fournisseur_id>', methods=['GET'])
@jwt_required()
def get_fournisseur(fournisseur_id):
    try:
        fournisseur = FournisseurModel.get_by_id(fournisseur_id)
        if not fournisseur:
            return jsonify({"message": "Fournisseur non trouvé"}), 404
            
        fournisseur["_id"] = str(fournisseur["_id"])
        fournisseur["magasin_id"] = str(fournisseur["magasin_id"])
        return jsonify(fournisseur), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@fournisseur_bp.route('/fournisseurs/<fournisseur_id>', methods=['PUT'])
@jwt_required()
def update_fournisseur(fournisseur_id):
    try:
        data = request.get_json()
        schema = UpdateFournisseurSchema()
        data = schema.load(data)
        
        success = FournisseurModel.update_fournisseur(fournisseur_id, data)
        
        if success:
            return jsonify({"message": "Fournisseur mis à jour avec succès"}), 200
        else:
            return jsonify({"message": "Fournisseur non trouvé ou aucune modification"}), 404
            
    except ValidationError as err:
        return jsonify(err.messages), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@fournisseur_bp.route('/fournisseurs/<fournisseur_id>', methods=['DELETE'])
@jwt_required()
def delete_fournisseur(fournisseur_id):
    try:
        success = FournisseurModel.delete_fournisseur(fournisseur_id)
        if success:
            return jsonify({"message": "Fournisseur supprimé avec succès"}), 200
        else:
            return jsonify({"message": "Fournisseur non trouvé"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
