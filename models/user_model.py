# models/user_model.py

import secrets
from datetime import datetime, timedelta
from bson import ObjectId
from flask_bcrypt import Bcrypt
from utils.roles import LOGISTICS_ROLES


bcrypt = Bcrypt()

# Verrouillage du compte après trop de mots de passe erronés.
MAX_ECHECS_CONNEXION = 5
DUREE_VERROUILLAGE = timedelta(minutes=15)


class UserModel:
    
    @staticmethod
    def get_collection():
        from extensions import db
        return db.tables.users

    @staticmethod
    def id_valide(user_id):
        return user_id is not None and ObjectId.is_valid(str(user_id))

    @staticmethod
    def hash_password(password):
        return bcrypt.generate_password_hash(password).decode("utf-8")

    @staticmethod
    def mot_de_passe_inutilisable():
        """Hash d'un secret aléatoire jeté aussitôt : personne ne connaît ce
        mot de passe. Un compte invité le garde jusqu'à ce que son titulaire
        choisisse le sien via le lien d'invitation."""
        return UserModel.hash_password(secrets.token_urlsafe(32))

    @staticmethod
    def check_password(user, password):
        """Compare un mot de passe au hash du compte, sans jamais lever
        d'exception sur un hash ancien ou invalide."""
        hashed = (user or {}).get("password")
        if not hashed or not password:
            return False
        try:
            return bcrypt.check_password_hash(hashed, password)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def set_password(user_id, password):
        """Change le mot de passe et invalide tous les liens et toutes les
        sessions déjà ouvertes du compte."""
        result = UserModel.get_collection().update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "password": UserModel.hash_password(password),
                    "password_changed_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                    "failed_logins": 0,
                    "locked_until": None,
                },
                "$inc": {"token_version": 1},
            }
        )
        return result.matched_count > 0

    @staticmethod
    def invalider_sessions(user_id):
        """Déconnecte le compte partout : tokens de connexion et liens email
        émis jusqu'ici deviennent invalides."""
        result = UserModel.get_collection().update_one(
            {"_id": ObjectId(user_id)}, {"$inc": {"token_version": 1}}
        )
        return result.matched_count > 0

    @staticmethod
    def est_verrouille(user):
        locked_until = (user or {}).get("locked_until")
        return bool(locked_until) and locked_until > datetime.utcnow()

    @staticmethod
    def enregistrer_echec_connexion(user):
        """Compte un mot de passe erroné ; verrouille le compte 15 minutes
        au 5e échec consécutif."""
        echecs = int(user.get("failed_logins", 0) or 0) + 1
        changes = {"failed_logins": echecs}
        if echecs >= MAX_ECHECS_CONNEXION:
            changes = {"failed_logins": 0, "locked_until": datetime.utcnow() + DUREE_VERROUILLAGE}
        UserModel.get_collection().update_one({"_id": user["_id"]}, {"$set": changes})

    @staticmethod
    def reinitialiser_echecs_connexion(user):
        if user.get("failed_logins") or user.get("locked_until"):
            UserModel.get_collection().update_one(
                {"_id": user["_id"]}, {"$set": {"failed_logins": 0, "locked_until": None}}
            )
    

    @staticmethod
    def create_staff(name, first_name, email, password=None):
        """Crée un compte staff. Sans mot de passe, le compte attend que son
        titulaire choisisse le sien via le lien d'invitation."""
        user = {
            "name" : name,
            "first_name": first_name,
            "email": email.lower(),
            "role": "staff",
            "password": UserModel.hash_password(password) if password else UserModel.mot_de_passe_inutilisable(),
            "is_active": True,
            "barcode_mode": False,
            "created_at": datetime.utcnow()
        }
        result = UserModel.get_collection().insert_one(user)
        return str(result.inserted_id)
    
    @staticmethod
    def create_admin(name, first_name, email, password=None, magasin_id=None, role="admin", is_active=True, personal_data=None):
        user = {
            "name" : name,
            "first_name": first_name,
            "email": email.lower(),
            "magasin_id": ObjectId(magasin_id),
            "role": role,
            "password": UserModel.hash_password(password) if password else UserModel.mot_de_passe_inutilisable(),
            "is_active": is_active,
            "barcode_mode": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        for field in ("sexe", "date_naissance", "telephone", "fonction"):
            value = (personal_data or {}).get(field)
            if value not in (None, ""):
                user[field] = value
        result = UserModel.get_collection().insert_one(user)
        return str(result.inserted_id)
    
    
    @staticmethod
    def create_manager(name, first_name, email, password=None, magasin_id=None):
        user = {
            "name" : name,
            "first_name": first_name,
            "email": email.lower(),
            "role": "manager",
            "password": UserModel.hash_password(password) if password else UserModel.mot_de_passe_inutilisable(),
            "magasin_id": ObjectId(magasin_id),
            "is_active": True,
            "barcode_mode": False,
            "created_at": datetime.utcnow()
            }
        result = UserModel.get_collection().insert_one(user)
        return str(result.inserted_id)
    
    @staticmethod
    def create_gerant(name, first_name, email, password=None, magasin_id=None, nom_boutique=None):
        user = {
            "name" : name,
            "first_name": first_name,
            "email": email.lower(),
            "role": "gerant",
            "password": UserModel.hash_password(password) if password else UserModel.mot_de_passe_inutilisable(),
            "magasin_id": ObjectId(magasin_id),
            "nom_boutique": nom_boutique,
            "is_active": True,
            "barcode_mode": False,
            "created_at": datetime.utcnow()
        }
        result = UserModel.get_collection().insert_one(user)
        return str(result.inserted_id)

    # Compatibilité interne : les anciens appels créent désormais un gérant.
    create_livreur = create_gerant

    @staticmethod
    def create_caissier(name, first_name, email, password=None, magasin_id=None, telephone=None):
        """Caissier : vend au comptoir depuis le stock du magasin et ne voit que ses ventes."""
        user = {
            "name": name,
            "first_name": first_name,
            "email": email.lower(),
            "role": "caissier",
            "password": UserModel.hash_password(password) if password else UserModel.mot_de_passe_inutilisable(),
            "magasin_id": ObjectId(magasin_id),
            "is_active": True,
            "barcode_mode": True,  # au comptoir, la douchette est l'usage normal
            "created_at": datetime.utcnow(),
        }
        if telephone:
            user["telephone"] = telephone
        result = UserModel.get_collection().insert_one(user)
        return str(result.inserted_id)

    @staticmethod
    def activate_user(user_id, password):
        # bcrypt, comme partout ailleurs : un hash pbkdf2 rendait la connexion impossible.
        result = UserModel.get_collection().update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "password": UserModel.hash_password(password),
                    "is_active": True,
                    "updated_at": datetime.utcnow()
                },
                "$inc": {"token_version": 1},
            }
        )
        return result.modified_count > 0
  
    @staticmethod
    def find_by_email(email):
        return UserModel.get_collection().find_one({"email": email.lower()})

    
    @staticmethod
    def find_by_id(user_id):
        return UserModel.get_collection().find_one({"_id": ObjectId(user_id)})



    
    @staticmethod
    def count_users_by_role(magasin_id, role):
        """
        Compte le nombre d'utilisateurs par rôle dans un magasin donné.
        :param magasin_id: ID du magasin
        :param role: Rôle de l'utilisateur (manager, gerant ou livreur historique)
        :return: Nombre d'utilisateurs avec le rôle spécifié
        """
        if role not in ["manager", "caissier", *LOGISTICS_ROLES]:
            raise ValueError("Rôle inconnu. Utilisez 'manager', 'gerant' ou 'caissier'.")
        if not ObjectId.is_valid(magasin_id):
            raise ValueError("ID de magasin invalide.")
        role_filter = {"$in": LOGISTICS_ROLES} if role in LOGISTICS_ROLES else role
        return UserModel.get_collection().count_documents({"magasin_id": ObjectId(magasin_id), "role": role_filter})
    
    #Mettre à jour le champs avatar ou ajouter un avatar
    @staticmethod
    def update_avatar(user_id, avatar_url):
        result = UserModel.get_collection().update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"avatar": avatar_url}}
        )
        return result.modified_count > 0
   
    # Récupérer les informations d'un utilisateur
    @staticmethod
    def get_user_by_id(user_id):
        if not user_id or user_id == "None":
            return None
        try:
            user = UserModel.get_collection().find_one({"_id": ObjectId(user_id)})
            if user:
                user["_id"] = str(user["_id"])
                user.pop("password", None)  # On ne renvoie jamais le mot de passe
            return user
        except Exception:
            return None
    

    
    @staticmethod
    def update_password(user_id, hashed_password):
        """
        Met à jour le mot de passe d'un utilisateur.
        :param user_id: ID de l'utilisateur
        :param hashed_password: Nouveau mot de passe haché
        :return: True si la mise à jour a réussi, False sinon
        """
        if not ObjectId.is_valid(user_id):
            raise ValueError("ID d'utilisateur invalide.")
        result = UserModel.get_collection().update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"password": hashed_password}}
        )
        return result.modified_count > 0
    
    @staticmethod
    def get_livreurs(magasin_id=None):
        """
        Récupère tous les livreurs.
        :return: Liste de dictionnaires contenant les informations des livreurs
        """
        if magasin_id is None:
            try:
                # Import local ici pour éviter le circular import
                from utils.inject_magasin_id import inject_magasin_id
                data = inject_magasin_id({})
                magasin_id = data["magasin_id"]
            except Exception as e:
                return {"error": str(e)}, 401
        livreurs = UserModel.get_collection().find(
            {"role": {"$in": LOGISTICS_ROLES}, "magasin_id": ObjectId(magasin_id)}
        )
        return [{"id": str(livreur["_id"]), "name": livreur["name"], "first_name": livreur["first_name"]} for livreur in livreurs]
    
    @staticmethod
    def delete_user(user_id):
        """
        Supprime un utilisateur par son ID.
        :param user_id: ID de l'utilisateur à supprimer
        :return: Dictionnaire contenant le message et le statut HTTP
        """
        if not ObjectId.is_valid(user_id):
            return {"message": "ID d'utilisateur invalide.", "status": 400}
        
        result = UserModel.get_collection().delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count == 0:
            return {"message": "Utilisateur non trouvé.", "status": 404}
        
        return {"message": "Utilisateur supprimé avec succès.", "status": 200}
    
    @staticmethod
    def update_user(user_id, data):
        """
        Met à jour les informations d'un utilisateur.
        :param user_id: ID de l'utilisateur à mettre à jour
        :param data: Dictionnaire contenant les nouvelles données de l'utilisateur
        :return: Dictionnaire contenant le message et le statut HTTP
        """
        if not ObjectId.is_valid(user_id):
            return {"message": "ID d'utilisateur invalide.", "status": 400}
        
        result = UserModel.get_collection().update_one(
            {"_id": ObjectId(user_id)},
            {"$set": data}
        )
        
        if result.modified_count == 0:
            return {"message": "Aucune modification effectuée ou utilisateur non trouvé.", "status": 404}
        
        return {"message": "Utilisateur mis à jour avec succès.", "status": 200}
    @staticmethod
    def get_all_users():
        """
        Récupère tous les utilisateurs.
        :return: Liste de dictionnaires contenant les informations de chaque utilisateur
        """
        return list(UserModel.get_collection().find())


    @staticmethod
    def reset_password(user_id, new_password):
        """Remplace le mot de passe (sans valeur par défaut) et ferme les
        sessions ouvertes."""
        return UserModel.set_password(user_id, new_password)
    
    #collection = db.tables.users
