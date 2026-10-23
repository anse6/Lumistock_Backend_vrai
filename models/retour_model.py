# models/retour_model.py

from datetime import datetime
from bson import ObjectId
from extensions import db
from utils.inject_magasin_id import inject_magasin_id  # Ajouté


class RetourLivraisonModel:
    #
    
    @staticmethod
    def get_collection():
        from extensions import db
        return db.tables.retours_livraison
    
    
    @staticmethod
    def enregistrer_retour(data, magasin_id=None):

        retour = {
            "livraison_id": ObjectId(data["livraison_id"]),
            "client_id": ObjectId(data["client_id"]),
            "produits": data["produits"],
            "montant_retour": data["montant_retour"],
            "montant_a_rembourser": data.get("montant_a_rembourser", 0.0),
            "montant_ht": data.get("montant_ht"),
            "montant_tva": data.get("montant_tva"),
            "cout_retour": data.get("cout_retour"),
            "retenue_pourcentage": data.get("retenue_pourcentage", 0.0),
            "frais_retour": data.get("frais_retour", 0.0),
            "motif": data.get("motif", ""),
            "magasin_id": ObjectId(magasin_id) if magasin_id else None,
            "manager_id": ObjectId(data["manager_id"]) if data.get("manager_id") else None,
            "livreur_id": ObjectId(data["livreur_id"]) if data.get("livreur_id") else None,
            "created_at": datetime.utcnow()
        }
        result = RetourLivraisonModel.get_collection().insert_one(retour)
        return str(result.inserted_id)
    
    @staticmethod
    def get_retours_by_livraison(livraison_id):
        return list(RetourLivraisonModel.get_collection().find({"livraison_id": ObjectId(livraison_id)}))

    @staticmethod
    def get_retours_by_livreur(livreur_id):
        return list(RetourLivraisonModel.get_collection().find({"livreur_id": ObjectId(livreur_id)}))
    
    @staticmethod
    def get_all_retours(magasin_id=None, livreur_id=None):
        """
        Récupère tous les retours, optionnellement filtrés par magasin et livreur.
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
            
        return list(RetourLivraisonModel.get_collection().find(query))



    #collection = db.tables.retours_livraison
