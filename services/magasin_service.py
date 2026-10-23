import re
from datetime import datetime
from bson import ObjectId
from flask import current_app, request
from models.magasin_model import MagasinModel, MAX_CAISSIERS_DEFAUT
from models.user_model import UserModel
from utils.email import send_registration_pending_email
from utils.uploads import save_image

from marshmallow.exceptions import ValidationError


STORE_OPTIONAL_FIELDS = (
    "raison_sociale", "forme_juridique", "country_code", "region",
    "adresse_ligne_1", "adresse_ligne_2", "code_postal", "telephone",
    "site_web", "nom_contact", "prenom_contact", "fonction_contact",
    "numero_immatriculation", "numero_fiscal", "devise", "fuseau_horaire",
)


def _valid_magasin_id(magasin_id):
    return isinstance(magasin_id, str) and ObjectId.is_valid(magasin_id)


def _save_logo(logo_file):
    """Save a store logo under a server-generated name, never the user filename."""
    return save_image(logo_file)


def _store_optional_data(data):
    return {field: data[field] for field in STORE_OPTIONAL_FIELDS if data.get(field) is not None}


def _validate_store_phone(country_code, telephone):
    """Validate the E.164 store number; Cameroon uses exactly 9 local digits."""
    if not isinstance(telephone, str) or not re.fullmatch(r"\+[1-9]\d{6,14}", telephone):
        raise ValueError("Le téléphone doit être au format international valide.")
    if country_code == "CM" and not re.fullmatch(r"\+237\d{9}", telephone):
        raise ValueError("Au Cameroun, le téléphone doit commencer par +237 et contenir exactement 9 chiffres.")

def validate_licence(licence):
    if "type" not in licence or "max_managers" not in licence or "max_livreurs" not in licence:
        raise ValidationError("Le champ licence doit contenir 'type', 'max_managers' et 'max_livreurs'.")
    
    # Assurance que les valeurs sont valides
    if not isinstance(licence["type"], str):
        raise ValidationError("Le type de licence doit être une chaîne de caractères.")
    
    if not isinstance(licence["max_managers"], int) or licence["max_managers"] < 1:
        raise ValidationError("Le nombre maximal de managers doit être un entier positif.")
    
    if not isinstance(licence["max_livreurs"], int) or licence["max_livreurs"] < 1:
        raise ValidationError("Le nombre maximal de livreurs doit être un entier positif.")

    if "max_caissiers" in licence and (not isinstance(licence["max_caissiers"], int) or licence["max_caissiers"] < 0):
        raise ValidationError("Le nombre maximal de caissiers doit être un entier positif ou nul.")

# Création d'un nouveau magasin
def create_magasin(data, logo_file=None):
    try:
        magasin_data = {
            "denomination": data["denomination"],
            "pays": data["pays"],
            "ville": data["ville"],
            "quartier": data["quartier"],
            "email": data["email"].strip().lower(),
            "activité": data["activité"]
        }
        magasin_data.update(_store_optional_data(data))
        _validate_store_phone(magasin_data.get("country_code"), magasin_data["telephone"])

        licence_data = {
            "nombre_manager": data["nombre_manager"],
            "nombre_livreur": data["nombre_livreur"],
            "nombre_caissier": data.get("nombre_caissier", MAX_CAISSIERS_DEFAUT),
        }

        admin_data = {
            "name": data["admin"]["name"],
            "first_name": data["admin"]["first_name"],
            "email": data["admin"]["email"].strip().lower(),
        }
        for field in ("sexe", "date_naissance", "telephone", "fonction"):
            if data["admin"].get(field) not in (None, ""):
                admin_data[field] = data["admin"][field]
        
        magasin_data["logo"] = _save_logo(logo_file)

        
        # Vérifier si l'email du magasin est déjà utilisé ou des comptes admin
        existing_magasin = MagasinModel.get_magasin_by_email(magasin_data["email"])
        if existing_magasin:
            return {"message": "Un magasin avec cet email existe déjà.", "status": 400}
        if UserModel.find_by_email(admin_data["email"]):
            return {"message": "Un compte admin avec cet email existe déjà.", "status": 400}
        # Créer le magasin et l'admin

        result = MagasinModel.create_magasin(magasin_data, licence_data, admin_data)

        # Aucun mot de passe par email : l'admin reçoit un lien d'invitation.
        from services.user_service import envoyer_invitation
        envoye = envoyer_invitation(result["admin_id"], magasin_data["denomination"])
        message = ("Magasin créé. Un lien d'invitation a été envoyé à l'administrateur pour choisir son mot de passe."
                   if envoye else
                   "Magasin créé, mais l'email d'invitation n'a pas pu être envoyé : renvoyez l'invitation depuis la liste des utilisateurs.")
        return {"message": message, "invitation_envoyee": envoye, "magasin_id": result["magasin_id"],
                "admin_id": result["admin_id"], "status": 201}

    except KeyError as e:
        return {"message": f"Champ manquant : {str(e)}", "status": 400}
    except ValueError as e:
        return {"message": str(e), "status": 400}
    except Exception as e:
        return {"message": f"Erreur serveur : {str(e)}", "status": 500}

