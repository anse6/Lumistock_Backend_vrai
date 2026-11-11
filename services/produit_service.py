# services/produit_services.py
from utils.uploads import save_image
from services import valorisation_service
from models.produit_model import ProduitModel
from flask import current_app
from models.categorie_model import CategorieModel
from models.livraison_model import LivraisonModel
from bson import ObjectId
from models.produit_model import ProduitModel
from models.user_model import UserModel
from models.client_model import ClientModel
from models.livraison_model import LivraisonModel
from flask import request
from datetime import datetime
from services import numeros_serie_service
from services.numeros_serie_service import ErreurNumeroSerie


def _vrai(valeur):
    return str(valeur).strip().lower() in ("1", "true", "oui", "on", "yes")


def _acteur(admin_id):
    user = UserModel.find_by_id(admin_id) if admin_id else None
    return {"admin_id": admin_id,
            "admin_nom": f"{user.get('first_name', '')} {user.get('name', '')}".strip() if user else None}

# Créer ou enregister un nouveau produit
def create_produit_service(data, image_file):
    # Vérification de l'obligation du code-barre si le mode est activé pour l'utilisateur
    admin_id = data.get("admin_id")
    if admin_id:
        user = UserModel.find_by_id(admin_id)
        if user and user.get("barcode_mode", False) and not data.get("code_barre"):
            return {"error": "Le code-barre est obligatoire lorsque le mode Lecteur est activé."}, 400

    # Contrôles d'unicité AVANT d'enregistrer l'image, pour ne pas laisser de fichier orphelin.
    if ProduitModel.nom_existe(data["magasin_id"], data["nom"]):
        return {"error": "Cet article existe déjà dans ce magasin"}, 409
    if ProduitModel.code_barre_existe(data["magasin_id"], data.get("code_barre")):
        return {"error": "Ce code-barre est déjà utilisé par un autre article du magasin."}, 409

    # Suivi par numéro de série : un numéro par unité du stock initial.
    suivi = _vrai(data.get("suivi_serie"))
    numeros = []
    if suivi:
        if not numeros_serie_service.actif(data["magasin_id"]):
            return {"error": "Activez d'abord les numéros de série dans Paramètres › Stock."}, 400
        if data.get("conditionnements"):
            return {"error": "Un article suivi par numéro de série se vend à l'unité : pas de conditionnement."}, 400
        try:
            numeros = numeros_serie_service.verifier_nouveaux(
                {"nom": data["nom"], "magasin_id": data["magasin_id"]}, data.get("numeros_serie"),
                int(data.get("quantite") or 0))
        except ErreurNumeroSerie as err:
            return {"error": str(err)}, err.statut

    # Conditionnements vérifiés avant d'enregistrer l'image (pas de fichier orphelin).
    from services.conditionnements import ErreurConditionnement, normaliser
    try:
        normaliser(data.get("conditionnements") or [], float(data.get("prix") or 0), float(data.get("prix_achat") or 0))
    except ErreurConditionnement as erreur:
        return {"error": str(erreur)}, 400

    try:
        image_filename = save_image(image_file)
        produit_id = ProduitModel.create_produit(data, image_filename)
    except ValueError as error:
        status = 409 if "existe déjà" in str(error) else 400
        return {"error": str(error)}, status
    if suivi:
        ProduitModel.get_collection().update_one({"_id": ObjectId(produit_id)}, {"$set": {"suivi_serie": True}})
        numeros_serie_service.entrer(ProduitModel.get_by_id(produit_id), numeros, "stock initial",
                                     cout=float(data.get("prix_achat") or 0), acteur=_acteur(data.get("admin_id")),
                                     fournisseur_id=data.get("fournisseur_id"))
    return {"message": "Article créé", "produit_id": produit_id}, 201



# Type de mouvement enregistré par la déclaration d'avarie (route /perime).
TYPE_MOUVEMENT_AVARIE = "produit périmé"


def resume_avaries(mouvements, produit=None):
    """Résume les avaries déclarées d'un produit à partir de son historique.

    :param produit: le produit, pour connaître la part déjà mise en solde.
    :return: None si aucune avarie, sinon quantité totale retirée, nombre de
             déclarations, le détail de la plus récente et ce qui reste à solder.
    """
    avaries = [m for m in mouvements or [] if m.get("type") == TYPE_MOUVEMENT_AVARIE]
    if not avaries:
        return None
    derniere = max(avaries, key=lambda m: m.get("date") or datetime.min)
    date = derniere.get("date")
    mise_en_solde = int((produit or {}).get("quantite_mise_en_solde") or 0)
    return {
        "quantite_totale": sum(abs(int(m.get("quantite") or 0)) for m in avaries),
        "nombre": len(avaries),
        "derniere_date": date.isoformat() if isinstance(date, datetime) else date,
        "dernier_type": derniere.get("type_avarie") or "perime",
        "derniere_description": derniere.get("description"),
        "quantite_mise_en_solde": mise_en_solde,
        "quantite_soldable": max(_avaries_soldables(avaries) - mise_en_solde, 0),
        "contient_perimes": any((m.get("type_avarie") or "perime") == "perime" for m in avaries),
        "produit_solde_id": str(produit["produit_solde_id"]) if (produit or {}).get("produit_solde_id") else None,
    }


