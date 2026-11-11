from datetime import datetime
from bson import ObjectId
from models.magasin_model import MagasinModel
from extensions import db
from utils.inject_magasin_id import inject_magasin_id

class FournisseurModel:

    @staticmethod
    def get_collection():
        return db.tables.fournisseurs

    @staticmethod
    def create_fournisseur(data):
        """
        Crée un nouveau fournisseur.
        """
        # Vérifier si un fournisseur avec le même nom existe déjà dans ce magasin
        existing = FournisseurModel.get_collection().find_one({
            "nom": data["nom"],
            "magasin_id": ObjectId(data["magasin_id"])
        })
        if existing:
            return {
                "error": "Ce fournisseur existe déjà dans ce magasin"
            }, 409

        magasin = MagasinModel.get_collection().find_one({"_id": ObjectId(data["magasin_id"])})
        if not magasin:
            return {"message": "Magasin non trouvé"}, 404

        fournisseur = {
            "nom": data["nom"],
            "pays": data.get("pays", ""),
            "ville": data.get("ville", ""),
            "telephone": data.get("telephone", ""),
            "email": data.get("email", ""),
            "adresse": data.get("adresse", ""),
            "magasin_id": ObjectId(data["magasin_id"]),
            "created_at": datetime.utcnow()
        }

        result = FournisseurModel.get_collection().insert_one(fournisseur)

        return {
            "message": "Fournisseur créé avec succès",
            "fournisseur_id": str(result.inserted_id)
        }

    @staticmethod
    def get_all_by_magasin(magasin_id):
        """
        Récupère tous les fournisseurs d'un magasin.
        """
        fournisseurs = FournisseurModel.get_collection().find({"magasin_id": ObjectId(magasin_id)})
        return list(fournisseurs)

    @staticmethod
    def get_by_id(fournisseur_id):
        """
        Récupère un fournisseur par son ID.
        """
        if not ObjectId.is_valid(fournisseur_id):
            return None
            
        fournisseur = FournisseurModel.get_collection().find_one({"_id": ObjectId(fournisseur_id)})
        if fournisseur:
            fournisseur["_id"] = str(fournisseur["_id"])
        return fournisseur

    @staticmethod
    def get_for_magasin(fournisseur_id, magasin_id):
        """Fournisseur du magasin donné, ou None (identifiant invalide ou autre magasin)."""
        if not ObjectId.is_valid(str(fournisseur_id)) or not ObjectId.is_valid(str(magasin_id)):
            return None
        return FournisseurModel.get_collection().find_one({
            "_id": ObjectId(str(fournisseur_id)),
            "magasin_id": ObjectId(str(magasin_id)),
        })

    @staticmethod
    def update_fournisseur(fournisseur_id, magasin_id, data):
        """
        Met à jour un fournisseur du magasin.
        """
        update_data = {k: v for k, v in data.items() if k in ["nom", "pays", "ville", "telephone", "email", "adresse"]}
        if not update_data:
            return False
        update_data["updated_at"] = datetime.utcnow()

        result = FournisseurModel.get_collection().update_one(
            {"_id": ObjectId(str(fournisseur_id)), "magasin_id": ObjectId(str(magasin_id))},
            {"$set": update_data}
        )
        return result.matched_count > 0

    @staticmethod
    def est_utilise(fournisseur_id):
        """Le fournisseur figure-t-il sur une facture ou une fiche produit ?

        Ces documents gardent sa référence : le supprimer les rendrait orphelins.
        """
        oid = ObjectId(str(fournisseur_id))
        return bool(
            db.tables.factures_fournisseurs.find_one({"fournisseur_id": oid}, {"_id": 1})
            or db.tables.produits.find_one({"fournisseur_id": oid}, {"_id": 1})
        )

    @staticmethod
    def delete_fournisseur(fournisseur_id, magasin_id):
        """
        Supprime un fournisseur du magasin.
        """
        result = FournisseurModel.get_collection().delete_one(
            {"_id": ObjectId(str(fournisseur_id)), "magasin_id": ObjectId(str(magasin_id))})
        return result.deleted_count > 0
