from extensions import db 
from bson import ObjectId
from datetime import datetime
from utils.inject_magasin_id import inject_magasin_id

class ClientModel:
     
    @staticmethod
    def get_collection():
        from extensions import db
        return db.tables.clients
    
    
    @staticmethod
    def create_client(data):
        """
        Crée un nouveau client avec les informations fournies.

        :param data: dict contenant 'nom', 'telephone', 'email', 'ville', 'quartier', 'adresse', 'password'
        :return: dict contenant le message de succès et l'ID du client créé
        """
        try:
            data = inject_magasin_id(data)
        except Exception as e:
            return {"error": str(e)}, 401

        client = {
            "nom": data["nom"],
            "created_by": data.get("created_by", None),
            "telephone": data.get("telephone", ""),
            "email": data.get("email", ""),
            "ville": data.get("ville", ""),
            "quartier": data.get("quartier", ""),
            "pays": (data.get("pays") or "").upper() or None,  # code ISO du pays du client
            "photo_profil": data.get("photo_profil", None),
            "magasin_id": ObjectId(data["magasin_id"]),
            "montant_en_caisse": float(data.get("montant_en_caisse", 0) or 0),
            "created_at": datetime.utcnow()
        }
        
        result = ClientModel.get_collection().insert_one(client)
        
        return {
            "message": "Client créé avec succès",
            "client_id": str(result.inserted_id)
        }

    @staticmethod
    def get_client_by_id(client_id):
        """
        Récupère un client par son ID.
        """
        client = ClientModel.get_collection().find_one({"_id": ObjectId(client_id), "deleted_at": {"$exists": False}})
        if client:
            client["_id"] = str(client["_id"])
        return client

    @staticmethod
    def get_all_clients_by_magasin(magasin_id=None):
        """
        Récupère tous les clients d'un magasin donné.
        """
        if magasin_id is None:
            try:
                data = inject_magasin_id({})
                magasin_id = data["magasin_id"]
            except Exception as e:
                return {"error": str(e)}, 401

        clients = ClientModel.get_collection().find({"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}})
        return list(clients)
    

    @staticmethod
    def get_clients(magasin_id=None):
        """
        Récupère tous les clients du magasin donné.
        :return: tuple (dict, status_code)
        """
        try:
            if magasin_id is None:
                data = inject_magasin_id({})
                magasin_id = data["magasin_id"]

            clients = ClientModel.get_collection().find({"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}})
            return {"clients": list(clients)}, 200
        except Exception as e:
            return {"error": str(e)}, 401
        

    @staticmethod
    def update_client(client_id, data):
        """
        Met à jour les informations d'un client existant.
        """
        update_data = {k: data[k] for k in data if k in ["nom", "telephone", "email", "ville", "quartier", "pays", "adresse", "montant_en_caisse"]}
        if update_data.get("pays"):
            update_data["pays"] = str(update_data["pays"]).upper()
        
        if "montant_en_caisse" in update_data:
            update_data["montant_en_caisse"] = float(update_data["montant_en_caisse"] or 0)
        
        result = ClientModel.get_collection().update_one(
            {"_id": ObjectId(client_id), "deleted_at": {"$exists": False}},
            {"$set": update_data}
        )
        
        return {
            "message": "Client mis à jour avec succès",
            "modified_count": result.modified_count
        }

    @staticmethod
    def delete_client(client_id):
        """
        Met un client en corbeille (Soft Delete).
        """
        result = ClientModel.get_collection().update_one(
            {"_id": ObjectId(client_id)},
            {"$set": {"deleted_at": datetime.utcnow()}}
        )

        return {
            "message": "Client mis en corbeille avec succès",
            "deleted_count": result.modified_count
        }

    @staticmethod
    def incrementer_montant_caisse(client_id, montant):
        """
        Incrémente atomiquement le montant en caisse d'un client.
        """
        result = ClientModel.get_collection().update_one(
            {"_id": ObjectId(client_id), "deleted_at": {"$exists": False}},
            {"$inc": {"montant_en_caisse": float(montant)}}
        )
        return result.modified_count > 0

    @staticmethod
    def get_all_clients(magasin_id, sort_order=-1):
        """
        Récupère tous les clients d’un magasin si précisé.
        Sort par défaut par date de création décroissante (-1).
        """
        query = {"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}}
        clients = ClientModel.get_collection().find(query).sort("created_at", sort_order)
        return list(clients)

    @staticmethod
    def get_client_by_phone(telephone, magasin_id=None):
        """
        Récupère un client par téléphone et magasin.
        """
        query = {"telephone": telephone, "deleted_at": {"$exists": False}}
        if magasin_id is None:
            try:
                data = inject_magasin_id({})
                magasin_id = data["magasin_id"]
            except Exception:
                pass
        if magasin_id:
            query["magasin_id"] = ObjectId(magasin_id)

        return ClientModel.get_collection().find_one(query)

    @staticmethod
    def get_clients_by_ids(client_ids, magasin_id=None):
        """
        Récupère les clients dont les IDs sont dans la liste, éventuellement filtrés par magasin.
        """
        object_ids = [ObjectId(cid) for cid in client_ids]
        query = {"_id": {"$in": object_ids}, "deleted_at": {"$exists": False}}
        if magasin_id is None:
            try:
                data = inject_magasin_id({})
                magasin_id = data["magasin_id"]
            except Exception:
                magasin_id = None
        if magasin_id:
            query["magasin_id"] = ObjectId(magasin_id)

        clients = ClientModel.get_collection().find(query)
        return list(clients)

    @staticmethod
    def get_corbeille_clients(magasin_id=None):
        """
        Récupère les clients en corbeille.
        """
        query = {"deleted_at": {"$exists": True}}
        if magasin_id:
            query["magasin_id"] = ObjectId(magasin_id)
        return list(ClientModel.get_collection().find(query).sort("deleted_at", -1))

    @staticmethod
    def get_corbeille_clients_livreur(livreur_id):
        """
        Récupère les clients en corbeille créés par un livreur spécifique.
        """
        query = {"deleted_at": {"$exists": True}, "created_by": ObjectId(livreur_id)}
        return list(ClientModel.get_collection().find(query).sort("deleted_at", -1))

    @staticmethod
    def get_or_create_default_client(magasin_id):
        """
        Récupère ou crée le client par défaut 'Autres' pour un magasin donné.
        """
        if not magasin_id:
            return None
            
        magasin_oid = ObjectId(magasin_id) if isinstance(magasin_id, str) else magasin_id
        
        # Chercher le client 'Autres'
        client = ClientModel.get_collection().find_one({
            "nom": "Autres",
            "magasin_id": magasin_oid,
            "deleted_at": {"$exists": False}
        })
        
        if client:
            return str(client["_id"])
            
        # Créer le client 'Autres' s'il n'existe pas
        new_client = {
            "nom": "Autres",
            "telephone": "00000000",
            "email": "autres@lumistock.com",
            "ville": "N/A",
            "quartier": "N/A",
            "magasin_id": magasin_oid,
            "montant_en_caisse": 0.0,
            "created_at": datetime.utcnow()
        }
        
        result = ClientModel.get_collection().insert_one(new_client)
        return str(result.inserted_id)
