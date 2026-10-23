# models/notitifaction_model.py

from datetime import datetime
from bson import ObjectId
from extensions import db
from utils.inject_magasin_id import inject_magasin_id



class NotificationModel:
    @staticmethod
    def get_collection():
        return db.tables.notifications
    
    
    
   
    @staticmethod
    def create_notification(data):
        """
        Crée une nouvelle notification.
        """
        data = inject_magasin_id(data)
        
        notification = {
            "title": data["title"],
            "message": data["message"],
            "type": data.get("type", "info"),
            "lien": data.get("lien"),
            "timestamp": datetime.utcnow(),
            "created_at": datetime.utcnow(),
            "vu": False,
            "archivee": False,
            "magasin_id": ObjectId(data["magasin_id"]) 
        }
        
        result = NotificationModel.get_collection().insert_one(notification)
        return str(result.inserted_id)

    @staticmethod
    def get_notifications():
        """
        Récupère toutes les notifications dont vu est à False en filtrant par magasin_id.
        """
        data = inject_magasin_id({})
        
        notifications = NotificationModel.get_collection().find({"vu": False, "magasin_id": ObjectId(data["magasin_id"]) }).sort("created_at", -1)
        return [{
            "id": str(n["_id"]),
            "title": n["title"],
            "message": n["message"],
            "vu" : n.get("vu", False),
            "archivee": n.get("archivee", False),
            "created_at": n["created_at"].isoformat()
        } for n in notifications]

    @staticmethod
    def marquer_comme_vu(notification_id):
        """
        Marque une notification comme vue.
        """
        result = NotificationModel.get_collection().update_one(
            {"_id": ObjectId(notification_id)},
            {"$set": {"vu": True}}
        )
        return result.modified_count > 0
    
    @staticmethod
    def archiver_notification(notification_id):
        """
        Archive une notification.
        """
        result = NotificationModel.get_collection().update_one(
            {"_id": ObjectId(notification_id)},
            {"$set": {"archivee": True}}
        )
        return result.modified_count > 0
    
    
    #collection = db.tables.notifications