# ---------------------------------------------------------------------------
# Mise en solde des produits avariés
# ---------------------------------------------------------------------------
# Les unités avariées sont déjà sorties du stock et comptées en perte à leur
# coût. Les solder les fait entrer dans un produit lié « Nom (soldé) », au
# coût 0 : vendues ensuite comme n'importe quel produit (caisse, vente,
# annulation, retour, rapports), elles rapportent leur prix soldé sans que la
# perte soit comptée deux fois.
TYPE_MOUVEMENT_MISE_EN_SOLDE = "mise en solde"
# Un vol ou une perte : les unités n'existent plus, on ne peut pas les vendre.
MOTIFS_NON_SOLDABLES = ("vol",)


def _avaries_soldables(avaries):
    return sum(abs(int(m.get("quantite") or 0)) for m in avaries
               if (m.get("type_avarie") or "perime") not in MOTIFS_NON_SOLDABLES)


def quantite_soldable(produit):
    """Unités avariées (hors vols) pas encore mises en solde."""
    avaries = [m for m in produit.get("mouvements_stock") or [] if m.get("type") == TYPE_MOUVEMENT_AVARIE]
    return max(_avaries_soldables(avaries) - int(produit.get("quantite_mise_en_solde") or 0), 0)


def _nom_produit_solde(produit):
    base = f"{produit['nom']} (soldé)"
    nom, n = base, 2
    while ProduitModel.nom_existe(produit["magasin_id"], nom):
        nom, n = f"{produit['nom']} (soldé {n})", n + 1
    return nom


def _produit_solde_actif(produit):
    """Le produit soldé déjà lié à ce produit, s'il est toujours actif."""
    lie = produit.get("produit_solde_id")
    if not lie:
        return None
    return ProduitModel.get_for_magasin(lie, produit["magasin_id"])


def solder_avarie_service(produit_id, data):
    """Met en solde des unités avariées d'un produit.

    :param data: quantite (int), prix_solde (prix unitaire de vente), note,
                 admin_id (auteur).
    """
    produit = ProduitModel.get_by_id(produit_id)
    if not produit:
        return {"error": "Article non trouvé"}, 404
    if produit.get("est_solde"):
        return {"error": "Cet article est déjà un article soldé."}, 400
    if numeros_serie_service.est_suivi(produit):
        return {"error": "La mise en solde n'est pas encore possible pour un article suivi par numéro de série."}, 400

    try:
        quantite = int(data.get("quantite"))
        prix_solde = round(float(data.get("prix_solde")), 2)
    except (TypeError, ValueError):
        return {"error": "Quantité et prix soldé doivent être des nombres."}, 400
    if quantite < 1:
        return {"error": "La quantité doit être d'au moins 1."}, 400
    if prix_solde < 0:
        return {"error": "Le prix soldé ne peut pas être négatif."}, 400
    prix_normal = float(produit.get("prix") or 0)
    if prix_normal > 0 and prix_solde >= prix_normal:
        return {"error": f"Le prix soldé doit être inférieur au prix normal ({prix_normal:g})."}, 400

    disponible = quantite_soldable(produit)
    if quantite > disponible:
        return {"error": f"Seulement {disponible} unité(s) avariée(s) peuvent encore être soldées "
                         "(les vols et les unités déjà mises en solde ne comptent pas)."}, 400

    # Réservation atomique : deux mises en solde simultanées ne peuvent pas
    # dépasser les unités avariées disponibles.
    collection = ProduitModel.get_collection()
    apres = collection.find_one_and_update(
        {"_id": produit["_id"]}, {"$inc": {"quantite_mise_en_solde": quantite}}, return_document=True)
    if quantite_soldable({**apres, "quantite_mise_en_solde": 0}) < int(apres.get("quantite_mise_en_solde") or 0):
        collection.update_one({"_id": produit["_id"]}, {"$inc": {"quantite_mise_en_solde": -quantite}})
        return {"error": "Ces unités viennent d'être mises en solde par quelqu'un d'autre."}, 409

    admin_id = data.get("admin_id")
    admin = UserModel.find_by_id(admin_id) if admin_id else None
    acteur = {
        "admin_id": admin_id,
        "admin_nom": f"{admin.get('first_name', '')} {admin.get('name', '')}".strip() if admin else "Inconnu",
    } if admin_id else {}
    note = str(data.get("note") or "").strip()[:200]

    solde = _produit_solde_actif(produit)
    if solde:
        solde_id = str(solde["_id"])
        # Un seul prix par produit : le nouveau prix soldé vaut pour toutes ses unités.
        collection.update_one({"_id": solde["_id"]}, {"$set": {"prix": prix_solde, "updated_at": datetime.utcnow()}})
    else:
        solde_id = ProduitModel.create_produit({
            "nom": _nom_produit_solde(produit),
            "description": f"Unités avariées de « {produit['nom']} » vendues à prix réduit.",
            "prix": prix_solde,
            "prix_achat": 0,
            "unite": produit.get("unite") or "unité",
            "quantite": 0,
            "categorie_id": str(produit["categorie_id"]) if produit.get("categorie_id") else None,
            "fournisseur_id": str(produit["fournisseur_id"]) if produit.get("fournisseur_id") else None,
            "magasin_id": str(produit["magasin_id"]),
            "code_barre": ProduitModel.generate_internal_barcode(produit["magasin_id"]),
        }, produit.get("image"))
        collection.update_one({"_id": ObjectId(solde_id)}, {"$set": {"est_solde": True, "solde_de": produit["_id"]}})

    # Coût 0 : la valeur de ces unités est déjà comptée en perte à l'avarie.
    valorisation_service.entree(solde_id, quantite, 0, TYPE_MOUVEMENT_MISE_EN_SOLDE)
    ProduitModel.increment_stock(
        solde_id, quantite, acteur=acteur, type_mouvement=TYPE_MOUVEMENT_MISE_EN_SOLDE,
        details={"prix_unitaire": 0, "prix_solde": prix_solde, "produit_origine_id": str(produit["_id"]),
                 "produit_origine_nom": produit["nom"], **({"description": note} if note else {})},
    )
    collection.update_one({"_id": produit["_id"]}, {
        "$set": {"produit_solde_id": ObjectId(solde_id), "updated_at": datetime.utcnow()},
        "$push": {"mises_en_solde": {
            "date": datetime.utcnow(), "quantite": quantite, "prix_solde": prix_solde,
            "produit_solde_id": solde_id, "acteur": acteur, **({"note": note} if note else {}),
        }},
    })

    produit_solde = ProduitModel.get_by_id(solde_id)
    return {
        "message": f"{quantite} unité(s) mise(s) en solde à {prix_solde:g} : "
                   f"elles sont en vente sous « {produit_solde['nom']} ».",
        "produit_solde": produit_solde,
        "quantite_soldable": disponible - quantite,
    }, 200


