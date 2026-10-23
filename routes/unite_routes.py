from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from services.unite_service import UniteService
from utils.decorators import role_required
from utils.inject_magasin_id import inject_magasin_id

unite_bp = Blueprint("unite_bp", __name__)

@unite_bp.route("/", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def create_unite():
    """ Créer une nouvelle unité (ex: KG, Litre, Pièce) """
    try:
        req_data = request.get_json() or {}
        data = inject_magasin_id(req_data)
        magasin_id = data["magasin_id"]
        
        response = UniteService.create_unite(data, magasin_id)
        return jsonify(response), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@unite_bp.route("/", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur") # Les livreurs peuvent avoir besoin d'afficher les unités
def get_unites():
    """ Récupérer la liste des unités du magasin """
    try:
        data = inject_magasin_id({})
        magasin_id = data["magasin_id"]
        
        unites = UniteService.get_unites(magasin_id)
        return jsonify(unites), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
