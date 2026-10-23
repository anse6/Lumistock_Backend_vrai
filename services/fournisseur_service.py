from bson import ObjectId
from models.produit_model import ProduitModel
from models.fournisseur_model import FournisseurModel

def get_top_suppliers(magasin_id):
    """
    Récupère les 5 meilleurs fournisseurs d'un magasin basés sur la valeur totale 
    des produits qu'ils fournissent (prix * quantité).
    """
    pipeline = [
        {
            "$match": {
                "magasin_id": ObjectId(magasin_id)
            }
        },
        {
            "$group": {
                "_id": "$fournisseur_id",
                "total_valeur": {
                    "$sum": { "$multiply": ["$prix", "$quantite"] }
                },
                "nombre_produits": { "$sum": 1 },
                "quantite_totale": { "$sum": "$quantite" }
            }
        },
        {
            "$sort": { "total_valeur": -1 }
        },
        {
            "$limit": 5
        },
        {
            "$lookup": {
                "from": "fournisseurs",
                "localField": "_id",
                "foreignField": "_id",
                "as": "fournisseur_info"
            }
        },
        {
            "$unwind": "$fournisseur_info"
        },
        {
            "$project": {
                "_id": 0,
                "fournisseur_id": { "$toString": "$_id" },
                "nom": "$fournisseur_info.nom",
                "ville": "$fournisseur_info.ville",
                "telephone": "$fournisseur_info.telephone",
                "total_valeur": 1,
                "nombre_produits": 1,
                "quantite_totale": 1
            }
        }
    ]
    
    results = list(ProduitModel.get_collection().aggregate(pipeline))
    return results