# Récupérer tous les produits d'un magasin
def get_produits_by_magasin_service(magasin_id, filters=None):
    if isinstance(magasin_id, dict) and "$oid" in magasin_id:
        magasin_id = magasin_id["$oid"]

    produits = ProduitModel.get_all_by_magasin(magasin_id)
    # Tri par date de mise à jour, du plus récent au plus ancien
    produits.sort(
        key=lambda x: x.get("updated_at") or x.get("created_at"),
        reverse=True
    )    
    result = []

    for produit in produits:
        produit["avarie"] = resume_avaries(produit.pop("mouvements_stock", None), produit)
        produit.pop("mises_en_solde", None)
        
        categorie_id = produit.get("categorie_id")
        categorie_nom = "Inconnu"
        if categorie_id:
            categorie = CategorieModel.get_categorie_by_id(categorie_id)
            if categorie:
                categorie_nom = categorie.get("nom", "Inconnu")
        produit["categorie_nom"] = categorie_nom

        produit["image_url"] = (
            f"{request.host_url}uploads/images/{produit['image']}"
            if produit.get("image") else None
        )

        # Vérification et signalement de stock faible (< 50)
        if produit.get("quantite", 0) < 50:
            produit["alert_stock"] = True
            produit["alerte_message"] = "Stock faible (inférieur à 50)"
        else:
            produit["alert_stock"] = False

        # Application des filtres
        if filters:
            if filters["nom"] and filters["nom"].lower() not in produit["nom"].lower():
                continue
            if filters["etat"] and produit.get("etat") != filters["etat"]:
                continue
            if filters["categorie_nom"] and filters["categorie_nom"].lower() not in categorie_nom.lower():
                continue
            if filters["prix_min"] and produit.get("prix", 0) < filters["prix_min"]:
                continue
            if filters["prix_max"] and produit.get("prix", 0) > filters["prix_max"]:
                continue
            if filters["quantite_min"] and produit.get("quantite", 0) < filters["quantite_min"]:
                continue
            if filters["quantite_max"] and produit.get("quantite", 0) > filters["quantite_max"]:
                continue

        result.append(produit)
        # retourner les produits par ordre de récent
    #trier par date de mise à jour
    result.sort(
        key=lambda x: x.get("updated_at") or x.get("created_at") or datetime.min,
        reverse=True
    )
    #result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

    return result



