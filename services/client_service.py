from werkzeug.utils import secure_filename
from flask import current_app
import os
from bson import ObjectId
from models.client_model import ClientModel
from models.livraison_model import LivraisonModel
from models.user_model import UserModel
from utils.re import clean_string
from utils.dates import iso_utc

NOM_CLIENT_PAR_DEFAUT = "Autres"  # client de passage, créé automatiquement
EXTENSIONS_PHOTO = {"png", "jpg", "jpeg", "webp", "gif"}


def normaliser_telephone(telephone):
    """Numéro au format international : « +237690112233 ».

    Les anciens numéros saisis sans indicatif (9 chiffres, ou 237 + 9 chiffres)
    sont camerounais. Le numéro fictif du client de passage reste tel quel.
    """
    brut = str(telephone or "").strip()
    chiffres = "".join(c for c in brut if c.isdigit())
    if not chiffres:
        return None
    if brut.startswith("+"):
        return "+" + chiffres
    if set(chiffres) == {"0"}:
        return chiffres
    if len(chiffres) == 9:
        return "+237" + chiffres
    if len(chiffres) == 12 and chiffres.startswith("237"):
        return "+" + chiffres
    return chiffres


def _client_meme_telephone(telephone, magasin_id, sauf_id=None):
    """Autre client du magasin avec ce numéro (doublon), ou None.

    Les numéros sont comparés sous leur forme internationale : « 690112233 »
    (ancien format) et « +237690112233 » sont le même numéro.
    """
    numero = normaliser_telephone(telephone)
    if not numero or set(numero) == {"0"}:
        return None
    query = {"magasin_id": ObjectId(str(magasin_id)), "deleted_at": {"$exists": False}}
    for client in ClientModel.get_collection().find(query):
        if str(client["_id"]) != str(sauf_id or "") and normaliser_telephone(client.get("telephone")) == numero:
            return client
    return None


def _enregistrer_photo(photo_file):
    """Enregistre la photo sous un nom unique ; (nom, erreur)."""
    import uuid
    extension = secure_filename(photo_file.filename or "").rsplit(".", 1)[-1].lower()
    if extension not in EXTENSIONS_PHOTO:
        return None, "La photo doit être une image (PNG, JPG, JPEG, WEBP ou GIF)"
    nom = f"client_{uuid.uuid4().hex}.{extension}"
    photo_file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], nom))
    return nom, None


def dette_client(client_id, magasin_id):
    """Reste à payer cumulé du client : ventes actives et dette d'ouverture."""
    from services.compte_client_service import dette_ouverture, reste_ouverture

    livraisons = LivraisonModel.get_collection().find({
        "client_id": ObjectId(str(client_id)), "magasin_id": ObjectId(str(magasin_id)),
        "deleted_at": {"$exists": False},
    })
    ventes = sum(max(float(l.get("montant_total", 0) or 0) - float(l.get("montant_paye", 0) or 0), 0)
                 for l in livraisons)
    return round(ventes + reste_ouverture(dette_ouverture(client_id)), 2)


def _ajouter_dettes_ouverture(stats, restes, **defaut):
    """Ajoute à chaque client sa dette d'ouverture (dette d'avant Lumistock).

    Les listes de dettes sont calculées depuis les ventes ; un client qui ne
    doit que sa dette d'ouverture y apparaît aussi.
    """
    for client_id, reste in restes.items():
        entree = stats.setdefault(client_id, {"client_id": client_id, "nombre_livraisons": 0,
                                              "montant_total": 0, "montant_paye": 0, **defaut})
        entree["montant_total"] += reste
        entree["dette_ouverture"] = reste


