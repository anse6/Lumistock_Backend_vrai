# models/approvisionnement_model.py

from bson import ObjectId
from datetime import datetime
from extensions import db

"""
Lorsqu’un admin ou manager approvisionne un livreur, un document est enregistré ici avec :

    l’admin_id ou manager_id

    le livreur_id

    la liste des produits avec leurs quantités

Ce modèle permet aussi de retrouver tous les approvisionnements d’un livreur, ou un approvisionnement en particulier.
"""

class ApprovisionnementModel:
    
    @staticmethod
    def get_collection():
        from extensions import db
        return db.tables.approvisionnements
    
    #collection = db.tables.approvisionnements

    @staticmethod
    def enregistrer_approvisionnement(data):
        approvisionnement = {
            "magasin_id": ObjectId(data["magasin_id"]),
            "admin_id": ObjectId(data["admin_id"]),  # ou manager_id
            "livreur_id": ObjectId(data["livreur_id"]),
            "produits": [
                {
                    "produit_id": ObjectId(item["produit_id"]),
                    "quantite": int(item["quantite"])
                } for item in data["produits"]
            ],
            "created_at": datetime.utcnow()
        }

        result = ApprovisionnementModel.get_collection().insert_one(approvisionnement)
        return str(result.inserted_id)

    @staticmethod
    def get_all_by_livreur(livreur_id):
        return list(ApprovisionnementModel.get_collection().find({
            "livreur_id": ObjectId(livreur_id),
            "deleted_at": {"$exists": False}
        }))
    
    @staticmethod
    def get_all_by_manager(admin_id):
        return list(ApprovisionnementModel.get_collection().find({
            "admin_id" : ObjectId(admin_id),
            "deleted_at": {"$exists": False}
        }))

    @staticmethod
    def get_by_id(approvisionnement_id):
        return ApprovisionnementModel.get_collection().find_one({
            "_id": ObjectId(approvisionnement_id)
        })

    @staticmethod
    def get_all_by_magasin(magasin_id):
        """Approvisionnements actifs d'un magasin, quel que soit leur auteur."""
        return list(ApprovisionnementModel.get_collection().find({
            "magasin_id": ObjectId(str(magasin_id)),
            "deleted_at": {"$exists": False}
        }))

    @staticmethod
    def get_corbeille_by_magasin(magasin_id):
        return list(ApprovisionnementModel.get_collection().find({
            "magasin_id": ObjectId(str(magasin_id)),
            "deleted_at": {"$exists": True}
        }))

    @staticmethod
    def get_for_magasin(approvisionnement_id, magasin_id, in_corbeille=None):
        """Retourne l'approvisionnement seulement s'il appartient au magasin.

        :param in_corbeille: None = actif ou supprimé, False = actif uniquement,
                             True = à la corbeille uniquement.
        """
        if not ObjectId.is_valid(str(approvisionnement_id)) or not magasin_id:
            return None
        query = {
            "_id": ObjectId(str(approvisionnement_id)),
            "magasin_id": ObjectId(str(magasin_id)),
        }
        if in_corbeille is not None:
            query["deleted_at"] = {"$exists": in_corbeille}
        return ApprovisionnementModel.get_collection().find_one(query)

    @staticmethod
    def get_corbeille_approvisionnements(admin_id):
        return list(ApprovisionnementModel.get_collection().find({
            "admin_id": ObjectId(admin_id),
            "deleted_at": {"$exists": True}
        }))

    @staticmethod
    def delete_permanently(approvisionnement_id):
        return ApprovisionnementModel.get_collection().delete_one({"_id": ObjectId(approvisionnement_id)})