## Mettre à jour un produit
def update_produit_service(produit_id, data, image_file=None):
    # Récupérer l'état actuel du produit
    produit_avant = ProduitModel.get_by_id(produit_id)
    if not produit_avant:
        return {"error": "Article non trouvé"}, 404
    
    ancienne_quantite = produit_avant["quantite"]  #  Stock actuel
    # Transformer la quantité en entier si elle est présente
    if "quantite" in data and data["quantite"] is not None:
        try:
            data["quantite"] = int(data["quantite"])
        except ValueError:
            return {"error": "La quantité doit être un entier"}, 400
    # Valider la nouvelle quantité si présente
    if "quantite" in data and data["quantite"] is not None:
        if data["quantite"] < 0:
            return {"error": "La quantité doit être un entier positif"}, 400

    if data.get("code_barre") and ProduitModel.code_barre_existe(
        produit_avant["magasin_id"], data["code_barre"], exclude_id=produit_id
    ):
        return {"error": "Ce code-barre est déjà utilisé par un autre article du magasin."}, 409

    # Traitement de l'image si fournie
    if image_file:
        try:
            data["image"] = save_image(image_file)
        except ValueError as error:
            return {"error": str(error)}, 400


    # Suivi par numéro de série : activation (numéros du stock actuel) ou arrêt.
    suivi_avant = numeros_serie_service.est_suivi(produit_avant)
    suivi_demande = _vrai(data.pop("suivi_serie")) if "suivi_serie" in data else suivi_avant
    numeros_saisis = data.pop("numeros_serie", None)
    if suivi_demande and data.get("quantite") is not None and int(data["quantite"]) != int(ancienne_quantite):
        return {"error": "Le stock d'un article suivi par numéro de série change par un approvisionnement "
                         "(nouveaux numéros) ou une avarie (numéros choisis), pas en modifiant la quantité."}, 400
    if suivi_demande and not suivi_avant:
        if not numeros_serie_service.actif(produit_avant["magasin_id"]):
            return {"error": "Activez d'abord les numéros de série dans Paramètres › Stock."}, 400
        if produit_avant.get("conditionnements") or data.get("conditionnements"):
            return {"error": "Un article suivi par numéro de série se vend à l'unité : retirez ses conditionnements."}, 400
        from models.stock_livreur_model import StockLivreurModel
        chez_gerants = sum(int(s.get("quantite") or 0) for s in StockLivreurModel.get_collection().find(
            {"produit_id": ObjectId(str(produit_id))}))
        if chez_gerants > 0:
            return {"error": f"{chez_gerants} unité(s) de cet article sont chez des gérants : faites-les revenir au "
                             "magasin avant d'activer le suivi par numéro de série."}, 400
        try:
            numeros = numeros_serie_service.verifier_nouveaux(produit_avant, numeros_saisis, int(ancienne_quantite))
        except ErreurNumeroSerie as err:
            return {"error": str(err)}, err.statut
        numeros_serie_service.entrer(produit_avant, numeros, "stock existant",
                                     cout=float(produit_avant.get("prix_achat") or 0), acteur=_acteur(data.get("admin_id")))
        ProduitModel.get_collection().update_one({"_id": produit_avant["_id"]}, {"$set": {"suivi_serie": True}})
    elif suivi_avant and not suivi_demande:
        numeros_serie_service.arreter_suivi(produit_avant, _acteur(data.get("admin_id")))
        ProduitModel.get_collection().update_one({"_id": produit_avant["_id"]}, {"$set": {"suivi_serie": False}})
    elif suivi_avant and data.get("conditionnements"):
        return {"error": "Un article suivi par numéro de série se vend à l'unité : pas de conditionnement."}, 400

    # Coût d'achat : calculé par la valorisation, il ne s'écrit pas
    # directement. Une modification n'est acceptée que comme correction du
    # lot initial (article qui n'a encore reçu qu'un seul achat).
    cout_saisi = data.pop("prix_achat", None)
    if cout_saisi not in (None, ""):
        try:
            cout_saisi = float(cout_saisi)
        except (TypeError, ValueError):
            return {"error": "Le coût d'achat doit être un nombre"}, 400
        if cout_saisi < 0:
            return {"error": "Le coût d'achat ne peut pas être négatif"}, 400
        if abs(cout_saisi - float(produit_avant.get("prix_achat", 0) or 0)) > 0.005:
            try:
                valorisation_service.corriger_cout(produit_id, cout_saisi)
            except ValueError as erreur:
                return {"error": str(erreur)}, 400
            produit_avant = ProduitModel.get_by_id(produit_id)

    # Valorisation : une hausse manuelle crée un lot (au dernier prix payé),
    # une baisse est une sortie (perte d'inventaire) valorisée selon la
    # méthode du magasin.
    if data.get("quantite") is not None and int(data["quantite"]) != int(ancienne_quantite):
        ecart = int(data["quantite"]) - int(ancienne_quantite)
        if ecart > 0:
            prix = float(produit_avant.get("dernier_prix_achat", produit_avant.get("prix_achat", 0)) or 0)
            valorisation_service.entree(produit_id, ecart, prix, "ajustement d'inventaire")
        else:
            methode = valorisation_service.get_methode(produit_avant.get("magasin_id"))
            valorisation_service.sortie(produit_id, -ecart, methode)

    # Appliquer la mise à jour sur la base
    from services.conditionnements import ErreurConditionnement
    try:
        ProduitModel.update_produit(produit_id, data)
    except ErreurConditionnement as erreur:
        return {"error": str(erreur)}, 400  # réponse ≥ 400 : rien n'est enregistré
    produit_apres = ProduitModel.get_by_id(produit_id)

    # Vérifier si la quantité a changé
    nouvelle_quantite = data.get("quantite", ancienne_quantite)

    if nouvelle_quantite != ancienne_quantite:
        diff = nouvelle_quantite - ancienne_quantite

        # Préparer l'acteur pour le mouvement
        admin_data = UserModel.find_by_id(data.get("admin_id"))
        acteur = {
            "admin_id": data.get("admin_id"),
            "admin_nom": f"{admin_data.get('first_name', '')} {admin_data.get('name', '')}".strip() if admin_data else "Inconnu"
        }

        # Ajouter simplement le mouvement de stock (la quantité est déjà mise à jour par update_produit)
        ProduitModel.ajouter_mouvement_stock(
            produit_id=produit_id,
            mouvement={
                "type": "modification_produit",
                "quantite": diff,
                "acteur": acteur
            }
        )

    return {
        "message": "Article mis à jour avec succès",
        "produit": produit_apres
    }, 200