# Service pour l'auto-inscription publique
def register_magasin_service(data, logo_file=None):
    try:
        magasin_data = {
            "denomination": data["denomination"],
            "pays": data["pays"],
            "ville": data["ville"],
            "quartier": data["quartier"],
            "email": data["email"].strip().lower(),
            "activité": data["activité"]
        }
        magasin_data.update(_store_optional_data(data))
        _validate_store_phone(magasin_data.get("country_code"), magasin_data["telephone"])

        # Licence d'essai : 2 managers, 2 gérants et 2 caissiers (le staff
        # l'ajuste ensuite si besoin).
        licence_data = {
            "nombre_manager": 2,
            "nombre_livreur": 2,
            "nombre_caissier": MAX_CAISSIERS_DEFAUT,
        }

        admin_data = {
            "name": data["admin_name"].strip(),
            "first_name": data["admin_first_name"].strip(),
            "email": data["admin_email"].strip().lower(),
        }
        # Profil facultatif de l'admin, validé comme partout ailleurs.
        from services.profil_utilisateur import nettoyer_profil
        profil = {cle: data.get(f"admin_{cle}") for cle in ("telephone", "sexe", "date_naissance", "fonction")
                  if data.get(f"admin_{cle}") not in (None, "")}
        admin_data.update(nettoyer_profil(profil, "admin"))
        
        magasin_data["logo"] = _save_logo(logo_file)

        # Vérifier si l'email du magasin ou de l'admin existe
        if MagasinModel.get_magasin_by_email(magasin_data["email"]):
            return {"message": "Un magasin avec cet email existe déjà.", "status": 400}
        
        if UserModel.find_by_email(admin_data["email"]):
            return {"message": "Un compte utilisateur avec cet email existe déjà.", "status": 400}

        # Créer le magasin et l'admin en mode INACTIF
        result = MagasinModel.create_magasin(magasin_data, licence_data, admin_data, is_active=False)
        
        # Envoyer l'email de confirmation d'inscription (en attente)
        send_registration_pending_email(admin_data["email"], magasin_data["denomination"])
        
        return {
            "message": "Inscription réussie ! Votre magasin est en attente de validation par le staff.",
            "magasin_id": result["magasin_id"],
            "status": 201
        }

    except ValueError as e:
        return {"message": str(e), "status": 400}
    except Exception as e:
        return {"message": f"Erreur lors de l'inscription : {str(e)}", "status": 500}


# activer un magasin ou deasctiver un magasin
def toggle_magasin_status_services(magasin_id):
    """
    Active ou désactive un magasin en fonction de son ID.
    
    :param magasin_id: ID du magasin à activer ou désactiver
    :return: Dictionnaire contenant le message et le statut HTTP
    """
    # Compatibilité avec les anciens appels : toute l'application passe par
    # la logique canonique basée sur `is_active`.
    return toggle_magasin_status_service(magasin_id)


#Activer ou désactiver un magasin  
def toggle_magasin_status_service(magasin_id):
    """
    Active ou désactive un magasin selon son état actuel.

    :param magasin_id: ID du magasin à modifier
    :return: Dictionnaire avec message et code HTTP
    """
    try:
        if not _valid_magasin_id(magasin_id):
            return {"message": "ID de magasin invalide.", "status": 400}
        magasin = MagasinModel.get_magasin_by_id(magasin_id)
        if not magasin:
            return {"message": "Magasin introuvable.", "status": 404}

        current_status = magasin.get("is_active", False)
        new_status = not current_status
        changes = {"is_active": new_status}
        message = f"Magasin {'activé' if new_status else 'désactivé'} avec succès."

        # Un magasin issu de l'inscription publique est créé inactif, avec un
        # admin inactif et un mot de passe inconnu. À sa PREMIÈRE validation,
        # l'admin est activé et reçoit un lien d'invitation pour choisir son
        # mot de passe (aucun mot de passe n'est envoyé par email). Une simple
        # réactivation ne touche jamais au mot de passe de l'admin.
        if new_status and not magasin.get("created_by_staff", False) and not magasin.get("first_activated_at"):
            admin_user = UserModel.get_collection().find_one({
                "magasin_id": ObjectId(magasin_id),
                "role": "admin"
            })

            if admin_user and not admin_user.get("is_active", False):
                UserModel.get_collection().update_one(
                    {"_id": admin_user["_id"]},
                    {"$set": {"is_active": True}}
                )

                from services.user_service import envoyer_invitation
                sent = envoyer_invitation(str(admin_user["_id"]), magasin.get("denomination"))
                if sent:
                    message += " Un lien d'invitation a été envoyé à l'administrateur."
                else:
                    message += (
                        " Attention : l'email d'invitation n'a pas pu être envoyé ;"
                        " renvoyez l'invitation depuis la liste des utilisateurs."
                    )
            changes["first_activated_at"] = datetime.utcnow()

        MagasinModel.get_collection().update_one(
            {"_id": ObjectId(magasin_id)},
            {"$set": changes}
        )

        return {"message": message, "status": 200}

    except Exception as e:
        return {
            "message": f"Erreur lors de l'activation/désactivation du magasin : {str(e)}",
            "status": 500
        }


