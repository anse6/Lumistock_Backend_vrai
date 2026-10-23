# routes/ristourne_routes.py
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from utils.decorators import role_required
from services.retour_service import get_stats_retours_par_periode_total
from utils.inject_magasin_id import inject_magasin_id
from services.user_service import get_livreur_service



retour_bp = Blueprint("retour", __name__)


@retour_bp.route("/retours/stats/par-periode", methods=["GET"])
@jwt_required()
@role_required("manager", "admin", "livreur")
def stats_retours():
    """
    Récupère les statistiques des retours par période (jour/semaine/mois/année)
    ---
    tags:
      - Retours
    parameters:
      - in: header
        name: Authorization
        required: true
        schema:
          type: string
        example: "Bearer votre.jwt.token"
        description: JWT token nécessaire pour l'autorisation
      - in: query
        name: granularite
        required: false
        schema:
          type: string
          enum: [jour, semaine, mois, annee]
        example: "jour"
        description: Niveau de granularité temporelle (jour, semaine, mois, année). Par défaut  jour.
      - in: query
        name: livreur_id
        required: false
        schema:
          type: string
        example: "64fd937b918a2047ddc5a332"
        description: ID du livreur pour filtrer les retours (facultatif)
      - in: query
        name: page
        required: false
        schema:
          type: integer
        example: 0
        description: Page de navigation pour la pagination des périodes. Par défaut  0.
    responses:
      200:
        description: Statistiques des retours récupérées avec succès
      400:
        description: Requête invalide
      401:
        description: Non autorisé
      403:
        description: Accès interdit
      500:
        description: Erreur interne
    security:
      - JWT: []
    """

    granularite = request.args.get("granularite", "jour")
    livreur_id = request.args.get("livreur_id", None)
    page = int(request.args.get("page", 0))

    try:
        data = inject_magasin_id({})
        magasin_id = data["magasin_id"]

        # Récupérer l'utilisateur actuel
        from models.user_model import UserModel
        current_user_id = get_jwt_identity()
        user = UserModel.find_by_id(current_user_id)

        if not user:
            return jsonify({"error": "Utilisateur non trouvé"}), 404

        # Si l'utilisateur est un livreur, il ne peut voir que ses propres statistiques
        if user.get("role") in ("livreur", "gerant"):
            livreur_id = str(user["_id"])

        retours_data = get_stats_retours_par_periode_total(livreur_id, granularite, page, magasin_id)

        livreur_result = get_livreur_service()

        if livreur_result.get("status") != 200:
            raise Exception(livreur_result.get("error", "Erreur inconnue lors de la récupération des livreurs."))

        return jsonify({
            "retours": retours_data,
            "livreurs": livreur_result["livreurs"]
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
