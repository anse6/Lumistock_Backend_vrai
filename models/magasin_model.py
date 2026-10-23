from datetime import datetime
from bson import ObjectId
from extensions import db
from models.user_model import UserModel
from models.approvisionnement_model import ApprovisionnementModel
from models.client_model import ClientModel
from models.produit_model import ProduitModel

# Caissiers autorisés par la licence quand le super-admin n'a rien fixé.
MAX_CAISSIERS_DEFAUT = 2

class MagasinModel:
    @staticmethod
    def _public_user(user):
        """Return the store-member shape that is safe to expose through the API."""
        public_user = dict(user)
        public_user.pop("password", None)
        public_user.pop("reset_token", None)
        public_user.pop("validation_token", None)
        public_user["_id"] = str(public_user["_id"])
        if public_user.get("magasin_id") is not None:
            public_user["magasin_id"] = str(public_user["magasin_id"])
        return public_user

    @staticmethod
    def get_collection():
        from extensions import db
        return db.tables.magasin
    

    @staticmethod
    def create_magasin(magasin_data, licence_data, admin_data, is_active=True):
        """
        Crée un nouveau magasin avec les informations détaillées et un compte admin associé.

        :param magasin_data: dict contenant 'denomination', 'pays', 'ville', 'quartier', 'email', 'activité'
        :param licence_data: dict contenant 'nombre_manager' et 'nombre_livreur'
        :param admin_data: dict contenant 'name', 'first_name' et 'email'
        :param is_active: Booléen indiquant si le magasin et l'admin sont actifs par défaut
        :return: dict contenant les IDs du magasin et de l’admin
        """
        # Création du document magasin
        magasin = {
            "denomination": magasin_data["denomination"],
            "pays": magasin_data["pays"],
            "ville": magasin_data["ville"],
            "quartier": magasin_data["quartier"],
            "email": magasin_data["email"],
            "activité": magasin_data["activité"],
            "logo": magasin_data.get("logo"),

            "licence": {
                "max_managers": int(licence_data["nombre_manager"]),
                "max_livreurs": int(licence_data["nombre_livreur"]),
                "max_caissiers": int(licence_data.get("nombre_caissier", MAX_CAISSIERS_DEFAUT)),
            },
            # Une création faite par le staff est active immédiatement ; une
            # inscription publique est créée inactive.
            "created_by_staff": is_active,
            "is_active" : is_active,
            "created_at": datetime.utcnow()
        }
        for field in (
            "raison_sociale", "forme_juridique", "country_code", "region",
            "adresse_ligne_1", "adresse_ligne_2", "code_postal", "telephone",
            "site_web", "nom_contact", "prenom_contact", "fonction_contact",
            "numero_immatriculation", "numero_fiscal", "devise", "fuseau_horaire",
        ):
            if magasin_data.get(field) is not None:
                magasin[field] = magasin_data[field]

        # Insertion du magasin
        result = MagasinModel.get_collection().insert_one(magasin)
        magasin_id = str(result.inserted_id)

        # Création du compte admin du magasin
        # Mot de passe inconnu de tous : l'admin choisit le sien via le lien
        # d'invitation (envoyé par le service dès que le magasin est actif).
        admin_id = UserModel.create_admin(
            name=admin_data["name"],
            first_name=admin_data["first_name"],
            email=admin_data["email"],
            magasin_id=magasin_id,
            role="admin",
            is_active=is_active,
            personal_data={k: v for k, v in admin_data.items() if k != "password"},
        )

        return {
            "message": "Magasin et compte admin créés avec succès",
            "magasin_id": magasin_id,
            "admin_id": admin_id
        }
        
    # Vérifie si l'email du magasin existe déjà
    @staticmethod
    def get_magasin_by_email(email):
        """
        Récupère un magasin par son email.
        :param email: L'email du magasin.
        :return: Un dictionnaire contenant les informations du magasin.
        """
        magasin = MagasinModel.get_collection().find_one({"email": email})
        if magasin:
            magasin["_id"] = str(magasin["_id"])
        return magasin
    
    # récupère un magasin via l'email de l'administrateur
    # pour vérifier si l'email de l'administrateur existe déjà
    # dans la base de données avant de créer un nouveau magasin
    # et un compte admin associé
    @staticmethod
    def get_admin_by_email(email):
        """
        Récupère un magasin par l'email de l'administrateur.
        :param email: L'email de l'administrateur.
        :return: Un dictionnaire contenant les informations du magasin.
        """
        magasin = MagasinModel.get_collection().find_one({"admin.email": email})
        if magasin:
            magasin["_id"] = str(magasin["_id"])
        return magasin
        
    # Récupère un magasin via son ID
    @staticmethod
    def get_magasin_by_id(magasin_id, include_archived=False):
        """
        Récupère un magasin par son ID.
        :param magasin_id: L'ID du magasin.
        :param include_archived: inclure un magasin archivé (consultation staff uniquement).
        :return: Un dictionnaire contenant les informations du magasin.
        """
        query = {"_id": ObjectId(magasin_id)}
        if not include_archived:
            query["deleted_at"] = {"$exists": False}
        magasin = MagasinModel.get_collection().find_one(query)
        if magasin:
            magasin["_id"] =str( magasin["_id"])  # On transforme l'ObjectId en chaîne pour l'utiliser dans l'API
            magasin["is_archived"] = "deleted_at" in magasin
            users = UserModel.get_collection().find({"magasin_id": ObjectId(magasin_id)})
            user_list = []
            for user in users:
                user_list.append(MagasinModel._public_user(user))
            magasin["utilisateurs"] = user_list
        else:   
            magasin = None
        return magasin
    
    @staticmethod
    def update_magasin(magasin_id, update_data):
        """
        Met à jour les informations d'un magasin.
        :param magasin_id: L'ID du magasin à mettre à jour.
        :param update_data: Un dictionnaire contenant les données à mettre à jour.
        :return: Un dictionnaire contenant le message de succès ou d'erreur.
        """
        result = MagasinModel.get_collection().update_one(
            {"_id": ObjectId(magasin_id)},
            {"$set": update_data}
        )
        
        if result.modified_count > 0:
            return {"message": "Magasin mis à jour avec succès."}
        else:
            return {"message": "Aucune mise à jour effectuée."}
        
    @staticmethod
    def delete_magasin(magasin_id):
        """
        Supprime un magasin par son ID.
        :param magasin_id: L'ID du magasin à supprimer.
        :return: Un dictionnaire contenant le message de succès ou d'erreur.
        """
        # Les livraisons, écritures comptables et stocks constituent un
        # historique : une suppression physique créait des références
        # orphelines. Le magasin est donc archivé, jamais purgé par l'API.
        result = MagasinModel.get_collection().update_one(
            {"_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}},
            {"$set": {"deleted_at": datetime.utcnow(), "is_active": False}},
        )

        if result.modified_count > 0:
            return {"message": "Magasin archivé avec succès."}
        return {"message": "Magasin non trouvé."}

    @staticmethod
    def delete_magasin_definitively(magasin_id):
        """Supprime définitivement un magasin déjà archivé."""
        result = MagasinModel.get_collection().delete_one({
            "_id": ObjectId(magasin_id),
            "deleted_at": {"$exists": True},
        })
        if result.deleted_count > 0:
            return {"message": "Magasin supprimé définitivement avec succès."}
        return {"message": "Magasin archivé non trouvé."}
        
    
    
    
    
     #Retourne tous les magasins de la base de données avec les utilisateurs associés.
    @staticmethod
    def get_all_magasin(include_archived=False):
        """
            Récupère tous les magasins avec les utilisateurs associés.
    :return: Une liste de dictionnaires contenant les informations de chaque magasin.   """
        query = {} if include_archived else {"deleted_at": {"$exists": False}}
        magasins = MagasinModel.get_collection().find(query).sort("created_at", -1)
        result = []
        for magasin in magasins:
            magasin_id = magasin["_id"]
            users = UserModel.get_collection().find({"magasin_id": magasin_id})
            user_list = []
            for user in users:
                user_list.append(MagasinModel._public_user(user))

            magasin["_id"] = str(magasin["_id"])
            magasin["is_archived"] = "deleted_at" in magasin
            magasin["utilisateurs"] = user_list
            result.append(magasin)
        return result    




    @staticmethod
    def toggle_magasin_status(magasin_id):
        """
        Change le statut d'un magasin (actif/inactif).
        :param magasin_id: L'ID du magasin à mettre à jour.
        :return: Un dictionnaire contenant le message de succès ou d'erreur.
        """
        magasin = MagasinModel.get_collection().find_one({
            "_id": ObjectId(magasin_id),
            "deleted_at": {"$exists": False},
        })
        if not magasin:
            return {"message": "Magasin non trouvé."}
        
        new_status = not magasin.get("is_active", False)
        result = MagasinModel.get_collection().update_one(
            {"_id": ObjectId(magasin_id)},
            {"$set": {"is_active": new_status}}
        )
        
        if result.modified_count > 0:
            return {"message": "Statut du magasin mis à jour avec succès."}
        else:
            return {"message": "Aucune mise à jour effectuée."}
        
    #collection = db.tables.magasin
