from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from marshmallow import ValidationError
from services.ai_service import ai_service, IAErreur, IANonConfiguree
from models.user_model import UserModel
from models.chat_conversation_model import ChatConversationModel
from models.chat_message_model import ChatMessageModel
from schemas.chat_schema import (
    ChatRequestSchema, 
    ChatConversationSchema, 
    ConversationDetailSchema,
    CreateConversationSchema,
    UpdateConversationSchema,
    ChatMessageSchema
)
from bson import ObjectId

ai_bp = Blueprint('ai', __name__, url_prefix='/ai')


def _reponse_erreur_ia(exc):
    """Message clair pour l'utilisateur ; le détail technique reste dans le journal du serveur."""
    if isinstance(exc, IANonConfiguree):
        return jsonify({"error": exc.message, "code": "ia_non_configuree"}), 503
    return jsonify({"error": IAErreur.message, "code": "ia_indisponible"}), 502

# Schémas
chat_request_schema = ChatRequestSchema()
chat_conversation_schema = ChatConversationSchema()
conversations_schema = ChatConversationSchema(many=True)
conversation_detail_schema = ConversationDetailSchema()
create_conversation_schema = CreateConversationSchema()
update_conversation_schema = UpdateConversationSchema()
message_schema = ChatMessageSchema()
messages_schema = ChatMessageSchema(many=True)


@ai_bp.route('/chat', methods=['POST'])
@jwt_required()
def chat():
    """
    Endpoint pour discuter avec l'IA avec gestion de l'historique.
    
    Body JSON:
    {
        "message": "votre question ici",
        "conversation_id": "optional-conversation-id"
    }
    
    Response:
    {
        "response": "Réponse de l'IA",
        "conversation_id": "conv-id",
        "message_id": "msg-id",
        "format": "markdown"
    }
    """
    try:
        data = chat_request_schema.load(request.get_json())
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400
    
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"error": "Le message est vide."}), 400
    conversation_id = data.get('conversation_id')

    user = UserModel.find_by_id(get_jwt_identity())
    if not user or not user.get('magasin_id'):
        return jsonify({"error": "Utilisateur non associé à un magasin"}), 403

    try:
        result = ai_service.chat(message, user, conversation_id)
    except LookupError:
        return jsonify({"error": "Conversation introuvable."}), 404
    except (IANonConfiguree, IAErreur) as exc:
        return _reponse_erreur_ia(exc)

    return jsonify({
        "response": result['response'],
        "conversation_id": result['conversation_id'],
        "message_id": result['message_id'],
        "format": "markdown"
    }), 200


@ai_bp.route('/conversations', methods=['GET'])
@jwt_required()
def get_conversations():
    """
    Récupérer toutes les conversations de l'utilisateur.
    
    Response:
    {
        "conversations": [
            {
                "id": "conv-id",
                "title": "Titre de la conversation",
                "created_at": "2026-02-08T12:00:00Z",
                "updated_at": "2026-02-08T13:00:00Z",
                "message_count": 5
            }
        ]
    }
    """
    user_id = get_jwt_identity()
    user = UserModel.find_by_id(user_id)
    
    if not user or 'magasin_id' not in user:
        return jsonify({"error": "Utilisateur non associé à un magasin"}), 403

    magasin_id = str(user['magasin_id'])
    
    conversations = ChatConversationModel.find_by_user(user_id, magasin_id)
    
    # Ajouter le nombre de messages pour chaque conversation
    for conv in conversations:
        conv['_id'] = str(conv['_id'])
        conv['message_count'] = ChatMessageModel.count_by_conversation(conv['_id'])
    
    return jsonify({
        "conversations": conversations_schema.dump(conversations)
    }), 200


@ai_bp.route('/conversations/<conversation_id>', methods=['GET'])
@jwt_required()
def get_conversation(conversation_id):
    """
    Récupérer une conversation spécifique avec tous ses messages.
    
    Response:
    {
        "id": "conv-id",
        "title": "Titre",
        "created_at": "...",
        "updated_at": "...",
        "messages": [
            {
                "id": "msg-id",
                "role": "user",
                "content": "Question...",
                "timestamp": "..."
            }
        ]
    }
    """
    user_id = get_jwt_identity()
    user = UserModel.find_by_id(user_id)
    
    if not user or 'magasin_id' not in user:
        return jsonify({"error": "Utilisateur non associé à un magasin"}), 403

    magasin_id = str(user['magasin_id'])
    
    # Vérifier que la conversation appartient à l'utilisateur
    if not ChatConversationModel.verify_ownership(conversation_id, user_id, magasin_id):
        return jsonify({"error": "Conversation non trouvée"}), 404
    
    conversation = ChatConversationModel.find_by_id(conversation_id)
    messages = ChatMessageModel.find_by_conversation(conversation_id)
    
    # Formater pour le schéma
    conversation['_id'] = str(conversation['_id'])
    for msg in messages:
        msg['_id'] = str(msg['_id'])
        msg['conversation_id'] = str(msg['conversation_id'])
    
    conversation['messages'] = messages
    
    return jsonify(conversation_detail_schema.dump(conversation)), 200