def create_client_service(data, photo_file=None):
    data["telephone"] = normaliser_telephone(data.get("telephone"))
    try:
        if float(data.get("montant_en_caisse") or 0) < 0:
            return {"message": "Le montant du compte client ne peut pas être négatif"}, 400
    except (TypeError, ValueError):
        return {"message": "Montant du compte client invalide"}, 400

    # Un numéro de téléphone identifie un seul client dans le magasin.
    doublon = _client_meme_telephone(data.get("telephone"), data["magasin_id"])
    if doublon:
        return {"message": f"Ce numéro est déjà utilisé par le client « {doublon.get('nom', '')} »"}, 409

    # La photo n'est enregistrée qu'une fois la demande validée (nom unique,
    # sinon deux clients avec « photo.jpg » se partageaient la même image).
    data["photo_profil"] = None
    if photo_file and photo_file.filename:
        data["photo_profil"], erreur = _enregistrer_photo(photo_file)
        if erreur:
            return {"message": erreur}, 400

    resultat = ClientModel.create_client(data)
    if isinstance(resultat, tuple):  # erreur d'injection du magasin
        return resultat
    depot = float(data.get("montant_en_caisse") or 0)
    if depot > 0:
        tracer_compte_client(resultat["client_id"], depot, data.get("mode_paiement_depot") or "cash",
                             data.get("created_by"), "depot_initial")
    return {"message": "Client créé avec succès", "client_id": resultat["client_id"]}, 201



# Récupérer tous les clients d'un magasin
def get_all_clients_service(magasin_id, sort_order=-1):
    # Si magasin_id est un dict de type {"$oid": "..."} on extrait l'ID
    if isinstance(magasin_id, dict) and "$oid" in magasin_id:
        magasin_id = magasin_id["$oid"]
        
    clients = ClientModel.get_all_clients(magasin_id, sort_order)
    for c in clients:
        c["_id"] = str(c["_id"])
    return clients




def get_client_by_id_service(client_id):
    client = ClientModel.get_client_by_id(client_id)
    if client:
        client["_id"] = str(client["_id"])
    return client



def update_client_service(client, data):
    """Modifie les informations d'un client.

    Le solde du compte client (montant_en_caisse) ne se modifie pas ici :
    il passe par « Ajouter au compte » / « Retirer du compte ».
    """
    data = {k: v for k, v in (data or {}).items() if k != "montant_en_caisse"}
    if "telephone" in data:
        data["telephone"] = normaliser_telephone(data["telephone"])
        doublon = _client_meme_telephone(data["telephone"], client["magasin_id"], sauf_id=client["_id"])
        if doublon:
            return {"message": f"Ce numéro est déjà utilisé par le client « {doublon.get('nom', '')} »"}, 409
    if client.get("nom") == NOM_CLIENT_PAR_DEFAUT and "nom" in data and data["nom"] != NOM_CLIENT_PAR_DEFAUT:
        return {"message": "Le client de passage « Autres » ne peut pas être renommé"}, 400
    ClientModel.update_client(client["_id"], data)
    return {"message": "Client mis à jour avec succès"}, 200


def delete_client_service(client):
    """Met un client à la corbeille, sauf s'il reste de l'argent en jeu."""
    if client.get("nom") == NOM_CLIENT_PAR_DEFAUT:
        return {"message": "Le client de passage « Autres » ne peut pas être supprimé"}, 400
    solde = float(client.get("montant_en_caisse", 0) or 0)
    if solde > 0:
        return {"message": f"Ce client a encore {solde:,.0f} CFA sur son compte : retirez-les avant de le supprimer".replace(",", " ")}, 400
    dette = dette_client(client["_id"], client["magasin_id"])
    if dette > 0:
        return {"message": f"Ce client doit encore {dette:,.0f} CFA : sa dette disparaîtrait des listes s'il était supprimé".replace(",", " ")}, 400
    ClientModel.delete_client(client["_id"])
    return {"message": "Client mis à la corbeille"}, 200

def restaurer_client_service(client_id):
    """
    Restaure un client de la corbeille.
    """
    from bson import ObjectId
    result = ClientModel.get_collection().update_one(
        {"_id": ObjectId(client_id), "deleted_at": {"$exists": True}},
        {"$unset": {"deleted_at": ""}}
    )
    if result.modified_count == 0:
        return {"error": "Client non trouvé dans la corbeille"}, 404
    return {"message": "Client restauré avec succès"}, 200

