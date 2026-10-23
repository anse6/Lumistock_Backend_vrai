from models.livraison_model import LivraisonModel
from models.stock_livreur_model import StockLivreurModel
from models.produit_model import ProduitModel
from models.user_model import UserModel
import secrets
from datetime import datetime
from models.magasin_model import MagasinModel, MAX_CAISSIERS_DEFAUT
from flask import jsonify, current_app
from utils.error_handler import handle_error
from bson import ObjectId


# Aucun mot de passe n'est choisi par l'administrateur ni envoyé par email :
# chaque nouveau compte reçoit un lien d'invitation à usage unique (72 h) pour
# choisir lui-même son mot de passe.
def envoyer_invitation(user_id, magasin_nom=None):
    """Envoie (ou renvoie) le lien d'invitation d'un compte. True si parti."""
    from services.auth_services import envoyer_lien_compte
    user = UserModel.find_by_id(user_id)
    if not user:
        return False
    return envoyer_lien_compte(user, "invitation", magasin_nom)


def message_invitation(libelle, envoye):
    if envoye:
        return (f"Compte {libelle} créé. Un lien d'invitation a été envoyé par email "
                "pour choisir le mot de passe.")
    return (f"Compte {libelle} créé, mais l'email d'invitation n'a pas pu être envoyé. "
            "Renvoyez l'invitation depuis la liste des utilisateurs.")



# Fonction pour la création d'un compte staff
def create_staff_account(name, first_name, email):
    """
    Crée un compte staff (réservé à un staff connecté). Le titulaire choisit
    son mot de passe via le lien d'invitation reçu par email.
    """
    email = (email or "").strip().lower()
    if UserModel.find_by_email(email):
        return handle_error("Un compte avec cet email existe déjà.", 400)

    user_id = UserModel.create_staff(name=name, first_name=first_name, email=email)
    envoye = envoyer_invitation(user_id)

    return jsonify({"message": message_invitation("staff", envoye),
                    "invitation_envoyee": envoye,
                    "user_id": str(user_id),
                    "email": email,
                    "name": name,
                    "first_name": first_name,
                    "role": "staff",
                    "is_active": True
                    }), 201

# Fonction pour la création d'un compte admin
def create_admin_account(name, first_name, email, magasin_id):
    """
    Crée un compte admin pour un magasin et lui envoie un lien d'invitation.
    """
    existing_user = UserModel.find_by_email(email)
    if existing_user:
        return handle_error("Un compte avec cet email existe déjà.", 400)

    user_id = UserModel.create_admin(name=name, first_name=first_name, email=email, magasin_id=magasin_id)
    envoye = envoyer_invitation(user_id)
    return jsonify({"message": message_invitation("admin", envoye), "invitation_envoyee": envoye,
                    "user_id": str(user_id)}), 201



# Activer un compte utilisateur par email
def activate_user_by_email(email):
    """Active un compte (staff). Le titulaire reçoit un lien d'invitation pour
    choisir son mot de passe : le staff ne choisit ni ne connaît ce mot de passe."""
    try:
        user = UserModel.find_by_email(email)
        if not user:
            return {"message": "Utilisateur introuvable", "status": 404}

        if user.get("is_active", False):
            return {"message": "Le compte est déjà activé", "status": 200}

        UserModel.get_collection().update_one(
            {"_id": user["_id"]},
            {"$set": {"is_active": True, "password": UserModel.mot_de_passe_inutilisable()},
             "$inc": {"token_version": 1}}
        )
        envoye = envoyer_invitation(str(user["_id"]))
        message = "Compte activé. Un lien d'invitation a été envoyé par email." if envoye else \
            "Compte activé, mais l'email d'invitation n'a pas pu être envoyé : renvoyez l'invitation."
        return {"message": message, "invitation_envoyee": envoye}

    except Exception as e:
        return {"message": f"Erreur lors de l’activation : {str(e)}", "status": 500}


