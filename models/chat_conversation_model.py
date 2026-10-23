from extensions import db
from bson import ObjectId
from datetime import datetime

class ChatConversationModel:
    """Modèle pour gérer les conversations de chat IA"""

    @staticmethod
    def get_collection():
        return db.tables.chat_conversations

    @staticmethod
    def create(user_id, magasin_id, title=None):
        """Créer une nouvelle conversation"""
        conversation = {
            "user_id": ObjectId(user_id),
            "magasin_id": ObjectId(magasin_id),
            "title": title or "Nouvelle conversation",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        result = ChatConversationModel.get_collection().insert_one(conversation)
        conversation['_id'] = result.inserted_id
        return conversation
    
    @staticmethod
    def find_by_id(conversation_id):
        """Trouver une conversation par ID"""
        return ChatConversationModel.get_collection().find_one({"_id": ObjectId(conversation_id)})
    
    @staticmethod
    def find_by_user(user_id, magasin_id):
        """Récupérer toutes les conversations d'un utilisateur"""
        return list(ChatConversationModel.get_collection().find({
            "user_id": ObjectId(user_id),
            "magasin_id": ObjectId(magasin_id)
        }).sort("updated_at", -1))
    
    @staticmethod
    def update_title(conversation_id, title):
        """Mettre à jour le titre d'une conversation"""
        result = ChatConversationModel.get_collection().update_one(
            {"_id": ObjectId(conversation_id)},
            {
                "$set": {
                    "title": title,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        return result.modified_count > 0
    
    @staticmethod
    def update_timestamp(conversation_id):
        """Mettre à jour le timestamp d'une conversation"""
        ChatConversationModel.get_collection().update_one(
            {"_id": ObjectId(conversation_id)},
            {"$set": {"updated_at": datetime.utcnow()}}
        )
    
    @staticmethod
    def delete(conversation_id):
        """Supprimer une conversation"""
        result = ChatConversationModel.get_collection().delete_one({"_id": ObjectId(conversation_id)})
        return result.deleted_count > 0
    
    @staticmethod
    def delete_all_by_user(user_id, magasin_id):
        """Supprimer toutes les conversations d'un utilisateur"""
        result = ChatConversationModel.get_collection().delete_many({
            "user_id": ObjectId(user_id),
            "magasin_id": ObjectId(magasin_id)
        })
        return result.deleted_count
    
    @staticmethod
    def verify_ownership(conversation_id, user_id, magasin_id):
        """Vérifier qu'une conversation appartient bien à un utilisateur"""
        conversation = ChatConversationModel.get_collection().find_one({
            "_id": ObjectId(conversation_id),
            "user_id": ObjectId(user_id),
            "magasin_id": ObjectId(magasin_id)
        })
        return conversation is not None
