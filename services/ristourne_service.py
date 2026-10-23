from bson import ObjectId
from extensions import db
from models.client_model import ClientModel
from datetime import datetime
from flask_jwt_extended import get_jwt_identity



def get_stats_ristournes():
    """
    Récupère les statistiques des ristournes.
    """
    pipeline = [
        {
            "$group": {
                "_id": "$client_id",
                "total_ristournes": {"$sum": "$ristourne"},
                "total_montant_initial": {"$sum": "$montant_initial"},
                "total_montant_paye": {"$sum": "$montant_paye"},
                "pertes": {"$sum": {"$subtract": ["$montant_initial", "$montant_paye"]}},
                "count": {"$sum": 1}
            }
        }
    ]
    # s'assurer que les données agrégés sont pour le magasin de l'utilisateur connecté
    # récupérer l'ID du magasin de l'utilisateur connecté
    user = db.tables.users.find_one({"_id": ObjectId(get_jwt_identity())})
    if user and "magasin_id" in user:
        pipeline[0]["$match"] = {"magasin_id": ObjectId(user["magasin_id"])}
    else:
        pipeline[0]["$match"] = {"magasin_id": None}
    
    # exécuter l'agrégation
    
    stats = list(db.tables.ristournes.aggregate(pipeline))
    return stats[0] if stats else {
        "total_ristournes": 0,
        "total_montant_initial": 0,
        "total_montant_paye": 0,
        "pertes": 0,
        "count": 0
    }
    
def get_ristournes_par_client(client_id):
    """
    Récupère les ristournes pour un client spécifique.
    """
    cursor = db.tables.ristournes.find({"client_id": ObjectId(client_id)})
    liste = list(cursor)
    total = sum(item["ristourne"] for item in liste)
    return {
        "client_id": client_id,
        "total_ristourne": total,
        "nombre": len(liste),
        "details": liste
    }
    
    



def get_clients_du_manager_service(magasin_id):
    """
    Récupère la liste des clients d'un magasin.
    :param magasin_id: ID du magasin
    :return: tuple (dict, status_code)
    """
    try:
        result, status = ClientModel.get_clients(magasin_id)
        
        if status != 200:
            return result, status

        result["clients"].sort(key=lambda x: x.get("created_at", datetime.min), reverse=True)

        return result, 200
    except Exception as e:
        return {"error": f"Erreur lors de la récupération des clients : {str(e)}"}, 500