# Activer un compte utilisateur par id
def activate_user_by_id(user_id):
    try:
        user = UserModel.find_by_id(user_id)
        #print("ID de l'utilisateur :", user_id)
        if not user:
            return {"message": "Utilisateur introuvable", "status": 404}

        if user.get("is_active", True):
            return {"message": "Le compte est déjà activé", "status": 200}

        UserModel.get_collection().update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "is_active": True
                }
            }
        )
    
        return {"message": "Compte activé avec succès", "status": 200}

    except Exception as e:
        return {"message": f"Erreur lors de l’activation : {str(e)}", "status": 500}


# Fonction pour désactiver un compte utilisateur par id
def deactivate_user_by_id(user_id):
    try:
        user = UserModel.find_by_id(user_id)
        #print("ID de l'utilisateur :", user_id)
        if not user:
            return {"message": "Utilisateur introuvable", "status": 404}

        if user.get("is_active", False):
            return {"message": "Le compte est déjà désactivé", "status": 200}

        UserModel.get_collection().update_one(
            {"id": user_id},  # CORRIGÉ ICI
            {
                "$set": {
                    "is_active": False
                }
            }
        )
    
        return {"message": "Compte activé avec succès", "status": 200}

    except Exception as e:
        return {"message": f"Erreur lors de l’activation : {str(e)}", "status": 500}
    


# Fonction pour activer ou désactiver un compte utilisateur

def set_user_activation(user_id, activate=True):
    try:
        if not ObjectId.is_valid(user_id):
            return {"message": "ID utilisateur invalide", "status": 400}

        user = UserModel.find_by_id(user_id)
        if not user:
            return {"message": "Utilisateur introuvable", "status": 404}

        current_status = user.get("is_active", False)

        if current_status == activate:
            status_text = "activé" if activate else "désactivé"
            return {"message": f"Le compte est déjà {status_text}", "status": 200}

        changes = {"$set": {"is_active": activate}}
        if not activate:
            # Ferme immédiatement les sessions et les liens en cours du compte.
            changes["$inc"] = {"token_version": 1}
        UserModel.get_collection().update_one({"_id": ObjectId(user_id)}, changes)

        status_text = "activé" if activate else "désactivé"
        return {"message": f"Compte {status_text} avec succès", "status": 200}

    except Exception as e:
        return {"message": f"Erreur lors de la modification : {str(e)}", "status": 500}


# Vérifie si la limite de rôles pour un magasin est atteinte
def check_role_limit(store_id, role):
    """
    Vérifie si la limite de rôles pour un magasin est atteinte.
    :param store_id: L'ID du magasin.
    :param role: Le rôle de l'utilisateur (manager ou livreur).
    :return: True si la limite n'est pas atteinte, False si elle est atteinte.
    """
    # Récupérer les informations du magasin
    magasin = MagasinModel.get_magasin_by_id(store_id)
    if not magasin:
        raise ValueError("Magasin non trouvé.")
    
    # Vérifier la limite des managers ou livreurs en fonction du rôle
    if role == "manager":
        current_managers_count = UserModel.count_users_by_role(store_id, "manager")
        if current_managers_count >= magasin["licence"]["max_managers"]:
            return False  # Limite atteinte pour les managers
    elif role in ("livreur", "gerant"):
        current_livreurs_count = UserModel.count_users_by_role(store_id, "gerant")
        if current_livreurs_count >= magasin["licence"]["max_livreurs"]:
            return False  # Limite atteinte pour les livreurs
    elif role == "caissier":
        if UserModel.count_users_by_role(store_id, "caissier") >= max_caissiers(magasin):
            return False
    
    return True  # La limite n'est pas atteinte