MODES_COMPTE_CLIENT = ("cash", "om", "momo", "bancaire", "banque", "cheque", "virement", "carte")


def tracer_compte_client(client_id, montant, mode="cash", par=None, nature="depot"):
    """Historique daté des dépôts et retraits du compte client (lu par la comptabilité)."""
    from datetime import datetime
    from bson import ObjectId

    ClientModel.get_collection().update_one({"_id": ObjectId(str(client_id))}, {"$push": {"mouvements_compte": {
        "date": datetime.now(), "montant": round(float(montant), 2), "mode": mode or "cash",
        "nature": nature, "par": str(par) if par else None,
    }}})


def ajouter_montant_caisse_service(client_id, montant, mode="cash", par=None):
    """
    Ajoute un montant au crédit (caisse) du client.
    """
    if isinstance(montant, bool) or not isinstance(montant, (int, float)) or montant <= 0:
        return {"error": "Le montant doit être un nombre positif"}, 400
    if mode not in MODES_COMPTE_CLIENT:
        return {"error": "Moyen de paiement inconnu"}, 400

    success = ClientModel.incrementer_montant_caisse(client_id, montant)
    if not success:
        return {"error": "Client non trouvé"}, 404
    tracer_compte_client(client_id, montant, mode, par, "depot")

    return {"message": f"Le montant de {montant} a été ajouté avec succès au compte du client."}, 200

def retirer_montant_caisse_service(client_id, montant, mode="cash", par=None):
    """
    Retire un montant du crédit (caisse) du client.
    """
    if mode not in MODES_COMPTE_CLIENT:
        return {"error": "Moyen de paiement inconnu"}, 400
    if isinstance(montant, bool) or not isinstance(montant, (int, float)) or montant <= 0:
        return {"error": "Le montant doit être un nombre positif"}, 400
    
    client = ClientModel.get_client_by_id(client_id)
    if not client:
        return {"error": "Client non trouvé"}, 404
        
    solde_actuel = client.get("montant_en_caisse", 0)
    if solde_actuel < montant:
        return {"error": f"Solde insuffisant. Solde actuel: {solde_actuel}"}, 400
        
    success = ClientModel.incrementer_montant_caisse(client_id, -montant)
    if not success:
        return {"error": "Erreur lors du retrait"}, 500
    tracer_compte_client(client_id, -montant, mode, par, "retrait")
        
    return {"message": f"Le montant de {montant} a été retiré avec succès du compte du client."}, 200

# Récupérer tous les clients d'un livreur avec pour chacun le nombre de livraison concernant chaque client
# Et le montant total de chaque livraison pour chaque client

def get_clients_livreur(livreur_id):
    """
    Récupère tous les clients ayant reçu au moins une livraison de ce livreur,
    avec le nombre total de livraisons et le montant cumulé pour chacun.
    """
    # 1. Récupération de toutes les livraisons faites par le livreur
    livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)

    # 2. Agréger les livraisons par client_id
    stats = {}

    for livraison in livraisons:
        client_id = str(livraison["client_id"])  # ID client sous forme de string

        if client_id not in stats:
            stats[client_id] = {
                "client_id": client_id,
                "nombre_livraisons": 0,
                "montant_total": 0,
            }

        stats[client_id]["nombre_livraisons"] += 1
        stats[client_id]["montant_total"] += livraison.get("montant_total", 0)

    # 3. Récupérer les infos clients une seule fois
    client_ids = list(stats.keys())
    clients_info = ClientModel.get_clients_by_ids(client_ids)

    # 4. Fusionner les infos client avec les stats
    for client in clients_info:
        cid = str(client["_id"])
        if cid in stats:
            stats[cid]["nom"] = client.get("nom", "")
            stats[cid]["telephone"] = client.get("telephone", "")
            stats[cid]["email"] = client.get("email", "")
            stats[cid]['quartier'] = client.get("quartier", "")
            stats[cid]['ville'] = client.get("ville", "")
            

    return list(stats.values())



