# services/categorie_services.py

from models.categorie_model import CategorieModel
from flask import jsonify

def create_categorie_service(data):
    result =  CategorieModel.create_categorie(data)
    return result
def get_categorie_details_service(categorie_id):
    categorie = CategorieModel.get_categorie_by_id(categorie_id)
    if not categorie:
        return jsonify({"message": "Catégorie non trouvée"}), 404
    return categorie

def get_categories_by_magasin_service(magasin_id):
    return CategorieModel.get_all_by_magasin(magasin_id)

def update_categorie_service(categorie_id, magasin_id, data):
    """Renvoie (réponse, code HTTP)."""
    if not CategorieModel.get_for_magasin(categorie_id, magasin_id):
        return {"message": "Catégorie non trouvée"}, 404
    nom = data.get("nom")
    if nom is not None:
        nom = str(nom).strip()
        if not nom:
            return {"message": "Le nom de la catégorie ne peut pas être vide."}, 400
        if CategorieModel.nom_pris(nom, magasin_id, sauf_id=categorie_id):
            return {"message": "Une autre catégorie de ce magasin porte déjà ce nom."}, 409
        data = {**data, "nom": nom}
    CategorieModel.update_categorie(categorie_id, magasin_id, data)
    return {"message": "Catégorie mise à jour"}, 200

def delete_categorie_service(categorie_id, magasin_id):
    """Renvoie (réponse, code HTTP)."""
    if not CategorieModel.get_for_magasin(categorie_id, magasin_id):
        return {"message": "Catégorie non trouvée"}, 404
    if CategorieModel.est_utilisee(categorie_id):
        return {"message": "Des articles sont rangés dans cette catégorie : déplacez-les avant de la supprimer."}, 409
    CategorieModel.delete_categorie(categorie_id, magasin_id)
    return {"message": "Catégorie supprimée"}, 200