@ai_bp.route('/conversations', methods=['POST'])
@jwt_required()
def create_conversation():
    """
    Créer une nouvelle conversation.
    
    Body JSON:
    {
        "title": "Titre de la conversation"  // optionnel
    }
    
    Response:
    {
        "id": "conv-id",
        "title": "Titre",
        "created_at": "...",
        "updated_at": "..."
    }
    """
    try:
        data = create_conversation_schema.load(request.get_json() or {})
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400
    
    user_id = get_jwt_identity()
    user = UserModel.find_by_id(user_id)
    
    if not user or 'magasin_id' not in user:
        return jsonify({"error": "Utilisateur non associé à un magasin"}), 403

    magasin_id = str(user['magasin_id'])
    title = data.get('title', 'Nouvelle conversation')
    
    conversation = ChatConversationModel.create(user_id, magasin_id, title)
    conversation['_id'] = str(conversation['_id'])
    
    return jsonify(chat_conversation_schema.dump(conversation)), 201


@ai_bp.route('/conversations/<conversation_id>', methods=['PATCH'])
@jwt_required()
def update_conversation(conversation_id):
    """
    Mettre à jour le titre d'une conversation.
    
    Body JSON:
    {
        "title": "Nouveau titre"
    }
    """
    try:
        data = update_conversation_schema.load(request.get_json())
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400
    
    user_id = get_jwt_identity()
    user = UserModel.find_by_id(user_id)
    
    if not user or 'magasin_id' not in user:
        return jsonify({"error": "Utilisateur non associé à un magasin"}), 403

    magasin_id = str(user['magasin_id'])
    
    # Vérifier que la conversation appartient à l'utilisateur
    if not ChatConversationModel.verify_ownership(conversation_id, user_id, magasin_id):
        return jsonify({"error": "Conversation non trouvée"}), 404
    
    title = data.get('title')
    success = ChatConversationModel.update_title(conversation_id, title)
    
    if not success:
        return jsonify({"error": "Échec de la mise à jour"}), 500
    
    conversation = ChatConversationModel.find_by_id(conversation_id)
    conversation['_id'] = str(conversation['_id'])
    
    return jsonify({
        "success": True,
        "conversation": chat_conversation_schema.dump(conversation)
    }), 200


@ai_bp.route('/conversations/<conversation_id>', methods=['DELETE'])
@jwt_required()
def delete_conversation(conversation_id):
    """
    Supprimer une conversation et tous ses messages.
    
    Response:
    {
        "success": true,
        "message": "Conversation supprimée avec succès"
    }
    """
    user_id = get_jwt_identity()
    user = UserModel.find_by_id(user_id)
    
    if not user or 'magasin_id' not in user:
        return jsonify({"error": "Utilisateur non associé à un magasin"}), 403

    magasin_id = str(user['magasin_id'])
    
    # Vérifier que la conversation appartient à l'utilisateur
    if not ChatConversationModel.verify_ownership(conversation_id, user_id, magasin_id):
        return jsonify({"error": "Conversation non trouvée"}), 404
    
    # Supprimer d'abord tous les messages
    ChatMessageModel.delete_by_conversation(conversation_id)
    
    # Puis supprimer la conversation
    success = ChatConversationModel.delete(conversation_id)
    
    if not success:
        return jsonify({"error": "Échec de la suppression"}), 500
    
    return jsonify({
        "success": True,
        "message": "Conversation supprimée avec succès"
    }), 200


@ai_bp.route('/conversations', methods=['DELETE'])
@jwt_required()
def delete_all_conversations():
    """
    Supprimer toutes les conversations de l'utilisateur.
    
    Response:
    {
        "success": true,
        "deleted_count": 5,
        "message": "5 conversation(s) supprimée(s)"
    }
    """
    user_id = get_jwt_identity()
    user = UserModel.find_by_id(user_id)
    
    if not user or 'magasin_id' not in user:
        return jsonify({"error": "Utilisateur non associé à un magasin"}), 403

    magasin_id = str(user['magasin_id'])
    
    # Récupérer toutes les conversations pour supprimer leurs messages
    conversations = ChatConversationModel.find_by_user(user_id, magasin_id)
    
    for conv in conversations:
        ChatMessageModel.delete_by_conversation(str(conv['_id']))
    
    # Supprimer toutes les conversations
    deleted_count = ChatConversationModel.delete_all_by_user(user_id, magasin_id)
    
    return jsonify({
        "success": True,
        "deleted_count": deleted_count,
        "message": f"{deleted_count} conversation(s) supprimée(s)"
    }), 200


@ai_bp.route('/insights', methods=['GET'])
@jwt_required()
def get_insights():
    """
    Récupérer les analyses stratégiques et conseils business de l'IA.
    L'analyse est basée sur les stocks réels, les marges et les ventes.
    """
    user = UserModel.find_by_id(get_jwt_identity())
    if not user or not user.get('magasin_id'):
        return jsonify({"error": "Utilisateur non associé à un magasin"}), 403

    forcer = request.args.get("forcer") in ("1", "true", "oui")
    try:
        return jsonify(ai_service.get_business_insights(user, forcer=forcer)), 200
    except (IANonConfiguree, IAErreur) as exc:
        return _reponse_erreur_ia(exc)
