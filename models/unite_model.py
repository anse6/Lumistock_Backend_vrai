from bson import ObjectId
from datetime import datetime
from extensions import db

class UniteModel:
    @staticmethod
    def get_collection():
        return db.tables.unites

    @staticmethod
    def create_unite(data):
        unite_doc = {
            "unite": data["unite"],
            "magasin_id": ObjectId(data["magasin_id"]),
            "created_at": datetime.utcnow()
        }
        result = UniteModel.get_collection().insert_one(unite_doc)
        return str(result.inserted_id)

    @staticmethod
    def get_all_by_magasin(magasin_id):
        # Trie par ordre alphabétique sur le champ "unite"
        return list(UniteModel.get_collection().find(
            {"magasin_id": ObjectId(magasin_id)}
        ).sort("unite", 1))