# Récupérer tous les magasins

def get_all_magasin_service(include_archived=False):
    """
    Récupère tous les magasins.
    
    :return: Liste de dictionnaires contenant les informations de chaque magasin
    """
    try:
        magasins = MagasinModel.get_all_magasin(include_archived=include_archived)
        # Ajouter les logos si présents
        for magasin in magasins:
            if magasin.get("logo"):
                magasin["logo"] = f"{request.host_url}uploads/images/{magasin['logo']}" 
        
        return {"magasins": magasins, "status": 200}
    except Exception as e:
        return {"message": f"Erreur lors de la récupération des magasins : {str(e)}", "status": 500}
    
    
# Supprimer un magasin
def delete_magasin_service(magasin_id):
    """
    Supprime un magasin par son ID.
    
    :param magasin_id: ID du magasin à supprimer
    :return: Dictionnaire contenant le message et le statut HTTP
    
    """
    if not _valid_magasin_id(magasin_id):
        return {"message": "ID de magasin invalide.", "status": 400}
    magasin = MagasinModel.get_magasin_by_id(magasin_id)
    if not magasin:
        return {"message": "Magasin non trouvé.", "status": 404}
    
    try:
        result = MagasinModel.delete_magasin(magasin_id)
        return {"message": result["message"], "status": 200}
    except Exception as e:
        return {"message": f"Erreur lors de la suppression du magasin : {str(e)}", "status": 500}


def delete_magasin_definitively_service(magasin_id):
    """Supprime définitivement un magasin uniquement s'il est archivé."""
    if not _valid_magasin_id(magasin_id):
        return {"message": "ID de magasin invalide.", "status": 400}

    try:
        result = MagasinModel.delete_magasin_definitively(magasin_id)
        if result["message"] == "Magasin archivé non trouvé.":
            return {"message": result["message"], "status": 404}
        return {"message": result["message"], "status": 200}
    except Exception as e:
        return {"message": f"Erreur lors de la suppression définitive du magasin : {str(e)}", "status": 500}


# Mettre à jour un magasin
def update_magasin_service(magasin_id, data, user_role, logo_file=None):
    """
    Met à jour les informations d'un magasin selon le rôle de l'utilisateur.
    - Admin : ne peut pas modifier la licence
    - Staff : peut tout modifier

    :param magasin_id: ID du magasin
    :param update_data: données à mettre à jour
    :param user_role: rôle de l'utilisateur (admin ou staff)
    :return: message de succès ou d'erreur
    """
    if not _valid_magasin_id(magasin_id):
        return {"message": "ID de magasin invalide.", "status": 400}
    current_store = MagasinModel.get_magasin_by_id(magasin_id)
    if not current_store:
        return {"message": "Magasin non trouvé.", "status": 404}

    # Seul le staff peut modifier les limites de licence.
    if user_role != "staff":
        data.pop("licence", None)
  
    if data.get("email"):
        data["email"] = data["email"].strip().lower()
        existing = MagasinModel.get_magasin_by_email(data["email"])
        if existing and existing["_id"] != magasin_id:
            return {"message": "Un magasin avec cet email existe déjà.", "status": 409}

    try:
        if data.get("telephone"):
            _validate_store_phone(
                data.get("country_code", current_store.get("country_code")),
                data["telephone"],
            )
        elif "country_code" in data and current_store.get("telephone"):
            _validate_store_phone(data["country_code"], current_store["telephone"])
    except ValueError as error:
        return {"message": str(error), "errors": {"telephone": [str(error)]}, "status": 400}

    if logo_file:
        try:
            data["logo"] = _save_logo(logo_file)
        except ValueError as error:
            return {"message": str(error), "status": 400}

    try:
        result = MagasinModel.update_magasin(magasin_id, data)
        return {"message": result["message"], "status": 200}
    except Exception as e:
        return {"message": f"Erreur lors de la mise à jour du magasin : {str(e)}", "status": 500}
    
# Récupère un magasin par son ID

def get_magasin_service(magasin_id, include_archived=False):
    """
    Récupère un magasin par son ID.
    
    :param magasin_id: ID du magasin à récupérer
    :return: Dictionnaire contenant les informations du magasin ou un message d'erreur
    """
    try:
        if not _valid_magasin_id(magasin_id):
            return {"message": "ID de magasin invalide.", "status": 400}
        magasin = MagasinModel.get_magasin_by_id(magasin_id, include_archived=include_archived)
        if not magasin:
            return {"message": "Magasin non trouvé.", "status": 404}
        magasin['logo'] = f"{request.host_url}uploads/images/{magasin['logo']}" if magasin.get("logo") else None
        return {"magasin": magasin, "status": 200}
    except Exception as e:
        return {"message": f"Erreur lors de la récupération du magasin : {str(e)}", "status": 500}
    
    