# Créer un compte livreur
def create_livreur(name, first_name, email, magasin_id, nom_boutique=None):
    """
    Crée un compte gérant et lui envoie un lien d'invitation.
    
    :param name: Nom du gérant
    :param first_name: Prénom du gérant
    :param email: Email du gérant
    :param nom_boutique: Nom de la boutique associée (optionnel)
    :return: Dictionnaire contenant le message et l'ID du gérant créé
    """
    if not check_role_limit(magasin_id, "gerant"):
        return {"error": "Limite de boutique atteinte pour ce magasin."}, 400
    try:
        existing_user = UserModel.find_by_email(email)
        if existing_user:
            return {"message": "Un compte avec cet email existe déjà.", "status": 400}

        user_id = UserModel.create_livreur(
            name=name, 
            first_name=first_name, 
            email=email,
            magasin_id=magasin_id,
            nom_boutique=nom_boutique
        )
        envoye = envoyer_invitation(user_id)
        return {"message": message_invitation("gérant", envoye), "invitation_envoyee": envoye,
                "user_id": str(user_id), "status": 201}
    
    except Exception as e:
        return {"message": f"Erreur lors de la création du compte livreur : {str(e)}", "status": 500}
  
  
  
def max_caissiers(magasin):
    """Caissiers autorisés par la licence (valeur par défaut si le super-admin n'a rien fixé)."""
    valeur = (magasin.get("licence") or {}).get("max_caissiers")
    return int(valeur) if isinstance(valeur, (int, float)) else MAX_CAISSIERS_DEFAUT


def create_caissier(name, first_name, email, magasin_id, telephone=None):
    """Crée un compte caissier et lui envoie un lien d'invitation."""
    magasin = MagasinModel.get_magasin_by_id(magasin_id)
    if not magasin:
        return {"error": "Magasin introuvable."}, 404
    if not check_role_limit(magasin_id, "caissier"):
        limite = max_caissiers(magasin)
        return {"error": f"Limite de caissiers atteinte ({limite} autorisé{'s' if limite > 1 else ''} par votre licence). "
                         "Contactez l'équipe Lumistock pour en ajouter."}, 400
    if UserModel.find_by_email(email):
        return {"error": "Un compte avec cet email existe déjà."}, 400
    try:
        user_id = UserModel.create_caissier(name=name, first_name=first_name, email=email,
                                            magasin_id=magasin_id, telephone=telephone)
    except Exception as e:
        return {"error": f"Erreur lors de la création du compte caissier : {str(e)}"}, 500
    envoye = envoyer_invitation(user_id)
    return {"message": message_invitation("caissier", envoye), "invitation_envoyee": envoye,
            "user_id": str(user_id)}, 201


# créer un compte manager  
def create_manager(name, first_name, email, magasin_id):
    """
    Crée un compte manager et lui envoie un lien d'invitation.
    """
    # Vérification de la non atteinte de la limite de création des managers (licence)
    if not check_role_limit(magasin_id, "manager"):
        return {"error": "Limite de managers atteinte pour ce magasin."}, 400
    try:
        existing_user = UserModel.find_by_email(email)
        if existing_user:
            return {"message": "Un compte avec cet email existe déjà.", "status": 400}

        user_id = UserModel.create_manager(name=name, first_name=first_name, email=email, magasin_id=magasin_id)
        envoye = envoyer_invitation(user_id)
        return {"message": message_invitation("manager", envoye), "invitation_envoyee": envoye,
                "user_id": str(user_id), "status": 201}
    
    except Exception as e:
        return {"message": f"Erreur lors de la création du compte manager : {str(e)}", "status": 500}
    
    
# Fonction pour récupérer la liste des livreurs
def get_livreur_service():
    """
    Récupère la liste des livreurs.
    
    :return: Dictionnaire contenant la liste des livreurs
    """
    try:
        livreurs = UserModel.get_livreurs()
        # classer par ordre de création
        livreurs.sort(key=lambda x: x.get("created_at", datetime.min), reverse=True)
        return {"livreurs": livreurs, "status": 200}
    
    except Exception as e:
        return {"message": f"Erreur lors de la récupération des livreurs : {str(e)}", "status": 500}
    