# Mettre un produit à la corbeille (Soft Delete)
def delete_produit_service(produit_id):
    produit = ProduitModel.delete_produit(produit_id)
    if not produit:
        return {"error": "Article non trouvé"}, 404
    return {"message": "Article mis à la corbeille avec succès"}, 200

MAX_SUPPRESSION_GROUPEE = 1000


def supprimer_plusieurs_service(ids, magasin_id):
    """Met plusieurs articles du magasin à la corbeille en une fois.

    Tout ou rien : un identifiant invalide, en double ou qui n'est pas un
    article actif du magasin fait refuser toute la demande (rien n'est
    supprimé), pour que l'utilisateur sache exactement ce qui s'est passé.
    """
    from bson import ObjectId
    if not isinstance(ids, list) or not ids:
        return {"error": "Sélectionnez au moins un article."}, 400
    if len(ids) > MAX_SUPPRESSION_GROUPEE:
        return {"error": f"Au plus {MAX_SUPPRESSION_GROUPEE} articles à la fois."}, 400
    uniques = list(dict.fromkeys(str(i) for i in ids))
    if any(not ObjectId.is_valid(i) for i in uniques):
        return {"error": "Identifiant d'article invalide."}, 400
    trouves = ProduitModel.get_collection().find({
        "_id": {"$in": [ObjectId(i) for i in uniques]},
        "magasin_id": ObjectId(str(magasin_id)),
        "deleted_at": {"$exists": False},
    })
    trouves = {str(p["_id"]) for p in trouves}
    manquants = [i for i in uniques if i not in trouves]
    if manquants:
        return {"error": f"{len(manquants)} article(s) introuvable(s) ou déjà supprimé(s) : actualisez la liste.",
                "introuvables": manquants}, 404
    for produit_id in uniques:
        ProduitModel.delete_produit(produit_id)
    return {"message": f"{len(uniques)} article(s) mis à la corbeille.", "supprimes": len(uniques)}, 200


# Restaurer un produit
def restaurer_produit_service(produit_id):
    from bson import ObjectId
    produit = ProduitModel.get_collection().find_one_and_update(
        {"_id": ObjectId(produit_id), "deleted_at": {"$exists": True}},
        {"$unset": {"deleted_at": ""}},
        return_document=True
    )
    if not produit:
        return {"error": "Article introuvable dans la corbeille"}, 404
    return {"message": "Article restauré avec succès"}, 200




# Récupérer un produit par son ID
def get_produit_by_id_service(produit_id):
    produit =  ProduitModel.get_by_id(produit_id)
    if not produit:
        return None
    produit['image'] = f"{request.host_url}uploads/images/{produit['image']}" if produit.get("image") else None
    return produit





# Récupérer les mouvements de stock d'un produit
def get_mouvements_stock_service(produit_id, type_mouvement=None):
    try:
        mouvements = ProduitModel.get_mouvements_stock(produit_id, type_mouvement)
        return {"mouvements": mouvements}, 200
    except Exception as e:
        return {"message": f"Erreur serveur : {str(e)}"}, 500




# Statistiques livreur

def get_meilleurs_produits_livreur(livreur_id):
    """
    Retourne les meilleurs produits livrés par un livreur donné,
    triés par quantité totale livrée (du plus vendu au moins vendu).
    Ainsi que : 
    - quantité totale livrée par produit
    - montant total généré par produit
    - pourcentage basé sur la quantité
    - pourcentage basé sur le montant
    
    """
    # 1. Récupérer toutes les livraisons faites par le livreur

    livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)

    # 2. Agréger les produits livrés
    stats = {}
    
    total_quantite = 0
    total_montant = 0  

    for livraison in livraisons:
        produits = livraison.get("produits", [])
        for produit in produits:
            produit_id = str(produit["produit_id"])
            quantite = produit.get("quantite", 0)
            prix_unitaire = produit.get("prix", 0)
            montant = quantite * prix_unitaire
            
            if produit_id not in stats:
                stats[produit_id] = {
                    "produit_id": produit_id,
                    "nom": produit.get("nom", ""),  # Peut être vide si non inclus dans la livraison
                    "quantite_totale": 0,
                    "montant_total": 0
                }

            stats[produit_id]["quantite_totale"] += quantite
            stats[produit_id]["montant_total"] += montant
            
            total_quantite += quantite
            total_montant += montant
            
    # 3. Compléter les infos des produits s'il en manque (optionnel mais recommandé)
    produit_ids = list(stats.keys())
    produits_info = ProduitModel.get_produits_by_ids(produit_ids)

    for produit in produits_info:
        pid = str(produit["_id"])
        if pid in stats:
            stats[pid]["nom"] = produit.get("nom", stats[pid]["nom"])
            quantite_totale = stats[pid]["quantite_totale"]
            montant_total = stats[pid]["montant_total"]
            
            stats[pid]["prix"] = produit.get("prix", 0)
            
            stats[pid]["pourcentage_quantite"] = round((quantite_totale / total_quantite) * 100, 2) if total_quantite else 0
            stats[pid]["pourcentage_montant"] = round((montant_total / total_montant) * 100, 2) if total_montant else 0
    # 4. Retourner la liste triée par quantité descendante
    return sorted(stats.values(), key=lambda x: x["montant_total"], reverse=True)[:5]





# Statistiques manager

