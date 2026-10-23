# models/ristourne_model.py
from datetime import datetime
from bson import ObjectId
from extensions import db
from utils.inject_magasin_id import inject_magasin_id  # Ajouté



class RistourneModel:
    
    @staticmethod
    def get_collection():
        return db.tables.ristournes
    
    
    @staticmethod
    def enregistrer_ristourne(livraison_id, livreur_id, client_id, montant_total, montant_paye, ristourne, motif=None, magasin_id=None):
        """
        Enregistre une ristourne.
        """
        data = {
            "livraison_id": livraison_id,
            "livreur_id": livreur_id,
            "client_id": client_id,
            "montant_initial": montant_total,
            "montant_paye": montant_paye,
            "ristourne": ristourne,
            "motif": motif,
            "magasin_id": magasin_id,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = RistourneModel.get_collection().insert_one(data)
        return str(result.inserted_id)
    
    @staticmethod
    def find_by_id(ristourne_id):
        """
        Trouve une ristourne par son ID.
        """
        return RistourneModel.get_collection().find_one({"_id": ObjectId(ristourne_id)})  
    
    @staticmethod
    def delete_by_id(ristourne_id):
        """
        Supprime une ristourne par son ID.
        """
        result = RistourneModel.get_collection().delete_one({"_id": ObjectId(ristourne_id)})
        return result.deleted_count > 0  
    
    @staticmethod
    def delete_by_livraison_id(livraison_id):
        """
        Supprime la ristourne associée à une livraison.
        """
        result = RistourneModel.get_collection().delete_many({"livraison_id": ObjectId(livraison_id)})
        return result.deleted_count > 0
    
    @staticmethod
    def find_by_livraison_id(livraison_id):
        
        """
        Récupère la ristourne associée à une livraison.
        
        """
        return RistourneModel.get_collection().find_one({"livraison_id": ObjectId(livraison_id)})  

    @staticmethod
    def get_ristournes_by_livreur(livreur_id):
        """
        Récupère les ristournes pour un livreur donné avec pagination.
        """
        return list(RistourneModel.get_collection().find({"livreur_id": ObjectId(livreur_id)}))
    
    @staticmethod
    def get_all_ristournes(magasin_id=None, livreur_id=None):
        """
        Récupère toutes les ristournes, optionnellement filtrées par magasin et livreur.
        """
        if magasin_id is None:
            try:
                data = inject_magasin_id({})
                magasin_id = data["magasin_id"]
            except Exception as e:
                return {"error": str(e)}, 401
        
        query = {"magasin_id": ObjectId(magasin_id)}
        if livreur_id:
            query["livreur_id"] = ObjectId(livreur_id)
            
        return list(RistourneModel.get_collection().find(query))
    
    #collection = db.tables.ristournes
