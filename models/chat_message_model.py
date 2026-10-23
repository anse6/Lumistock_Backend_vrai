from extensions import db
from bson import ObjectId
from datetime import datetime

class ChatMessageModel:
    """Modèle pour gérer les messages de chat IA"""

    @staticmethod
    def get_collection():
        return db.tables.chat_messages

    @staticmethod
    def create(conversation_id, role, content):
        """
        Créer un nouveau message
        role: 'user' ou 'assistant'
        """
        message = {
            "conversation_id": ObjectId(conversation_id),
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow()
        }
        result = ChatMessageModel.get_collection().insert_one(message)
        message['_id'] = result.inserted_id
        return message
    
    @staticmethod
    def find_by_conversation(conversation_id, limit=None):
        """Récupérer tous les messages d'une conversation"""
        query = {"conversation_id": ObjectId(conversation_id)}
        cursor = ChatMessageModel.get_collection().find(query).sort("timestamp", 1)
        
        if limit:
            cursor = cursor.limit(limit)
        
        return list(cursor)
    
    @staticmethod
    def get_conversation_history(conversation_id, limit=20):
        """
        Récupérer l'historique formaté pour l'IA
        Retourne une liste de dicts au format: [{"role": "user", "content": "..."}]
        """
        messages = ChatMessageModel.find_by_conversation(conversation_id)
        # Garder seulement les N derniers messages pour limiter le contexte
        if len(messages) > limit:
            messages = messages[-limit:]
        
        return [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages
        ]
    
    @staticmethod
    def delete_by_conversation(conversation_id):
        """Supprimer tous les messages d'une conversation"""
        result = ChatMessageModel.get_collection().delete_many({
            "conversation_id": ObjectId(conversation_id)
        })
        return result.deleted_count
    
    @staticmethod
    def count_by_conversation(conversation_id):
        """Compter le nombre de messages dans une conversation"""
        return ChatMessageModel.get_collection().count_documents({
            "conversation_id": ObjectId(conversation_id)
        })
    
    @staticmethod
    def get_last_user_message(conversation_id):
        """Récupérer le dernier message de l'utilisateur"""
        return ChatMessageModel.get_collection().find_one(
            {
                "conversation_id": ObjectId(conversation_id),
                "role": "user"
            },
            sort=[("timestamp", -1)]
        )