def get_meilleurs_produits_total(livreur_id=None):
    """
    Retourne les meilleurs produits livrés (tous les livreurs ou un seul),
    triés par quantité totale livrée (du plus vendu au moins vendu).
    Ainsi que : 
    - quantité totale livrée par produit
    - montant total généré par produit
    - pourcentage basé sur la quantité
    - pourcentage basé sur le montant
    
    """
    # 1. Récupérer toutes les livraisons faites dans tout le magasin
    if livreur_id:
        livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)
    else:
        livraisons = LivraisonModel.get_all_livraisons()

    # 2. Agréger les produits livrés
    stats = {}
    
    total_quantite = 0
    total_montant = 0  

    for livraison in livraisons:
        produits = livraison.get("produits", [])
        for produit in produits:
            produit_id = str(produit["produit_id"])
            quantite = produit.get("quantite", 0)
            prix_unitaire = produit.get("prix", 0)
            montant = quantite * prix_unitaire
            
            if produit_id not in stats:
                stats[produit_id] = {
                    "produit_id": produit_id,
                    "nom": produit.get("nom", ""),  # Peut être vide si non inclus dans la livraison
                    "quantite_totale": 0,
                    "montant_total": 0
                }

            stats[produit_id]["quantite_totale"] += quantite
            stats[produit_id]["montant_total"] += montant
            
            total_quantite += quantite
            total_montant += montant
            
    # 3. Compléter les infos des produits s'il en manque (optionnel mais recommandé)
    produit_ids = list(stats.keys())
    produits_info = ProduitModel.get_produits_by_ids(produit_ids)

    for produit in produits_info:
        pid = str(produit["_id"])
        if pid in stats:
            stats[pid]["nom"] = produit.get("nom", stats[pid]["nom"])
            quantite_totale = stats[pid]["quantite_totale"]
            montant_total = stats[pid]["montant_total"]
            
            stats[pid]["prix"] = produit.get("prix", 0)
            
            stats[pid]["pourcentage_quantite"] = round((quantite_totale / total_quantite) * 100, 2) if total_quantite else 0
            stats[pid]["pourcentage_montant"] = round((montant_total / total_montant) * 100, 2) if total_montant else 0
    # 4. Retourner la liste triée par quantité descendante
    return sorted(stats.values(), key=lambda x: x["montant_total"], reverse=True)[:5]


# Retourne tous les produits d'un magasin, regroupés par catégorie
def get_stock_par_categorie_total(data):
    produits = ProduitModel.get_all_by_magasin(data["magasin_id"])  # Ce sont directement des produits
    produits_par_categorie = {}

    for produit in produits:
        produit["_id"] = str(produit["_id"])
        produit["categorie_id"] = str(produit["categorie_id"]) if produit.get("categorie_id") else None
        produit["quantite_magasin"] = produit["quantite"]  # Renomme pour clarté
        produit['image'] = f"{request.host_url}uploads/images/{produit['image']}" if produit.get("image") else None

        #  Suppression des mouvements de stock
        if "mouvements_stock" in produit:
            del produit["mouvements_stock"]

        categorie_id = produit["categorie_id"]
        if categorie_id not in produits_par_categorie:
            categorie = CategorieModel.get_categorie_by_id(categorie_id)
            if not categorie:
                continue
            categorie["_id"] = str(categorie["_id"])
            categorie["produits"] = []

            produits_par_categorie[categorie_id] = categorie

        produits_par_categorie[categorie_id]["produits"].append(produit)

    return list(produits_par_categorie.values())


# approvisionner un produit dans le stock du magasin
def approvisionner_produit_service(produit_id, data):
    try:
        quantite = int(data["quantite"])
        admin_id = data.get("admin_id")
        admin_data = UserModel.find_by_id(admin_id)
        acteur = {
            "admin_id": admin_id,
            "admin_nom": f"{admin_data.get('first_name', '')} {admin_data.get('name', '')}".strip() if admin_data else "Inconnu"
        } if admin_id else {}

        # Prix d'achat unitaire de ce lot : saisi, sinon montant payé / quantité,
        # sinon le prix d'achat actuel du produit.
        produit_avant = ProduitModel.get_by_id(produit_id)
        if not produit_avant:
            return {"error": "Article non trouvé"}, 404
        prix_unitaire = data.get("prix_achat")
        if prix_unitaire in (None, "") and data.get("montant") not in (None, "", 0) and quantite > 0:
            prix_unitaire = float(data["montant"]) / quantite
        if prix_unitaire in (None, ""):
            # Sans prix saisi : le dernier prix payé, pas le coût moyen.
            prix_unitaire = produit_avant.get("dernier_prix_achat", produit_avant.get("prix_achat", 0))
        prix_unitaire = float(prix_unitaire)
        if prix_unitaire < 0:
            return {"error": "Le prix d'achat ne peut pas être négatif"}, 400
        numeros = []
        if numeros_serie_service.est_suivi(produit_avant):
            try:
                numeros = numeros_serie_service.verifier_nouveaux(produit_avant, data.get("numeros_serie"), quantite)
            except ErreurNumeroSerie as err:
                return {"error": str(err)}, err.statut
        valorisation_service.entree(produit_id, quantite, prix_unitaire, "réapprovisionnement")
        if numeros:
            numeros_serie_service.entrer(produit_avant, numeros, "réapprovisionnement", cout=prix_unitaire,
                                         acteur={**acteur, "admin_nom": acteur.get("admin_nom")},
                                         fournisseur_id=data.get("fournisseur_id"), facture_id=data.get("facture_id"))

        produit = ProduitModel.approvisionner_produit(produit_id, quantite, acteur, prix_unitaire, data.get("facture_id"))
        if produit:
            # Le prix payé est gardé comme « dernier prix d'achat » (proposé au
            # prochain approvisionnement). Le coût d'achat de l'article, lui,
            # vient d'être recalculé par la valorisation (CMUP / FIFO / LIFO).
            ProduitModel.get_collection().update_one({"_id": produit["_id"]}, {"$set": {"dernier_prix_achat": prix_unitaire}})
            produit["dernier_prix_achat"] = prix_unitaire

        if not produit:
            return {"error": "Article non trouvé"}, 404

        return {
            "message": "Article approvisionné avec succès",
            "produit": produit
        }, 200

    except ValueError:
        return {"error": "La quantité doit être un entier valide"}, 400