# Fonction pour supprimer un utilisateur par son ID
def delete_user_service(user_id):
    """
    Supprime un utilisateur par son ID.

    - Si l'utilisateur est un livreur :
        - Vérifie s’il a des livraisons non terminées → refuse suppression
        - Réintègre son stock dans le stock magasin
        - Supprime son stock
    - Supprime ensuite l'utilisateur

    :param user_id: ID de l'utilisateur à supprimer
    :return: Dictionnaire contenant le message et le statut HTTP
    """
    try:
        user = UserModel.get_user_by_id(user_id)
        if not user:
            return {"message": "Utilisateur non trouvé.", "status": 404}

        # Cas spécifique : si c'est un livreur
        if user['role'] in ('livreur', 'gerant'):
            # Ventes pas encore soldées : l'argent dû doit être encaissé d'abord.
            non_soldees = [
                l for l in LivraisonModel.get_collection().find(
                    {"livreur_id": ObjectId(str(user_id)), "deleted_at": {"$exists": False}})
                if float(l.get("montant_total", 0) or 0) - float(l.get("montant_paye", 0) or 0) > 0.004
            ]

            if non_soldees:
                return {
                    "message": f"Impossible de supprimer ce gérant : {len(non_soldees)} de ses ventes ne sont pas encore soldées.",
                    "status": 400
                }

            # Réintégration du stock dans le stock du magasin
            stock_livreur = StockLivreurModel.get_all_stock_livreur(user_id)
            for item in stock_livreur:
                produit = ProduitModel.get_by_id(item["produit_id"])
                if produit:
                    ProduitModel.get_collection().update_one(
                        {"_id": item["produit_id"]},
                        {"$inc": {"quantite": item["quantite"]}}
                    )

        # Un caissier qui a déjà vendu garde son historique : on le désactive.
        if user['role'] == 'caissier':
            from models.caisse_session_model import CaisseSessionModel
            if CaisseSessionModel.session_ouverte(user_id):
                return {"message": "Ce caissier a une caisse ouverte : fermez-la avant de supprimer le compte.",
                        "status": 400}
            if LivraisonModel.get_collection().count_documents({"livreur_id": ObjectId(user_id)}) > 0:
                return {"message": "Ce caissier a déjà enregistré des ventes : désactivez son compte "
                                   "pour garder l'historique au lieu de le supprimer.", "status": 400}

        # Suppression de l'utilisateur
        result = UserModel.delete_user(user_id)
        if result["status"] == 200:
            return {"message": result["message"], "status": 200}
        else:
            return {"message": result["message"], "status": result["status"]}

    except Exception as e:
        return {"message": f"Erreur lors de la suppression de l'utilisateur : {str(e)}", "status": 500}
   
    
# Mettre à jour les informations d'un utilisateur
   
# Champs qu'un formulaire de profil peut modifier. Le rôle, le magasin, le
# statut et le mot de passe ont leurs propres routes : les accepter ici
# permettrait à un admin ou à un manager de se donner le rôle « staff ».
CHAMPS_UTILISATEUR_MODIFIABLES = (
    "name", "first_name", "email", "nom_boutique",
    "telephone", "sexe", "date_naissance", "fonction",
    "pays", "ville", "quartier", "adresse", "piece_identite", "contact_urgence",
    "date_embauche", "notes", "poste_caisse", "boutique",
)