# Fonction statistiques 
# Récupérer les meilleurs clients d'un livreur avec le montant total de chaque livraison pour chaque client

def get_meilleurs_clients_livreur(livreur_id):
    """
    Retourne les meilleurs clients d’un livreur donné,
    triés par montant total décroissant.
    """
    # 1. Récupération de toutes les livraisons faites par le livreur
    livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)

    # 2. Agrégation des données par client
    stats = {}

    for livraison in livraisons:
        client_id = str(livraison["client_id"])

        if client_id not in stats:
            stats[client_id] = {
                "client_id": client_id,
                "nombre_livraisons": 0,
                "montant_total": 0,
            }

        stats[client_id]["nombre_livraisons"] += 1
        stats[client_id]["montant_total"] += livraison.get("montant_total", 0)

    # 3. Récupération des infos clients
    client_ids = list(stats.keys())
    clients_info = ClientModel.get_clients_by_ids(client_ids)

    # 4. Fusionner les infos clients
    for client in clients_info:
        cid = str(client["_id"])
        if cid in stats:
            stats[cid]["nom"] = clean_string(client.get("nom", ""))
            stats[cid]["telephone"] = client.get("telephone", "")
            stats[cid]["email"] = client.get("email", "")
            stats[cid]["quartier"] = client.get("quartier", "")
            stats[cid]["ville"] = client.get("ville", "")

    # 5. Retourner trié selon montant_total (décroissant)
    return sorted(stats.values(), key=lambda x: x["montant_total"], reverse=True)[:5]




def get_meilleurs_clients_total():
    """
    Retourne les meilleurs clients de tous le magasin,
    triés par montant total décroissant.
    """
    # 1. Récupération de toutes les livraisons faites par le livreur
    livraisons = LivraisonModel.get_all_livraisons()

    # 2. Agrégation des données par client
    stats = {}

    for livraison in livraisons:
        client_id = str(livraison["client_id"])

        if client_id not in stats:
            stats[client_id] = {
                "client_id": client_id,
                "nombre_livraisons": 0,
                "montant_total": 0,
            }

        stats[client_id]["nombre_livraisons"] += 1
        stats[client_id]["montant_total"] += livraison.get("montant_total", 0)

    # 3. Récupération des infos clients
    client_ids = list(stats.keys())
    clients_info = ClientModel.get_clients_by_ids(client_ids)

    # 4. Fusionner les infos clients
    for client in clients_info:
        cid = str(client["_id"])
        if cid in stats:
            stats[cid]["nom"] = client.get("nom", "")
            stats[cid]["telephone"] = client.get("telephone", "")
            stats[cid]["email"] = client.get("email", "")
            stats[cid]["quartier"] = client.get("quartier", "")
            stats[cid]["ville"] = client.get("ville", "")

    # 5. Retourner trié selon montant_total (décroissant)
    return sorted(stats.values(), key=lambda x: x["montant_total"], reverse=True)[:5]