# Gérer les produits périmés (retrait du stock)
def manage_expired_product_service(produit_id, data):
    try:
        produit = ProduitModel.get_by_id(produit_id)
        if not produit:
            return {"error": "Article non trouvé"}, 404

        quantite_perime = int(data["quantite"])
        
        if quantite_perime > produit["quantite"]:
            return {
                "error": f"La quantité périmée ({quantite_perime}) ne peut pas dépasser le stock actuel ({produit['quantite']})"
            }, 400

        numeros_avaries = []
        if numeros_serie_service.est_suivi(produit):
            try:
                numeros_avaries = numeros_serie_service.verifier_disponibles(
                    produit, data.get("numeros_serie"), quantite_perime)
            except ErreurNumeroSerie as err:
                return {"error": str(err)}, err.statut

        admin_id = data.get("admin_id")
        admin_data = UserModel.find_by_id(admin_id)
        acteur = {
            "admin_id": admin_id,
            "admin_nom": f"{admin_data.get('first_name', '')} {admin_data.get('name', '')}".strip() if admin_data else "Inconnu"
        } if admin_id else {}

        # Le motif et les détails saisis dans le formulaire sont conservés
        # dans le mouvement, pour pouvoir les consulter dans la liste des avaries.
        details = {
            champ: data[champ]
            for champ in ("type_avarie", "description", "responsable")
            if data.get(champ)
        }
        perte = valorisation_service.sortie(
            produit_id, quantite_perime, valorisation_service.get_methode(produit.get("magasin_id"))
        )
        details["valeur_perte"] = perte["cout_total"]
        details["methode_valorisation"] = perte["methode"]
        if numeros_avaries:
            details["numeros_serie"] = numeros_avaries
            numeros_serie_service.sortir(produit_id, numeros_avaries, "avarie", acteur,
                                         statut=numeros_serie_service.AVARIE,
                                         type_avarie=data.get("type_avarie"), motif=data.get("description"))
        updated_produit = ProduitModel.decrement_stock(
            produit_id=produit_id,
            quantite=quantite_perime,
            acteur=acteur,
            type_mouvement=TYPE_MOUVEMENT_AVARIE,
            details=details,
        )

        return {
            "message": "Article périmé enregistré et retiré du stock",
            "produit": updated_produit
        }, 200

    except ValueError:
        return {"error": "La quantité doit être un entier valide"}, 400
    except Exception as e:
        return {"error": f"Erreur lors du traitement : {str(e)}"}, 500
# Récupérer tous les mouvements de stock d'un magasin pour une année donnée
def get_all_magasin_mouvements_service(magasin_id, year=None, type_mouvement=None):
    if year:
        try:
            year = int(year)
        except ValueError:
            year = datetime.now().year
    else:
        year = datetime.now().year
    
    start_date = datetime(year, 1, 1)
    end_date = datetime(year, 12, 31, 23, 59, 59)
    
    # ✅ CORRECTION : Si le type est 'tous', on ne filtre pas par type
    if type_mouvement == "tous" or not type_mouvement:
        type_mouvement = None

    # Le journal des mouvements fait foi : chaque vente y est tracée au moment
    # où elle a lieu (ainsi que ses modifications et retours). Le remplacer par
    # les ventes actuelles, déjà réduites par les retours, comptait les retours
    # deux fois (vente de 5 entièrement retournée : +5 au lieu de 0).
    tous_les_mouvements = ProduitModel.get_all_mouvements_by_magasin(
        magasin_id=magasin_id,
        start_date=start_date,
        end_date=end_date,
        type_mouvement=type_mouvement
    )

    tous_les_mouvements.sort(key=lambda x: x["date"] if isinstance(x["date"], datetime) else datetime.fromisoformat(x["date"]), reverse=True)

    # Formatter les dates pour le JSON
    for m in tous_les_mouvements:
        if isinstance(m.get("date"), datetime):
            m["date"] = m["date"].isoformat()
            
    return {
        "year": year,
        "type_filtre": type_mouvement if type_mouvement else "tous",
        "total": len(tous_les_mouvements),
        "mouvements": tous_les_mouvements
    }, 200


def _serialiser_conditionnement(cond):
    cond = dict(cond)
    if cond.get("_id") is not None:
        cond["_id"] = str(cond["_id"])
    return cond


