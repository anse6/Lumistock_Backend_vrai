# routes/ristourne_routes.py
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from utils.decorators import role_required
from services.ristourne_service import get_stats_ristournes, get_ristournes_par_client
from services.pertes_service import get_pertes_par_periode_total
from services.ristourne_service import get_clients_du_manager_service
from utils.inject_magasin_id import inject_magasin_id
from services.user_service import get_livreur_service



ristourne_bp = Blueprint("ristourne", __name__)


@ristourne_bp.route("/ristournes/stats/pertes-par-periode", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def route_pertes_par_periode_total():
    """
    Obtenir les pertes par période (ristournes), avec détails et variation.
    ---
    tags:
      - Ristournes
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
          enum: [jour, semaine, mois]
        example: "jour"
        description: Granularité temporelle pour les pertes (jour/semaine/mois). Défaut  jour.
      - in: query
        name: livreur_id
        required: false
        schema:
          type: string
        example: "64fd937b918a2047ddc5a332"
        description: ID du livreur pour filtrer les pertes (facultatif)
      - in: query
        name: page
        required: false
        schema:
          type: integer
        example: 0
        description: Numéro de page pour la pagination. Défaut  0.
    responses:
      200:
        description: Statistiques des pertes par période récupérées avec succès
        content:
          application/json:
            schema:
              type: object
              properties:
                pertes:
                  type: array
                  description: Liste des pertes groupées par période
                  items:
                    type: object
                    properties:
                      periode:
                        type: string
                        example: "2025-07-01"
                      montant_perdu:
                        type: number
                        format: float
                        example: 12450.75
                      variation:
                        type: number
                        format: float
                        example: -5.2
                livreurs:
                  type: array
                  description: Liste des livreurs disponibles
                  items:
                    type: object
                    properties:
                      id:
                        type: string
                        example: "64fd937b918a2047ddc5a332"
                      nom:
                        type: string
                        example: "Jean Dupont"
      400:
        description: Requête invalide (ex mauvais format de paramètres)
        content:
          application/json:
            schema:
              type: object
              properties:
                error:
                  type: string
                  example: "Paramètre 'page' invalide"
      401:
        description: Non autorisé (JWT manquant ou invalide)
      403:
        description: Accès interdit (rôle insuffisant)
      500:
        description: Erreur interne du serveur
        content:
          application/json:
            schema:
              type: object
              properties:
                error:
                  type: string
                  example: "Erreur interne  Erreur inconnue lors de la récupération des clients."
    security:
      - JWT: []
    """

    granularite = request.args.get("granularite", "jour")
    livreur_id = request.args.get("livreur_id")
    page = int(request.args.get("page", 0))

    try:
        data = inject_magasin_id({})
        magasin_id = data["magasin_id"]

        pertes_data = get_pertes_par_periode_total(livreur_id, granularite, page, magasin_id)

        livreur_result = get_livreur_service()

        if livreur_result.get("status") != 200:
            raise Exception(livreur_result.get("error", "Erreur inconnue lors de la récupération des clients."))

        return jsonify({
            "pertes": pertes_data,
            "livreurs": livreur_result["livreurs"]
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Erreur interne : {str(e)}"}), 500