# Récupérer tous les clients d'un magasin avec pour chacun le nombre de livraison concernant chaque client
def get_clients_magasins(filters=None, magasin_id=None):
    """Tous les clients du magasin (y compris sans vente), avec leurs chiffres."""
    from utils.inject_magasin_id import inject_magasin_id
    if magasin_id is None:
        magasin_id = inject_magasin_id({})["magasin_id"]
    livraisons = LivraisonModel.get_all_livraisons(magasin_id)

    stats = {}
    for livraison in livraisons:
        client_id = str(livraison["client_id"])
        if client_id not in stats:
            stats[client_id] = {"client_id": client_id, "nombre_livraisons": 0, "montant_total": 0, "dette": 0}
        stats[client_id]["nombre_livraisons"] += 1
        stats[client_id]["montant_total"] += livraison.get("montant_total", 0)
        stats[client_id]["dette"] += max(
            float(livraison.get("montant_total", 0) or 0) - float(livraison.get("montant_paye", 0) or 0), 0)

    from services.compte_client_service import restes_ouverture
    clients_info = ClientModel.get_all_clients(magasin_id)
    for client in clients_info:
        stats.setdefault(str(client["_id"]), {
            "client_id": str(client["_id"]), "nombre_livraisons": 0, "montant_total": 0, "dette": 0})
    for client_id, reste in restes_ouverture(magasin_id).items():
        if client_id in stats:
            stats[client_id]["dette"] += reste
            stats[client_id]["dette_ouverture"] = reste

    created_by_ids = list({
        str(client.get("created_by"))
        for client in clients_info
        if client.get("created_by") is not None
    })
    users = UserModel.get_collection().find(
        {"_id": {"$in": [ObjectId(uid) for uid in created_by_ids]}}
    )
    user_map = {str(user["_id"]): user for user in users}

    result = []
    for client in clients_info:
        cid = str(client["_id"])
        if cid in stats:
            stats[cid].update({
                "nom": clean_string(client.get("nom", "")),
                "telephone": client.get("telephone", ""),
                "email": client.get("email", ""),
                "quartier": client.get("quartier", ""),
                "ville": client.get("ville", ""),
                "pays": client.get("pays"),
                "photo_profil": client.get("photo_profil", None),
                "montant_en_caisse": round(float(client.get("montant_en_caisse", 0) or 0), 2),
                "dette": round(stats[cid]["dette"], 2),
                "dette_ouverture": stats[cid].get("dette_ouverture", 0),
                "par_defaut": client.get("nom") == NOM_CLIENT_PAR_DEFAUT,
                "created_by": str(client.get("created_by") or ""),
                "created_by_name": "",
                "created_by_first_name": "",
                "created_at": iso_utc(client.get("created_at")),
            })

            creator_id = str(client.get("created_by", ""))
            if creator_id and creator_id in user_map:
                stats[cid]["created_by_name"] = user_map[creator_id].get("name", "")
                stats[cid]["created_by_first_name"] = user_map[creator_id].get("first_name", "")

            result.append(stats[cid])

    # Filtres
    if filters:
        def match_filter(c):
            # Champs vides (None) tolérés : ils faisaient planter le filtre.
            for champ in ("nom", "telephone", "quartier", "ville", "created_by_name"):
                if filters.get(champ) and filters[champ].lower() not in (c.get(champ) or "").lower():
                    return False
            return True

        result = list(filter(match_filter, result))

    # Tri par date de création décroissant
    result.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return result


def get_clients_with_debt_by_livreur(livreur_id):
    """
    Récupère tous les clients d'un livreur avec leur dette totale.
    Dette = montant_total - montant_paye (pour toutes les livraisons)
    Ne retourne que les clients avec une dette > 0
    """
    # 1. Récupération de toutes les livraisons du livreur
    livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)

    # 2. Agrégation par client_id
    stats = {}

    for livraison in livraisons:
        client_id = str(livraison["client_id"])

        if client_id not in stats:
            stats[client_id] = {
                "client_id": client_id,
                "nombre_livraisons": 0,
                "montant_total": 0,
                "montant_paye": 0,
            }

        stats[client_id]["nombre_livraisons"] += 1
        stats[client_id]["montant_total"] += livraison.get("montant_total", 0)
        stats[client_id]["montant_paye"] += livraison.get("montant_paye", 0)

    # Dettes d'ouverture saisies par ce gérant
    from services.compte_client_service import restes_ouverture
    livreur = UserModel.get_user_by_id(livreur_id)
    if livreur and livreur.get("magasin_id"):
        _ajouter_dettes_ouverture(stats, restes_ouverture(livreur["magasin_id"], cree_par=livreur_id))

    # 3. Calcul de la dette pour chaque client
    for client_id in stats:
        stats[client_id]["dette"] = stats[client_id]["montant_total"] - stats[client_id]["montant_paye"]

    # 4. Filtrer uniquement les clients avec dette > 0
    clients_with_debt = {cid: data for cid, data in stats.items() if data["dette"] > 0}

    if not clients_with_debt:
        return []

    # 5. Récupérer les infos clients
    client_ids = list(clients_with_debt.keys())
    clients_info = ClientModel.get_clients_by_ids(client_ids)

    # 6. Fusionner les infos clients
    for client in clients_info:
        cid = str(client["_id"])
        if cid in clients_with_debt:
            clients_with_debt[cid]["nom"] = clean_string(client.get("nom", ""))
            clients_with_debt[cid]["telephone"] = client.get("telephone", "")
            clients_with_debt[cid]["email"] = client.get("email", "")
            clients_with_debt[cid]["quartier"] = client.get("quartier", "")
            clients_with_debt[cid]["ville"] = client.get("ville", "")

    # 7. Retourner trié par dette décroissante
    return sorted(clients_with_debt.values(), key=lambda x: x["dette"], reverse=True)