def get_produit_by_barcode_service(barcode, magasin_id):
    """
    Produit scanné. Si le code est celui d'un carton (conditionnement),
    la réponse porte aussi « conditionnement_scanne ».
    """
    try:
        produit, conditionnement = ProduitModel.trouver_par_code(magasin_id, barcode)
        numero_scanne = None
        if not produit:
            # Le code scanné est peut-être le numéro de série d'une unité en stock.
            unite = numeros_serie_service.trouver_en_stock(magasin_id, barcode)
            if unite:
                produit = ProduitModel.get_collection().find_one(
                    {"_id": unite["produit_id"], "deleted_at": {"$exists": False}})
                numero_scanne = unite["numero_serie"] if produit else None
        if not produit:
            return {"message": "Article non trouvé", "code_barre": barcode}, 404

        # Sérialisation
        produit["_id"] = str(produit["_id"])
        produit["magasin_id"] = str(produit["magasin_id"])
        produit["categorie_id"] = str(produit["categorie_id"]) if produit.get("categorie_id") else None
        if produit.get("fournisseur_id"):
            produit["fournisseur_id"] = str(produit["fournisseur_id"])
        else:
            produit["fournisseur_id"] = None
        produit.pop("mouvements_stock", None)
        produit["conditionnements"] = [_serialiser_conditionnement(c) for c in produit.get("conditionnements") or []]
        produit["conditionnement_scanne"] = _serialiser_conditionnement(conditionnement) if conditionnement else None
        produit["numero_serie_scanne"] = numero_scanne
        produit["suivi_serie"] = bool(produit.get("suivi_serie"))

        produit["image_url"] = (
            f"{request.host_url}uploads/images/{produit['image']}"
            if produit.get("image") else None
        )

        return produit, 200
    except Exception as e:
        return {"error": str(e)}, 500


def associer_code_barre_service(produit_id, magasin_id, code_barre, conditionnement_id=None):
    """
    Donne un code-barre à un produit, ou à l'un de ses cartons (conditionnement_id).
    Un code vide retire le code existant.
    """
    from models.produit_model import normaliser_code_barre

    code = normaliser_code_barre(code_barre)
    if code and len(code) > 64:
        return {"error": "Code-barre trop long (64 caractères au plus)."}, 400
    produit = ProduitModel.get_for_magasin(produit_id, magasin_id)
    if not produit:
        return {"error": "Article non trouvé"}, 404

    if conditionnement_id is None:
        if code and ProduitModel.code_barre_existe(magasin_id, code, exclude_id=produit_id):
            return {"error": "Ce code-barre est déjà utilisé dans le magasin."}, 409
        ProduitModel.get_collection().update_one({"_id": produit["_id"]}, {"$set": {"code_barre": code}})
        return {"message": "Code-barre enregistré", "produit_id": str(produit["_id"]), "code_barre": code}, 200

    conditionnements = list(produit.get("conditionnements") or [])
    cible = next((c for c in conditionnements if str(c.get("_id")) == str(conditionnement_id)), None)
    if not cible:
        return {"error": "Conditionnement non trouvé"}, 404
    if code and ProduitModel.code_barre_existe(magasin_id, code, exclude_id=produit_id,
                                               exclude_cond_id=conditionnement_id):
        return {"error": "Ce code-barre est déjà utilisé dans le magasin."}, 409
    cible["code_barre"] = code
    ProduitModel.get_collection().update_one({"_id": produit["_id"]}, {"$set": {"conditionnements": conditionnements}})
    return {"message": "Code-barre du conditionnement enregistré", "produit_id": str(produit["_id"]),
            "conditionnement_id": str(conditionnement_id), "code_barre": code}, 200


def generer_codes_manquants_service(magasin_id, produit_ids):
    """Donne un code interne EAN-13 aux produits choisis qui n'en ont pas encore."""
    generes = []
    for produit_id in produit_ids or []:
        produit = ProduitModel.get_for_magasin(produit_id, magasin_id)
        if not produit or produit.get("code_barre"):
            continue
        code = ProduitModel.generate_internal_barcode(magasin_id)
        ProduitModel.get_collection().update_one({"_id": produit["_id"]}, {"$set": {"code_barre": code}})
        generes.append({"produit_id": str(produit["_id"]), "nom": produit.get("nom"), "code_barre": code})
    return {"message": f"{len(generes)} code(s)-barres générés", "generes": generes}, 200


def generate_barcode_service(magasin_id):
    """
    Génère un code-barres interne unique.
    """
    try:
        code = ProduitModel.generate_internal_barcode(magasin_id)
        return {"code_barre": code}, 200
    except Exception as e:
        return {"error": str(e)}, 500
def supprimer_definitivement_produit_service(produit_id):
    """
    Supprime définitivement un produit de la corbeille.
    """
    try:
        produit = ProduitModel.get_collection().find_one({
            "_id": ObjectId(produit_id),
            "deleted_at": {"$exists": True}
        })

        if not produit:
            return {"error": "Article introuvable dans la corbeille"}, 404

        # Suppression définitive
        ProduitModel.delete_permanently(produit_id)

        return {"message": "Article supprimé définitivement avec succès"}, 200

    except Exception as e:
        return {"error": f"Erreur lors de la suppression définitive : {str(e)}"}, 500
