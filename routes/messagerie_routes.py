# routes/messagerie_routes.py
"""Messagerie clients : paramètres, aperçu, envoi et historique des SMS/emails.

Les paramètres et les modèles sont réservés à l'administrateur du magasin ;
l'administrateur et le manager peuvent envoyer des messages.
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from services import messagerie_planification as plan
from services import messagerie_service as svc
from utils.decorators import role_required
from utils.decorators import permission_requise
from utils.inject_magasin_id import inject_magasin_id

messagerie_bp = Blueprint("messagerie", __name__, url_prefix="/messagerie")


def _magasin_id():
    return inject_magasin_id({})["magasin_id"]


def _erreur(e, code=400):
    return jsonify({"error": str(e)}), code


@messagerie_bp.route("/parametres", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def get_parametres():
    """
    Paramètres de messagerie du magasin
    ---
    tags:
      - Messagerie
    description: >
      Canaux activés, modèles de message par type de clients, options, variables
      disponibles, état de la configuration SMS/email de la plateforme et quota.
    responses:
      200:
        description: Paramètres
    """
    magasin_id = _magasin_id()
    return jsonify({
        "parametres": svc.get_parametres(magasin_id),
        "segments": svc.SEGMENTS,
        "variables": svc.VARIABLES,
        "longueurs_max": svc.LONGUEUR_MAX,
        "canaux_plateforme": svc.etat_canaux(),
        "quota": svc.quota_sms(magasin_id),
        "resume": svc.resume_segments(magasin_id),
        "frequences": plan.FREQUENCES,
        "jours_semaine": plan.JOURS_SEMAINE,
    }), 200


@messagerie_bp.route("/parametres", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("messagerie.parametres")
def put_parametres():
    """
    Modifier les paramètres et les modèles (admin)
    ---
    tags:
      - Messagerie
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            canaux:
              type: object
              example: {"sms": true, "email": true}
            modeles:
              type: object
              example: {"debiteurs": {"actif": true, "sms": "Bonjour {nom}, vous devez {dette}."}}
            options:
              type: object
              example: {"delai_relance_heures": 24}
    responses:
      200:
        description: Paramètres enregistrés
      400:
        description: Modèle invalide (variable inconnue, texte trop long…)
    """
    try:
        params = svc.enregistrer_parametres(_magasin_id(), request.get_json(silent=True) or {}, get_jwt_identity())
    except svc.ErreurMessagerie as e:
        return _erreur(e)
    return jsonify({"message": "Paramètres enregistrés.", "parametres": params}), 200


@messagerie_bp.route("/apercu", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def apercu():
    """
    Aperçu des destinataires et des messages
    ---
    tags:
      - Messagerie
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            segment:
              type: string
              enum: [debiteurs, avances, acheteurs, tous]
            canaux:
              type: array
              items:
                type: string
                enum: [sms, email]
    responses:
      200:
        description: Destinataires, message rendu pour chacun, raison si non joignable
    """
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(svc.apercu(_magasin_id(), data.get("segment"), data.get("canaux"), data.get("filtres"),
                                  bool(data.get("ignorer_delai")), data.get("client_ids"),
                                  data.get("personnalise"))), 200
    except svc.ErreurMessagerie as e:
        return _erreur(e)


@messagerie_bp.route("/campagnes", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def creer_campagne():
    """
    Envoyer un message à une catégorie de clients
    ---
    tags:
      - Messagerie
    description: >
      Les destinataires et les textes sont recalculés par le serveur (dette du
      jour). ``client_ids`` restreint l'envoi à une partie des clients.
      L'envoi se poursuit en arrière-plan : suivez-le dans l'historique.
      Avec ``date_envoi``, le message est programmé : destinataires et
      montants seront calculés au moment de l'envoi.
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [segment, canaux]
          properties:
            segment:
              type: string
            canaux:
              type: array
              items:
                type: string
            client_ids:
              type: array
              items:
                type: string
            filtres:
              type: object
              example: {"dette_minimum": 10000, "anciennete_minimum_jours": 7}
            ignorer_delai:
              type: boolean
            date_envoi:
              type: string
              example: "2026-10-20T09:00"
    responses:
      201:
        description: Campagne créée, envoi en cours (ou envoi programmé)
      400:
        description: Canal désactivé, modèle inactif, quota atteint…
    """
    data = request.get_json(silent=True) or {}
    client_ids = data.get("client_ids")
    if client_ids is not None and not isinstance(client_ids, list):
        return _erreur("client_ids doit être une liste.")
    return _envoyer_ou_programmer(data, data.get("segment"), client_ids, bool(data.get("ignorer_delai")))


def _envoyer_ou_programmer(data, segment, client_ids, ignorer_delai, type_envoi="manuel", origine=None):
    """Envoi immédiat, ou programmé si ``date_envoi`` est fourni."""
    magasin_id, user_id = _magasin_id(), get_jwt_identity()
    try:
        if data.get("date_envoi"):
            programme_id = plan.programmer_envoi(
                magasin_id, user_id, segment, data.get("canaux"), data["date_envoi"], client_ids,
                data.get("filtres"), ignorer_delai, data.get("personnalise"))
            return jsonify({"message": "Envoi programmé.", "programme_id": programme_id}), 201
        campagne_id = svc.creer_campagne(magasin_id, user_id, segment, data.get("canaux"), client_ids,
                                         data.get("filtres"), ignorer_delai, data.get("personnalise"),
                                         type_envoi=type_envoi, origine=origine)
    except svc.ErreurMessagerie as e:
        return _erreur(e)
    response = jsonify({"message": "Envoi lancé.", "campagne_id": campagne_id})
    response.status_code = 201
    _lancer_apres_reponse(response, campagne_id)
    return response


def _lancer_apres_reponse(response, campagne_id):
    # L'envoi démarre une fois la requête terminée (après l'enregistrement de
    # la campagne) : voir messagerie_service.lancer_campagne.
    svc.lancer_campagne(campagne_id)


@messagerie_bp.route("/campagnes", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def liste_campagnes():
    """
    Historique des envois du magasin
    ---
    tags:
      - Messagerie
    responses:
      200:
        description: Campagnes, de la plus récente à la plus ancienne
    """
    return jsonify({"campagnes": svc.liste_campagnes(_magasin_id())}), 200


@messagerie_bp.route("/campagnes/<campagne_id>", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def detail_campagne(campagne_id):
    """
    Détail d'un envoi : statut de chaque message
    ---
    tags:
      - Messagerie
    parameters:
      - in: path
        name: campagne_id
        type: string
        required: true
    responses:
      200:
        description: Campagne et messages
      404:
        description: Campagne introuvable
    """
    campagne = svc.detail_campagne(_magasin_id(), campagne_id)
    if not campagne:
        return _erreur("Campagne introuvable.", 404)
    return jsonify(campagne), 200


@messagerie_bp.route("/campagnes/<campagne_id>/reprendre", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def reprendre_campagne(campagne_id):
    """
    Reprendre un envoi interrompu
    ---
    tags:
      - Messagerie
    responses:
      200:
        description: Envoi relancé pour les messages encore en attente
    """
    try:
        cid = svc.reprendre_campagne(_magasin_id(), campagne_id)
    except svc.ErreurMessagerie as e:
        return _erreur(e)
    response = jsonify({"message": "Envoi relancé.", "campagne_id": cid})
    _lancer_apres_reponse(response, cid)
    return response


@messagerie_bp.route("/test", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("messagerie.parametres")
def envoyer_test():
    """
    Recevoir un modèle en test (valeurs d'exemple)
    ---
    tags:
      - Messagerie
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [segment, canal, destinataire]
          properties:
            segment:
              type: string
            canal:
              type: string
              enum: [sms, email]
            destinataire:
              type: string
              example: "+237690000000"
    responses:
      200:
        description: Test envoyé
    """
    from utils.rate_limit import limite_atteinte
    data = request.get_json(silent=True) or {}
    if limite_atteinte(f"messagerie-test:{_magasin_id()}", 10, 3600):
        return _erreur("Trop de tests envoyés. Réessayez dans une heure.", 429)
    try:
        message = svc.envoyer_test(_magasin_id(), data.get("segment"), data.get("canal"), data.get("destinataire"))
    except svc.ErreurMessagerie as e:
        return _erreur(e)
    return jsonify({"message": message}), 200


@messagerie_bp.route("/clients/<client_id>/preference", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
def preference_client(client_id):
    """
    Le client ne souhaite plus (ou de nouveau) recevoir de messages
    ---
    tags:
      - Messagerie
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            ne_pas_contacter:
              type: boolean
    responses:
      200:
        description: Préférence enregistrée
    """
    data = request.get_json(silent=True) or {}
    try:
        svc.definir_preference(_magasin_id(), client_id, data.get("ne_pas_contacter", True))
    except svc.ErreurMessagerie as e:
        return _erreur(e, 404)
    return jsonify({"message": "Préférence enregistrée."}), 200


@messagerie_bp.route("/clients/<client_id>/historique", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def historique_client(client_id):
    """
    Messages envoyés à un client
    ---
    tags:
      - Messagerie
    responses:
      200:
        description: Messages du plus récent au plus ancien
    """
    return jsonify({"messages": svc.historique_client(_magasin_id(), client_id)}), 200


# ---------------------------------------------------------------------------
# Message à un client
# ---------------------------------------------------------------------------
@messagerie_bp.route("/clients/<client_id>", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def situation_client(client_id):
    """
    Situation d'un client pour la messagerie (dette, avance, catégories)
    ---
    tags:
      - Messagerie
    responses:
      200:
        description: Situation du client
      404:
        description: Client introuvable
    """
    client = next((c for c in svc.situation_clients(_magasin_id()) if c["client_id"] == client_id), None)
    if not client:
        return _erreur("Client introuvable.", 404)
    publique = {k: v for k, v in client.items() if k != "achats"}
    publique["nombre_achats_non_soldes"] = len(client["achats"])
    return jsonify(publique), 200


@messagerie_bp.route("/clients/<client_id>/envoyer", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def envoyer_au_client(client_id):
    """
    Envoyer (ou programmer) un message à un seul client
    ---
    tags:
      - Messagerie
    description: >
      Utilise le modèle du type de message choisi (``segment``) ou un texte
      saisi pour l'occasion (``personnalise``). Le délai entre deux messages
      ne s'applique pas : l'envoi est une décision explicite. Avec
      ``date_envoi``, le message part à la date choisie, avec les montants du
      jour de l'envoi.
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [segment, canaux]
          properties:
            segment:
              type: string
            canaux:
              type: array
              items:
                type: string
            personnalise:
              type: object
              example: {"sms": "Bonjour {nom}, ..."}
            date_envoi:
              type: string
    responses:
      201:
        description: Message envoyé ou programmé
    """
    data = request.get_json(silent=True) or {}
    return _envoyer_ou_programmer(data, data.get("segment"), [client_id], True, type_envoi="individuel")


@messagerie_bp.route("/campagnes/<campagne_id>/renvoyer", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
def renvoyer_campagne(campagne_id):
    """
    Renvoyer un envoi passé aux mêmes clients (maintenant ou à une date)
    ---
    tags:
      - Messagerie
    description: >
      Les messages sont recalculés avec les montants du jour : un client qui a
      payé entre-temps n'est plus relancé pour sa dette.
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            date_envoi:
              type: string
    responses:
      201:
        description: Renvoi lancé ou programmé
    """
    data = request.get_json(silent=True) or {}
    try:
        campagne, client_ids = svc.clients_d_une_campagne(_magasin_id(), campagne_id)
    except svc.ErreurMessagerie as e:
        return _erreur(e, 404)
    if not client_ids:
        return _erreur("Cet envoi ne contient aucun client.")
    data = {**data, "canaux": data.get("canaux") or campagne.get("canaux_envoi")}
    return _envoyer_ou_programmer(data, campagne.get("segment"), client_ids, True,
                                  type_envoi="renvoi", origine={"campagne_id": campagne_id})


# ---------------------------------------------------------------------------
# Envois programmés
# ---------------------------------------------------------------------------
@messagerie_bp.route("/programmes", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def liste_programmes():
    """
    Envois programmés (à venir et récents)
    ---
    tags:
      - Messagerie
    responses:
      200:
        description: Envois programmés
    """
    return jsonify({"programmes": plan.liste_programmes(_magasin_id())}), 200


@messagerie_bp.route("/programmes/<programme_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
def annuler_programme(programme_id):
    """
    Annuler un envoi programmé
    ---
    tags:
      - Messagerie
    responses:
      200:
        description: Envoi annulé
    """
    try:
        plan.annuler_programme(_magasin_id(), programme_id)
    except svc.ErreurMessagerie as e:
        return _erreur(e)
    return jsonify({"message": "Envoi programmé annulé."}), 200


# ---------------------------------------------------------------------------
# Relances automatiques
# ---------------------------------------------------------------------------
@messagerie_bp.route("/regles", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def liste_regles():
    """
    Relances automatiques du magasin
    ---
    tags:
      - Messagerie
    responses:
      200:
        description: Règles, avec leur prochaine exécution
    """
    return jsonify({"regles": plan.liste_regles(_magasin_id())}), 200


@messagerie_bp.route("/regles", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("messagerie.parametres")
def creer_regle():
    """
    Créer une relance automatique (admin)
    ---
    tags:
      - Messagerie
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [nom, segment, canaux, frequence, heure]
          properties:
            nom:
              type: string
              example: "Relance hebdomadaire des dettes"
            segment:
              type: string
              example: debiteurs
            canaux:
              type: array
              items:
                type: string
              example: ["sms"]
            frequence:
              type: string
              enum: [quotidienne, hebdomadaire, mensuelle]
            jours:
              type: array
              items:
                type: integer
              example: [0]
            heure:
              type: string
              example: "09:00"
            filtres:
              type: object
              example: {"dette_minimum": 10000, "anciennete_minimum_jours": 7}
            actif:
              type: boolean
    responses:
      201:
        description: Relance créée
    """
    try:
        regle = plan.creer_regle(_magasin_id(), get_jwt_identity(), request.get_json(silent=True) or {})
    except svc.ErreurMessagerie as e:
        return _erreur(e)
    return jsonify({"message": "Relance automatique créée.", "regle": regle}), 201


@messagerie_bp.route("/regles/<regle_id>", methods=["PUT"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("messagerie.parametres")
def modifier_regle(regle_id):
    """
    Modifier, activer ou mettre en pause une relance automatique (admin)
    ---
    tags:
      - Messagerie
    responses:
      200:
        description: Relance modifiée
    """
    try:
        regle = plan.modifier_regle(_magasin_id(), regle_id, request.get_json(silent=True) or {})
    except svc.ErreurMessagerie as e:
        return _erreur(e)
    return jsonify({"message": "Relance automatique enregistrée.", "regle": regle}), 200


@messagerie_bp.route("/regles/<regle_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("messagerie.parametres")
def supprimer_regle(regle_id):
    """
    Supprimer une relance automatique (admin)
    ---
    tags:
      - Messagerie
    responses:
      200:
        description: Relance supprimée
    """
    try:
        plan.supprimer_regle(_magasin_id(), regle_id)
    except svc.ErreurMessagerie as e:
        return _erreur(e, 404)
    return jsonify({"message": "Relance automatique supprimée."}), 200


@messagerie_bp.route("/regles/<regle_id>/executer", methods=["POST"])
@jwt_required()
@role_required("admin", "manager")
@permission_requise("messagerie.parametres")
def executer_regle(regle_id):
    """
    Exécuter une relance automatique maintenant (admin)
    ---
    tags:
      - Messagerie
    description: Sa prochaine date prévue ne change pas.
    responses:
      200:
        description: Résultat de l'exécution
    """
    try:
        resultat = plan.executer_regle_maintenant(_magasin_id(), regle_id)
    except svc.ErreurMessagerie as e:
        return _erreur(e, 404)
    return jsonify({"resultat": resultat}), 200