def update_user_service(user_id, data):
    """
    Met à jour les informations d'un utilisateur.
    
    :param user_id: ID de l'utilisateur à mettre à jour
    :param data: Dictionnaire contenant les nouvelles données de l'utilisateur
    :return: Dictionnaire contenant le message et le statut HTTP
    """
    refuses = sorted(set(data) - set(CHAMPS_UTILISATEUR_MODIFIABLES))
    if refuses:
        return {"message": f"Champ(s) non modifiable(s) : {', '.join(refuses)}.", "status": 400}
    if not data:
        return {"message": "Aucune donnée à mettre à jour.", "status": 400}
    # Profil détaillé : validé, et un champ vidé est retiré du compte.
    from services.profil_utilisateur import CHAMPS_PROFIL, ProfilInvalide, nettoyer_profil
    cible = UserModel.find_by_id(user_id) if ObjectId.is_valid(str(user_id)) else None
    try:
        profil = nettoyer_profil(data, (cible or {}).get("role"))
    except ProfilInvalide as e:
        return {"message": str(e), "status": 400}
    a_retirer = [c for c in CHAMPS_PROFIL if c in data and c not in profil]
    for nom in ("name", "first_name"):
        if nom in data and not str(data[nom] or "").strip():
            return {"message": "Le nom et le prénom ne peuvent pas être vides.", "status": 400}
    data = {**{k: v for k, v in data.items() if k not in CHAMPS_PROFIL}, **profil}
    if "email" in data:
        email = str(data["email"] or "").strip().lower()
        if not email:
            return {"message": "L'email ne peut pas être vide.", "status": 400}
        existant = UserModel.find_by_email(email)
        if existant and str(existant["_id"]) != str(user_id):
            return {"message": "Cet email est déjà utilisé par un autre compte.", "status": 409}
        data = {**data, "email": email}
    try:
        if not cible:
            return {"message": "Utilisateur non trouvé.", "status": 404}
        if a_retirer:
            UserModel.get_collection().update_one({"_id": ObjectId(user_id)}, {"$unset": {c: "" for c in a_retirer}})
        # Enregistrer sans rien changer n'est pas une erreur.
        result = UserModel.update_user(user_id, data) if data else {"status": 200}
        if result["status"] == 404 and cible:
            result = {"status": 200}
        if result["status"] == 200:
            return {"message": "Utilisateur mis à jour avec succès.", "status": 200}
        else:
            return {"message": result["message"], "status": result["status"]}
    
    except Exception as e:
        return {"message": f"Erreur lors de la mise à jour de l'utilisateur : {str(e)}", "status": 500}
    
from flask_jwt_extended import get_jwt_identity
from bson import ObjectId

def get_all_users_service():
    """
    Récupère tous les utilisateurs :
    - Si l'utilisateur connecté est 'staff' => retourne tous les utilisateurs.
    - Sinon => retourne uniquement les utilisateurs de son magasin.
    """
    try:
        current_user_id = get_jwt_identity()
        user = UserModel.find_by_id(current_user_id)

        if not user:
            return {"message": "Utilisateur connecté introuvable", "status": 404}

        # Récupération des utilisateurs selon le rôle
        if user["role"] == "staff":
            users = UserModel.get_all_users()
        else:
            magasin_id = user.get("magasin_id")
            if not magasin_id:
                return {"message": "Magasin introuvable pour cet utilisateur", "status": 400}

            users = list(UserModel.get_collection().find({"magasin_id": magasin_id}))

        # Ajouter le nom du magasin à chaque utilisateur sans exposer de
        # données sensibles du magasin. Cela évite au frontend d'afficher
        # uniquement un identifiant technique.
        magasin_ids = [u.get("magasin_id") for u in users if u.get("magasin_id")]
        magasin_names = {}
        if magasin_ids:
            from models.magasin_model import MagasinModel
            magasins = MagasinModel.get_collection().find(
                {"_id": {"$in": magasin_ids}}, {"denomination": 1}
            )
            magasin_names = {
                str(magasin["_id"]): magasin.get("denomination", "")
                for magasin in magasins
            }


        # Conversion des ObjectId et uniformisation des clés
        for u in users:
            # Assurer que "_id" est une string
            if "_id" in u:
                u["_id"] = str(u["_id"])
            # Supprimer l’éventuel champ "id"
            if "id" in u:
                del u["id"]
            # Supprimer le champ "password" pour des raisons de sécurité
            if "password" in u:
                del u["password"]
            # Convertir magasin_id en string si c’est un ObjectId
            if "magasin_id" in u and isinstance(u["magasin_id"], ObjectId):
                u["magasin_id"] = str(u["magasin_id"])
            if u.get("magasin_id"):
                u["magasin_nom"] = magasin_names.get(str(u["magasin_id"]), "")
            if user["role"] == "manager":
                users = [u for u in users if u.get("role") != "admin"]

        # Supprimer le staff de la liste des utilisateurs
        users = [u for u in users if u.get("role") != "staff"]
        # Trier les utilisateurs par date de création
        users.sort(key=lambda x: x.get("created_at", datetime.min), reverse=True)
        # Double authentification : seulement l'indicateur, jamais le secret.
        from services.deux_facteurs_service import actifs_parmi
        avec_2fa = actifs_parmi([u["_id"] for u in users])
        for u in users:
            u["deux_facteurs"] = u["_id"] in avec_2fa

        return {"users": users, "status": 200}

    except Exception as e:
        return {"message": f"Erreur lors de la récupération des utilisateurs : {str(e)}", "status": 500}