def get_clients_with_debt_by_magasin(magasin_id=None, livreur_id=None):
    """
    Récupère tous les clients d'un magasin avec leur dette totale.
    Dette = montant_total - montant_paye (pour toutes les livraisons)
    Inclut les informations du livreur associé
    Ne retourne que les clients avec une dette > 0
    """
    # 1. Récupération de toutes les livraisons du magasin
    livraisons = LivraisonModel.get_all_livraisons(magasin_id, livreur_id=livreur_id)

    # 2. Agrégation par client_id
    stats = {}
    livreur_ids = set()

    for livraison in livraisons:
        client_id = str(livraison["client_id"])
        livreur_id_val = str(livraison.get("livreur_id", ""))

        if livreur_id_val:
            livreur_ids.add(livreur_id_val)

        if client_id not in stats:
            stats[client_id] = {
                "client_id": client_id,
                "nombre_livraisons": 0,
                "montant_total": 0,
                "montant_paye": 0,
                "livreur_id": livreur_id_val,
            }

        stats[client_id]["nombre_livraisons"] += 1
        stats[client_id]["montant_total"] += livraison.get("montant_total", 0)
        stats[client_id]["montant_paye"] += livraison.get("montant_paye", 0)

    # Dettes d'ouverture (pour un gérant : celles qu'il a saisies)
    from services.compte_client_service import restes_ouverture
    if magasin_id is None:
        from utils.inject_magasin_id import inject_magasin_id
        magasin_id = inject_magasin_id({})["magasin_id"]
    _ajouter_dettes_ouverture(stats, restes_ouverture(magasin_id, cree_par=livreur_id), livreur_id="")

    # 3. Calcul de la dette pour chaque client
    for client_id in stats:
        stats[client_id]["dette"] = stats[client_id]["montant_total"] - stats[client_id]["montant_paye"]

    # 4. Filtrer uniquement les clients avec dette > 0
    clients_with_debt = {cid: data for cid, data in stats.items() if data["dette"] > 0}

    if not clients_with_debt:
        return []

    # 5. Récupérer les infos des livreurs
    livreur_map = {}
    if livreur_ids:
        livreurs = UserModel.get_collection().find(
            {"_id": {"$in": [ObjectId(lid) for lid in livreur_ids]}}
        )
        livreur_map = {str(livreur["_id"]): livreur for livreur in livreurs}

    # 6. Récupérer les infos clients
    client_ids = list(clients_with_debt.keys())
    clients_info = ClientModel.get_clients_by_ids(client_ids)

    # 7. Fusionner les infos clients et livreurs
    for client in clients_info:
        cid = str(client["_id"])
        if cid in clients_with_debt:
            clients_with_debt[cid]["nom"] = clean_string(client.get("nom", ""))
            clients_with_debt[cid]["telephone"] = client.get("telephone", "")
            clients_with_debt[cid]["email"] = client.get("email", "")
            clients_with_debt[cid]["quartier"] = client.get("quartier", "")
            clients_with_debt[cid]["ville"] = client.get("ville", "")

            # Ajouter les infos du livreur
            l_id = clients_with_debt[cid].get("livreur_id", "")
            if l_id and l_id in livreur_map:
                livreur = livreur_map[l_id]
                clients_with_debt[cid]["livreur_nom"] = livreur.get("name", "")
                clients_with_debt[cid]["livreur_prenom"] = livreur.get("first_name", "")
            else:
                clients_with_debt[cid]["livreur_nom"] = ""
                clients_with_debt[cid]["livreur_prenom"] = ""

    # 8. Retourner trié par dette décroissante
    return sorted(clients_with_debt.values(), key=lambda x: x["dette"], reverse=True)


