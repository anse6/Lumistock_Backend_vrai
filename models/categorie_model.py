# models/categorie_model.py
from datetime import datetime
from bson import ObjectId
from models.magasin_model import MagasinModel
from extensions import db
from rapidfuzz import fuzz
from utils.inject_magasin_id import inject_magasin_id


class CategorieModel:
    
    @staticmethod
    def get_collection():
        from extensions import db
        return db.tables.categories
    

    @staticmethod
    def create_categorie(data):
        """
        Crée une nouvelle catégorie avec les informations fournies.
        
        :param data: dict contenant 'name', 'description', 'magasin_id'
        :return: dict contenant le message de succès et l'ID de la catégorie créée
        """
        
        # s'asser que la catégorie qu'on veut créer n'existe pas déjà
        categorie_existant = CategorieModel.get_collection().find_one({
            "nom": data["nom"],
            "magasin_id": ObjectId(data["magasin_id"])
        })
        if categorie_existant:
            return {
                "error": "Cette catégorie exite déjà existe déjà dans ce magasin"
            }, 409  # 409 Conflict

        magasin = MagasinModel.get_collection().find_one({"_id": ObjectId(data["magasin_id"])})
        if not magasin:
            return {"message": "Magasin non trouvé"}, 404
        categorie = {
            "nom": data["nom"],
            "description": data.get("description", ""),
            "magasin_id": ObjectId(data["magasin_id"]),
            "created_at": datetime.utcnow()
        }
        
        result = CategorieModel.get_collection().insert_one(categorie)
        
        return {
            "message": "Catégorie créée avec succès",
            "categorie_id": str(result.inserted_id)
        }
        
    @staticmethod
    def get_all_by_magasin(magasin_id):
        """
        Récupère toutes les catégories d'un magasin donné.
        
        :param magasin_id: ID du magasin
        :return: Liste de dictionnaires contenant les informations de chaque catégorie
        """
        categories = CategorieModel.get_collection().find({"magasin_id": ObjectId(magasin_id)})
        #return [categorie for categorie in categories]
        return list(categories)
    
    @staticmethod
    def get_categorie_by_id(categorie_id):
        """
        Récupère une catégorie par son ID.
        
        :param categorie_id: ID de la catégorie
        :return: Dictionnaire contenant les informations de la catégorie
        """
        categorie = CategorieModel.get_collection().find_one({"_id": ObjectId(categorie_id)})
        if categorie:
            categorie["_id"] = str(categorie["_id"])
        return categorie
    
    
    @staticmethod
    def get_by_id(categorie_id):
        """
        Récupère une catégorie par son ID.
        
        :param categorie_id: ID de la catégorie
        :return: Dictionnaire contenant les informations de la catégorie
        """
        categorie = CategorieModel.get_collection().find_one({"_id": ObjectId(categorie_id)})
        if categorie:
            categorie["_id"] = str(categorie["_id"])
        return categorie
    
    @staticmethod
    def update_categorie(categorie_id, data):
        """
        Met à jour une catégorie existante.
        
        :param categorie_id: ID de la catégorie à mettre à jour
        :param data: dict contenant les nouvelles informations
        :return: bool indiquant si la mise à jour a réussi
        """
        result = CategorieModel.get_collection().update_one(
            {"_id": ObjectId(categorie_id)},
            {"$set": data}
        )
        return result.modified_count > 0
    
    @staticmethod
    def delete_categorie(categorie_id):
        """
        Supprime une catégorie par son ID.
        
        :param categorie_id: ID de la catégorie à supprimer
        :return: bool indiquant si la suppression a réussi
        """
        result = CategorieModel.get_collection().delete_one({"_id": ObjectId(categorie_id)})
        return result.deleted_count > 0
    
    @staticmethod
    def get_all():
        return list(CategorieModel.get_collection().find({}))
    
    @staticmethod
    def find_best_match(nom, magasin_id, seuil=80):
        """Catégorie la plus proche du nom donné, parmi celles du magasin uniquement."""
        if not nom or not magasin_id:
            return None
        nom = nom.lower()
        categories = CategorieModel.get_collection().find({"magasin_id": ObjectId(str(magasin_id))})
        matches = [
            (fuzz.ratio(nom, cat["nom"].lower()), cat)
            for cat in categories
        ]
        matches = [m for m in matches if m[0] >= seuil]
        if matches:
            best = max(matches, key=lambda match: match[0])
            return str(best[1]["_id"])
        return None

    @staticmethod
    def create_if_not_exists(nom_categorie, magasin_id):
        if not nom_categorie or not magasin_id:
            return None
        nom = nom_categorie.strip()
        for categorie in CategorieModel.get_collection().find({"magasin_id": ObjectId(str(magasin_id))}):
            if categorie.get("nom", "").strip().lower() == nom.lower():
                return str(categorie["_id"])
        # Crée une nouvelle catégorie dans le magasin de l'utilisateur
        nouvelle_categorie = {
            "magasin_id": ObjectId(str(magasin_id)),
            "nom": nom,
            "description": f"Catégorie auto-créée lors d’un import Excel",
            "created_at": datetime.utcnow(),
        }
        result = CategorieModel.get_collection().insert_one(nouvelle_categorie)
        return str(result.inserted_id)
    
    #collection = db.tables.categories