def get_user_by_id_service(user_id):
    """
    Récupère un utilisateur par son ID.
    
    :param user_id: ID de l'utilisateur à récupérer
    :return: Dictionnaire contenant les informations de l'utilisateur ou un message d'erreur
    """
    try:
        user = UserModel.get_user_by_id(user_id)
        if not user:
            return {"message": "Utilisateur non trouvé.", "status": 404}
        
        return {"user": user, "status": 200}
    
    except Exception as e:
        return {"message": f"Erreur lors de la récupération de l'utilisateur : {str(e)}", "status": 500}


# Service permettant de reinitialiser le mot de passe d'un utilisateur par l'admin ou le manager
def reset_password_service(user_id, data=None):
    """Réinitialisation demandée par l'administrateur.

    Aucun mot de passe n'est généré ni envoyé : l'ancien mot de passe est
    rendu inutilisable, toutes les sessions du compte sont fermées, et
    l'utilisateur reçoit un lien (30 min) pour choisir un nouveau mot de passe.
    Si l'email ne part pas, la réponse est une erreur (502) : la transaction
    de la requête est annulée et l'ancien mot de passe reste valide.
    """
    from services.auth_services import envoyer_lien_compte

    user = UserModel.find_by_id(user_id)
    if not user:
        return {"error": "Utilisateur non trouvé."}, 404
    email = user.get("email")
    if not email:
        return {"error": "Cet utilisateur n'a pas d'adresse email : impossible de lui envoyer un lien."}, 400
    if not user.get("is_active", False):
        return {"error": "Ce compte est désactivé : réactivez-le avant de réinitialiser son mot de passe."}, 400

    UserModel.reset_password(user_id, secrets.token_urlsafe(32))
    # Le lien porte la nouvelle version de sécurité du compte.
    if not envoyer_lien_compte(UserModel.find_by_id(user_id), "reset"):
        return {
            "error": "L'email n'a pas pu être envoyé : le mot de passe n'a pas été modifié. Réessayez plus tard."
        }, 502

    return {"message": f"Un lien de réinitialisation a été envoyé par email à {email}."}, 200


def renvoyer_invitation_service(user_id):
    """Renvoie le lien d'invitation (compte qui n'a pas encore choisi son mot de passe)."""
    user = UserModel.find_by_id(user_id)
    if not user:
        return {"error": "Utilisateur non trouvé."}, 404
    if not user.get("is_active", False):
        return {"error": "Ce compte est désactivé : réactivez-le avant d'envoyer l'invitation."}, 400
    if user.get("password_changed_at"):
        return {"error": "Ce compte a déjà choisi son mot de passe. Utilisez la réinitialisation."}, 400
    if not envoyer_invitation(user_id):
        return {"error": "L'email d'invitation n'a pas pu être envoyé. Réessayez plus tard."}, 502
    return {"message": f"Invitation renvoyée à {user['email']}."}, 200

def toggle_barcode_mode_service(user_id, mode=None):
    try:
        user = UserModel.find_by_id(user_id)
        if not user:
            return {'message': 'Utilisateur non trouvé', 'status': 404}
        current_mode = user.get('barcode_mode', False)
        new_mode = mode if mode is not None else not current_mode
        UserModel.get_collection().update_one({'_id': ObjectId(user_id)}, {'$set': {'barcode_mode': new_mode}})
        status = "activé" if new_mode else "désactivé"
        return {'message': f'Mode code-barres {status}', 'barcode_mode': new_mode}, 200
    except Exception as e:
        return {'error': str(e)}, 500
