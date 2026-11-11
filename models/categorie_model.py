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
    def get_for_magasin(categorie_id, magasin_id):
        """Catégorie du magasin donné, ou None (identifiant invalide ou autre magasin)."""
        if not ObjectId.is_valid(str(categorie_id)) or not ObjectId.is_valid(str(magasin_id)):
            return None
        return CategorieModel.get_collection().find_one({
            "_id": ObjectId(str(categorie_id)),
            "magasin_id": ObjectId(str(magasin_id)),
        })

    @staticmethod
    def nom_pris(nom, magasin_id, sauf_id=None):
        """Une autre catégorie du magasin porte-t-elle déjà ce nom ?"""
        existante = CategorieModel.get_collection().find_one({"nom": nom, "magasin_id": ObjectId(str(magasin_id))})
        return bool(existante) and str(existante["_id"]) != str(sauf_id)

    @staticmethod
    def update_categorie(categorie_id, magasin_id, data):
        """
        Met à jour une catégorie du magasin.

        :param data: dict des champs autorisés (nom, description)
        :return: bool indiquant si la catégorie a été trouvée
        """
        changements = {k: data[k] for k in ("nom", "description") if k in data}
        changements["updated_at"] = datetime.utcnow()
        result = CategorieModel.get_collection().update_one(
            {"_id": ObjectId(str(categorie_id)), "magasin_id": ObjectId(str(magasin_id))},
            {"$set": changements}
        )
        return result.matched_count > 0

    @staticmethod
    def est_utilisee(categorie_id):
        """Des produits (corbeille comprise) sont-ils rangés dans cette catégorie ?"""
        return bool(db.tables.produits.find_one({"categorie_id": ObjectId(str(categorie_id))}, {"_id": 1}))

    @staticmethod
    def delete_categorie(categorie_id, magasin_id):
        """
        Supprime une catégorie du magasin.

        :return: bool indiquant si la suppression a réussi
        """
        result = CategorieModel.get_collection().delete_one(
            {"_id": ObjectId(str(categorie_id)), "magasin_id": ObjectId(str(magasin_id))})
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
