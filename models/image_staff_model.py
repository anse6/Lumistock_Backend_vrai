from extensions import db
from bson import ObjectId
from datetime import datetime



class ImageStaffModel:
    @staticmethod
    def get_collection():
        from extensions import db
        return db.tables.images_staff
    
    
    @staticmethod
    def ajouter_image(data):
        data["created_at"] = datetime.utcnow()
        data["updated_at"] = datetime.utcnow()
        result = ImageStaffModel.get_collection().insert_one(data)
        return str(result.inserted_id)
    
    
    @staticmethod
    def get_all_images():
        """
        Récupère toutes les images de staff.
        """
        return list(ImageStaffModel.get_collection().find())  
    
    @staticmethod
    def get_image_by_critere(nom=None, categorie=None, description=None, tag=None):
        filtre = {}
        
        if nom:
            filtre["nom"] = {"$regex": nom, "$options": "i"}  # Recherche insensible à la casse
        
        if categorie:
            filtre["categorie"] = {"$regex": categorie, "$options": "i"}
        if description:
            filtre["description"] = {"$regex": description, "$options": "i"}
            
        if tag:
            filtre["tags"] = {"$in": [tag]}

        return list(ImageStaffModel.get_collection().find(filtre))


    @staticmethod
    def supprimer_image(image_id):
        """
        Supprime une image de staff par son ID.
        """
        if not ObjectId.is_valid(image_id):
            return {"error": "ID non valide."}, 400

        result = ImageStaffModel.get_collection().delete_one({"_id": ObjectId(image_id)})

        if result.deleted_count == 0:
            return {"error": "Image non trouvée."}, 404
        
        return {"message": "Image supprimée avec succès."}, 200

    @staticmethod
    def modifier_image(image_id, data):
        """Met à jour les métadonnées et le fichier d'une image de staff."""
        if not ObjectId.is_valid(image_id):
            return {"error": "ID non valide."}, 400

        update = {
            key: value.strip() if isinstance(value, str) else value
            for key, value in data.items()
            if key in {"nom", "description", "filename"} and value is not None
        }
        if not update.get("nom"):
            return {"error": "Le nom de l'image est obligatoire."}, 400

        update["updated_at"] = datetime.utcnow()
        result = ImageStaffModel.get_collection().update_one(
            {"_id": ObjectId(image_id)},
            {"$set": update},
        )
        if result.matched_count == 0:
            return {"error": "Image non trouvée."}, 404

        return {"message": "Image modifiée avec succès."}, 200
    
    
    #collection = db.tables.images_staff
