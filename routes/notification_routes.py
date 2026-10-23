# routes/notification_routes.py

from flask import Blueprint, request, jsonify, send_file, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from models.user_model import UserModel
from models.notification_model import NotificationModel
from services.audio_summary_service import audio_summary_service
from services.interactive_voice_service import interactive_voice_service
import os
import tempfile
from services.ia_contexte import IANonConfiguree, client_ia

notification_bp = Blueprint("notification", __name__)


@notification_bp.route("/notifications", methods=["GET"])
@jwt_required()
def lister_notifications():
    user = UserModel.get_user_by_id(get_jwt_identity())
    notifications = NotificationModel.get_notifications()
    if not notifications:
        return jsonify([])
    return jsonify(notifications)


@notification_bp.route("/notifications/<id>/vu", methods=["PATCH"])
@jwt_required()
def marquer_notification_vu(id):
    NotificationModel.marquer_comme_vu(id)
    return jsonify({"message": "Notification marquée comme lue."})


# archiver une notification

@notification_bp.route("/notifications/archive/<id>", methods=["PATCH"])
@jwt_required()
def archiver_notification(id):
    NotificationModel.archiver_notification(id)
    return jsonify({"message": "Notification archivée avec succès."})


@notification_bp.route("/notifications/audio-summary", methods=["GET"])
@jwt_required()
def obtenir_audio_summary():
    user_id = get_jwt_identity()
    user = UserModel.get_user_by_id(user_id)
    if not user or 'magasin_id' not in user:
        return jsonify({"error": "Utilisateur introuvable ou magasin non associé"}), 404
        
    magasin_id = str(user['magasin_id'])
    
    try:
        audio_path = audio_summary_service.generate_mp3(user_id, magasin_id)
        # Send file cache timeout 0 to avoid playing old file
        return send_file(audio_path, mimetype="audio/mpeg", max_age=0)
    except Exception:
        current_app.logger.exception("Génération audio impossible")
        return jsonify({"error": "Le message vocal n'a pas pu être généré. Réessayez."}), 502

@notification_bp.route("/notifications/ask-voice", methods=["POST"])
@jwt_required()
def demander_audio_interactif():
    user_id = get_jwt_identity()
    user = UserModel.get_user_by_id(user_id)
    if not user or 'magasin_id' not in user:
        return jsonify({"error": "Utilisateur introuvable ou magasin non associé"}), 404
        
    magasin_id = str(user['magasin_id'])
    
    # Récupérer la question vocale (sous forme de texte transcrit par le frontend)
    req_data = request.get_json() or {}
    question = req_data.get("question")
    
    if not question:
        return jsonify({"error": "La question est requise"}), 400
    
    try:
        audio_path = interactive_voice_service.generate_mp3(user_id, magasin_id, question)
        return send_file(audio_path, mimetype="audio/mpeg", max_age=0)
    except Exception:
        current_app.logger.exception("Génération audio impossible")
        return jsonify({"error": "Le message vocal n'a pas pu être généré. Réessayez."}), 502

@notification_bp.route("/notifications/ask-voice-audio", methods=["POST"])
@jwt_required()
def demander_audio_interactif_fichier():
    user_id = get_jwt_identity()
    user = UserModel.get_user_by_id(user_id)
    if not user or 'magasin_id' not in user:
        return jsonify({"error": "Utilisateur introuvable ou magasin non associé"}), 400
        
    magasin_id = str(user['magasin_id'])
    
    # 1. Récupérer le fichier audio envoyé par le Frontend
    if 'audio' not in request.files:
        return jsonify({"error": "Aucun fichier audio fourni"}), 400
        
    audio_file = request.files['audio']
    
    client = client_ia()
    if client is None:
        return jsonify({"error": IANonConfiguree.message, "code": "ia_non_configuree"}), 503

    webm_path = None
    try:
        # 2. Sauvegarder l'audio temporairement
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp_webm:
            audio_file.save(temp_webm.name)
            webm_path = temp_webm.name

        # 3. Transcrire la question avec Whisper (Groq)
        with open(webm_path, "rb") as fichier:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(webm_path), fichier.read()),
                model="whisper-large-v3",
                language="fr",
                response_format="text",
            )
        question_text = (transcription if isinstance(transcription, str) else getattr(transcription, "text", "")) or ""
        if not question_text.strip():
            return jsonify({"error": "Aucune parole détectée. Parlez plus près du micro et réessayez."}), 400

        # 4. Réponse vocale
        audio_path = interactive_voice_service.generate_mp3(user_id, magasin_id, question_text.strip())
        return send_file(audio_path, mimetype="audio/mpeg", max_age=0)
    except Exception:
        current_app.logger.exception("Traitement de la question vocale impossible")
        return jsonify({"error": "La question vocale n'a pas pu être traitée. Réessayez.", "code": "ia_indisponible"}), 502
    finally:
        # Le fichier temporaire est toujours supprimé (avant : os.path.remove, qui n'existe pas).
        if webm_path and os.path.exists(webm_path):
            os.remove(webm_path)