def get_debt_statistics(magasin_id=None, period='day', client_id=None, date_debut=None, date_fin=None, livreur_id=None):
    """
    Récupère les statistiques de dette agrégées par période (jour, semaine, mois, année).
    
    Args:
        magasin_id: ID du magasin (optionnel, sera injecté si None)
        period: Type d'agrégation ('day', 'week', 'month', 'year')
        client_id: ID du client pour filtrer (optionnel)
        date_debut: Date de début au format datetime (optionnel)
        date_fin: Date de fin au format datetime (optionnel)
        livreur_id: ID du livreur pour filtrer (optionnel)
    
    Returns:
        dict: Statistiques avec totaux et détails par période
    """
    from datetime import datetime
    
    # 1. Récupération de toutes les livraisons du magasin
    livraisons = LivraisonModel.get_all_livraisons(magasin_id, livreur_id=livreur_id)
    
    # 2. Filtrer par client si spécifié
    if client_id:
        livraisons = [l for l in livraisons if str(l.get("client_id")) == str(client_id)]
    
    # 3. Filtrer par plage de dates si spécifiée
    if date_debut or date_fin:
        filtered_livraisons = []
        for livraison in livraisons:
            date_livraison = livraison.get("date_livraison")
            if not date_livraison:
                continue
            
            # Convertir en datetime si c'est une string
            if isinstance(date_livraison, str):
                try:
                    date_livraison = datetime.fromisoformat(date_livraison.replace('Z', '+00:00'))
                except:
                    continue
            
            # Appliquer les filtres de date
            if date_debut and date_livraison < date_debut:
                continue
            if date_fin and date_livraison > date_fin:
                continue
            
            filtered_livraisons.append(livraison)
        
        livraisons = filtered_livraisons
    
    # 4. Fonction helper pour obtenir la clé de période
    def get_period_key(date, period_type):
        if isinstance(date, str):
            try:
                date = datetime.fromisoformat(date.replace('Z', '+00:00'))
            except:
                return None
        
        if period_type == 'day':
            return date.strftime('%Y-%m-%d')
        elif period_type == 'week':
            return date.strftime('%Y-W%W')
        elif period_type == 'month':
            return date.strftime('%Y-%m')
        elif period_type == 'year':
            return date.strftime('%Y')
        return None
    
    # 5. Agrégation par période
    stats_by_period = {}
    
    for livraison in livraisons:
        date_livraison = livraison.get("date_livraison")
        if not date_livraison:
            continue
        
        period_key = get_period_key(date_livraison, period)
        if not period_key:
            continue
        
        if period_key not in stats_by_period:
            stats_by_period[period_key] = {
                "period": period_key,
                "montant_total": 0,
                "montant_paye": 0,
                "dette": 0,
                "nombre_livraisons": 0
            }
        
        stats_by_period[period_key]["montant_total"] += livraison.get("montant_total", 0)
        stats_by_period[period_key]["montant_paye"] += livraison.get("montant_paye", 0)
        stats_by_period[period_key]["nombre_livraisons"] += 1

    # Dettes d'ouverture, rangées à leur date d'origine (sinon à leur saisie)
    from extensions import db
    query = {"deleted_at": {"$exists": False}}
    if magasin_id is not None:
        query["magasin_id"] = ObjectId(str(magasin_id))
    if client_id:
        query["client_id"] = ObjectId(str(client_id))
    for d in db.tables.dettes_ouverture.find(query):
        if livreur_id and str(d.get("cree_par")) != str(livreur_id):
            continue
        quand = d.get("date_origine") or d.get("created_at")
        if not quand or (date_debut and quand < date_debut) or (date_fin and quand > date_fin):
            continue
        period_key = get_period_key(quand, period)
        entree = stats_by_period.setdefault(period_key, {
            "period": period_key, "montant_total": 0, "montant_paye": 0, "dette": 0, "nombre_livraisons": 0})
        entree["montant_total"] += float(d.get("montant", 0) or 0)
        entree["montant_paye"] += float(d.get("montant_paye", 0) or 0)
    
    # 6. Calculer la dette pour chaque période
    for period_key in stats_by_period:
        stats_by_period[period_key]["dette"] = (
            stats_by_period[period_key]["montant_total"] - 
            stats_by_period[period_key]["montant_paye"]
        )
    
    # 7. Trier par période (du plus récent au plus ancien)
    statistics = sorted(stats_by_period.values(), key=lambda x: x["period"], reverse=True)
    
    # 8. Calculer les totaux globaux
    total_dette = sum(s["dette"] for s in statistics)
    total_montant = sum(s["montant_total"] for s in statistics)
    total_paye = sum(s["montant_paye"] for s in statistics)
    
    return {
        "statistics": statistics,
        "total_dette": total_dette,
        "total_montant": total_montant,
        "total_paye": total_paye
    }


def get_client_debt_details_service(client_id, magasin_id=None):
    """
    Récupère l'historique détaillé des dettes d'un client.
    Retourne la liste des livraisons non totalement payées avec détails des produits.
    """
    # 1. Récupération de toutes les livraisons du client (filtrées par magasin si applicable)
    livraisons = LivraisonModel.get_all_livraisons(magasin_id)
    livraisons_client = [l for l in livraisons if str(l.get("client_id")) == str(client_id)]

    historique = []
    total_dette = 0.0

    for l in livraisons_client:
        montant_total = l.get("montant_total", 0)
        montant_paye = l.get("montant_paye", 0)
        relicat_dette = montant_total - montant_paye

        # On ne garde que les livraisons non soldées (statut_paiement != 'payé' ou relicat > 0)
        if relicat_dette > 0:
            total_dette += relicat_dette
            
            detail = {
                "livraison_id": str(l["_id"]),
                "date_livraison": str(l.get("date_livraison", "")),
                "produits": l.get("produits", []),
                "montant_total": montant_total,
                "montant_produits": l.get("montant_produits", montant_total),
                "dette_ancienne_incluse": l.get("dette_ancienne", 0),
                "montant_paye": montant_paye,
                "relicat_dette": relicat_dette,
                "livreur_id": str(l.get("livreur_id", "")),
                "methode_paiement": l.get("methode_paiement", "")
            }
            historique.append(detail)

    # Trier l'historique de la plus ancienne à la plus récente
    historique.sort(key=lambda x: x["date_livraison"])

    # Récupérer les infos du client
    client_info = ClientModel.get_client_by_id(client_id)
    client_name = client_info.get("nom", "Inconnu") if client_info else "Inconnu"

    from services.compte_client_service import dette_ouverture, reste_ouverture, serialiser_dette_ouverture
    ouverture = dette_ouverture(client_id)
    total_dette += reste_ouverture(ouverture)

    return {
        "client_id": str(client_id),
        "client_nom": client_name,
        "total_dette": total_dette,
        "dette_ouverture": serialiser_dette_ouverture(ouverture),
        "historique": historique
    }
