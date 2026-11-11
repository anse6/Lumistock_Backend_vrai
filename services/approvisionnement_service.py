from datetime import datetime
from schemas.approvisionnement_schema import ApprovisionnementSchema
from models.approvisionnement_model import ApprovisionnementModel
from models.stock_livreur_model import StockLivreurModel
from models.produit_model import ProduitModel
from marshmallow import ValidationError
from utils.error_handler import verifier_produits_approvisionnement_valide
from models.user_model import UserModel 
from bson import ObjectId


def _refus_articles_suivis(produits):
    """Les gérants ne reçoivent pas encore d'articles suivis par numéro de série."""
    from services import numeros_serie_service
    for p in produits:
        produit = ProduitModel.get_by_id(str(p.get("produit_id")))
        if numeros_serie_service.est_suivi(produit):
            return {"error": f"« {produit.get('nom')} » est suivi par numéro de série : la remise à un gérant "
                             "n'est pas encore possible pour ces articles."}, 400
    return None


def approvisionner_livreur(data):
    try:
        validated_data = ApprovisionnementSchema().load(data)
    except ValidationError as err:
        return {"error": err.messages}, 400

    # Doit être fait uniquement si la validation a réussi
    produits = validated_data["produits"]

    # Validation personnalisée
    is_valid, error = verifier_produits_approvisionnement_valide(produits)
    if not is_valid:
        return {"error": error}, 400
    erreur_serie = _refus_articles_suivis(produits)
    if erreur_serie:
        return erreur_serie

    # 1. Enregistrer l'approvisionnement
    appro_id = ApprovisionnementModel.enregistrer_approvisionnement(validated_data)

    # 2. Mettre à jour le stock du livreur
    livreur_id = validated_data["livreur_id"]
    StockLivreurModel.ajouter_stock(livreur_id, produits)

    # 3. Décrémenter le stock du magasin
    #print("id du manager : ", validated_data["admin_id"])
    # ✅ ENRICHISSEMENT : Récupérer les infos du livreur pour l'historique détaillé
    livreur = UserModel.find_by_id(livreur_id)
    acteur = {
        "admin_id": validated_data["admin_id"],
        "livreur_id": str(livreur_id),
        "livreur_nom": f"{livreur.get('first_name', '')} {livreur.get('name', '')}".strip() if livreur else "Inconnu"
    }
    for produit in produits:
        ProduitModel.decrement_stock(
            produit_id=produit["produit_id"],
            quantite=produit["quantite"],
            acteur=acteur,
            type_mouvement="approvisionnement_livreur"
        )

    return {"message": "Approvisionnement réussi", "approvisionnement_id": appro_id}, 201



ROLES_LIBELLES = {
    "admin": "Administrateur",
    "manager": "Manager",
    "gerant": "Gérant",
    "livreur": "Gérant",
    "staff": "Staff",
}


def _personne(user_id):
    """Identité publique d'un utilisateur pour les documents (jamais le mot de passe)."""
    user = UserModel.find_by_id(user_id) if user_id else None
    if not user:
        return None
    return {
        "id": str(user["_id"]),
        "prenom": user.get("first_name", ""),
        "nom": user.get("name", ""),
        "nom_complet": f"{user.get('first_name', '')} {user.get('name', '')}".strip(),
        "role": ROLES_LIBELLES.get(user.get("role"), user.get("role", "")),
        "email": user.get("email", ""),
        "telephone": user.get("telephone", ""),
    }


def serialize_approvisionnement(approvisionnement_data):
    # Celui qui reçoit le stock (gérant) et celui qui l'a remis (admin/manager).
    livreur = _personne(approvisionnement_data.get("livreur_id"))
    auteur = _personne(approvisionnement_data.get("admin_id"))
    modifie_par = _personne(approvisionnement_data.get("modifie_par"))
    prenom_livreur = livreur["prenom"] if livreur else ""
    nom_livreur = livreur["nom"] if livreur else ""
    
    id = str(approvisionnement_data["_id"])
    produits = approvisionnement_data.get("produits", [])
    # Calcul du montant total des produits
    # Note: Le montant total n'est pas calculé ici car il n'est pas stocké dans le modèle
    quantite_stock_total = 0
    
    for produit in produits:
        produit_id = produit.get("produit_id")
        if produit_id:
            produit_data = ProduitModel.get_by_id(produit_id)
            if produit_data:
                produit["nom"] = produit_data.get("nom", "Nom inconnu")
                produit["prix"] = produit_data.get("prix", 0)
                produit["prix_achat"] = produit_data.get("prix_achat", 0)
                produit["unite"] = produit_data.get("unite", "")
            else:
                produit["nom"] = "Nom inconnu"
                produit["prix"] = 0
                produit["prix_achat"] = 0
                produit["unite"] = ""
            produit["produit_id"] = str(produit_id)
        else:
            produit["nom"] = "ID inconnu"
            produit["prix"] = 0
            
        # 🔹 Récupérer la quantité en stock actuelle pour ce livreur
        stock = StockLivreurModel.get_collection().find_one({
            "livreur_id": ObjectId(approvisionnement_data['livreur_id']),
            "produit_id": ObjectId(produit_id)
        })
        # calculer la somme des quantites de tous les produits du stock du livreur
        produit["quantite_livreur"] = stock.get("quantite", 0) if stock else 0
        # calculer la somme de toutes les quantite par produit
        
    # Calcul du stock total actuel du livreur (tous produits confondus)
    all_stocks = StockLivreurModel.get_collection().find({
        "livreur_id": ObjectId(approvisionnement_data['livreur_id'])
    })
    quantite_stock_total = sum(stock.get("quantite", 0) for stock in all_stocks)

        
        
        
    montant_total = sum([produit.get("prix", 0) * produit.get("quantite", 0) for produit in produits])
    montant_achat = sum([produit.get("prix_achat", 0) * produit.get("quantite", 0) for produit in produits])
    quantite_total_approvisionne = sum([produit.get("quantite", 0) for produit in produits])

    # Récuperer le quantite en stock restant pour le livreur dans son stock

    return {
        "id": id,
        "livreur_id": str(approvisionnement_data["livreur_id"]),
        "first_name": prenom_livreur,
        "last_name": nom_livreur,
        "livreur": livreur,
        "approvisionne_par": auteur,
        "modifie_par": modifie_par,
        "updated_at": (
            approvisionnement_data["updated_at"].isoformat()
            if isinstance(approvisionnement_data.get("updated_at"), datetime)
            else approvisionnement_data.get("updated_at")
        ),
        "montant": montant_total,
        "montant_achat": montant_achat,
        "quantite": quantite_total_approvisionne,
        "quantite_stock_total":quantite_stock_total,
        "produits" : produits,
        "created_at": (
            approvisionnement_data["created_at"].isoformat()
            if isinstance(approvisionnement_data["created_at"], datetime)
            else str(approvisionnement_data["created_at"])
        ),
    }

# Récupérer tous les approvisionnement effectué par un manager
def get_approvisionner_service(magasin_id):
    """
    Retourne tous les approvisionnements du magasin, quel que soit leur auteur :
    un manager voit aussi ceux de l'administrateur, et une modification
    (qui change l'auteur de la dernière action) ne fait plus disparaître la ligne.
    """
    if not magasin_id:
        return [], 200
    approvisionnement_data = ApprovisionnementModel.get_all_by_magasin(magasin_id)
    
    # Trie des approvionnements par date de création
    approvisionnement_data_tries = sorted(
        approvisionnement_data,
        key=lambda l:l.get("created_at", datetime.min), 
        reverse=True
    )
    approvisionnement_nettoyees = [serialize_approvisionnement(a) for a in approvisionnement_data_tries]
    

    return approvisionnement_nettoyees, 200
    
   

def delete_approvisionnement_service(approvisionnement_id):
    """
    Supprime un approvisionnement :
    - Réintègre les produits restants dans le stock principal
    - Réduit le stock du livreur selon ce qui est encore disponible
    - Supprime l'enregistrement d'approvisionnement
    - Ajoute un mouvement de stock de type 'annulation_approvisionnement'
    """
    try:
        approvisionnement = ApprovisionnementModel.get_by_id(approvisionnement_id)
        if not approvisionnement:
            return {"message": "Approvisionnement introuvable"}, 404

        produits = approvisionnement.get("produits", [])
        livreur_id = approvisionnement.get("livreur_id")

        if not produits or not livreur_id:
            return {"message": "Approvisionnement invalide : données incomplètes"}, 400

        for produit in produits:
            try:
                produit_id = produit.get("produit_id")
                quantite_appro = produit.get("quantite")

                if not produit_id or quantite_appro is None:
                    print(f"⛔ Article mal structuré : {produit}")
                    continue

                produit_id_obj = ObjectId(produit_id)

                if not ProduitModel.get_by_id(str(produit_id_obj)):
                    print(f"⛔ Article {produit_id} introuvable dans la base principale")
                    continue

                # Vérifier stock réel chez le livreur
                filtre = {
                    "livreur_id": ObjectId(livreur_id),
                    "produit_id": produit_id_obj
                }
                stock_livreur = StockLivreurModel.get_collection().find_one(filtre)
                quantite_en_stock = stock_livreur["quantite"] if stock_livreur else 0

                if quantite_en_stock <= 0:
                    print(f"⚠️ Aucune quantité disponible à rapatrier pour article {produit_id}")
                    continue

                # Calcul de la quantité à récupérer
                quantite_a_retirer = min(quantite_en_stock, quantite_appro)

                # Retirer du stock livreur
                try:
                    StockLivreurModel.retirer_du_stock(str(livreur_id), str(produit_id_obj), quantite_a_retirer)
                except Exception as retrait_err:
                    print(f"⚠️ Erreur de retrait stock livreur : {retrait_err}")
                    continue

                # ✅ ENRICHISSEMENT
                livreur = UserModel.find_by_id(livreur_id)
                acteur_annul = {
                    "livreur_id": str(livreur_id),
                    "livreur_nom": f"{livreur.get('first_name', '')} {livreur.get('name', '')}".strip() if livreur else "Inconnu"
                }
                
                # Rapatrier dans le stock principal
                type_mvt = "annulation_approvisionnement" if quantite_a_retirer == quantite_appro else "annulation_approvisionnement_partielle"
                ProduitModel.increment_stock(
                    produit_id=str(produit_id_obj),
                    quantite=quantite_a_retirer,
                    acteur=acteur_annul,
                    type_mouvement=type_mvt,
                )

            except Exception as e:
                print(f"⛔ Erreur inattendue pour article {produit.get('produit_id')} : {e}")
                continue

        # Supprimer l'approvisionnement même partiellement traité (soft delete)
        ApprovisionnementModel.get_collection().update_one(
            {"_id": ObjectId(approvisionnement["_id"])},
            {"$set": {"deleted_at": datetime.utcnow()}}
        )

        return {"message": "Approvisionnement supprimé (restant récupéré si possible)"}, 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"message": f"Erreur lors de la suppression : {str(e)}"}, 500





# détails d'un approvisionnement
def get_approvisionnement_details_service(approvisionnement_id):
    """
    Récupère les détails d'un approvisionnement spécifique.
    """
    approvisionnement = ApprovisionnementModel.get_by_id(approvisionnement_id)
    
    if not approvisionnement:
        return {"message": "Approvisionnement introuvable"}, 404
    
    # Sérialisation des données
    approvisionnement_details = serialize_approvisionnement(approvisionnement)
    #print("approvionnement : ", approvisionnement_details)
    
    
    return approvisionnement_details, 200



def update_approvisionnement_service(approvisionnement_id, data):
    """
    Modifie un approvisionnement existant.
    - Calcule la différence (delta) entre les anciennes et nouvelles quantités.
    - Met à jour le stock magasin et le stock livreur.
    - Enregistre les nouvelles données.
    """
    try:
        approvisionnement = ApprovisionnementModel.get_by_id(approvisionnement_id)
        if not approvisionnement:
            return {"message": "Approvisionnement introuvable"}, 404
            
        validated_data = ApprovisionnementSchema().load(data)
        erreur_serie = _refus_articles_suivis(validated_data["produits"])
        if erreur_serie:
            return erreur_serie
        
        anciens_produits_list = approvisionnement.get("produits", [])
        anciens_produits = {str(p["produit_id"]): p["quantite"] for p in anciens_produits_list}
        
        nouveaux_produits_list = validated_data["produits"]
        nouveaux_produits = {str(p["produit_id"]): p["quantite"] for p in nouveaux_produits_list}
        
        old_livreur_id = str(approvisionnement["livreur_id"])
        new_livreur_id = str(validated_data["livreur_id"])
        admin_id = validated_data["admin_id"]
        
        # ✅ CORRECTION : Si le livreur a changé, on doit transférer le stock existant de l'ancien vers le nouveau
        if old_livreur_id != new_livreur_id:
            # Vérifier si l'ancien livreur a encore le stock qu'on lui avait donné
            for p_id, anc_q in anciens_produits.items():
                stock_anc_livreur = StockLivreurModel.get_produit_quantite(old_livreur_id, p_id)
                if stock_anc_livreur < anc_q:
                    return {"error": f"Impossible de changer de livreur car l'ancien livreur a déjà écoulé une partie du stock. (Article {p_id} : reste {stock_anc_livreur}, besoin de retirer {anc_q})"}, 400
            
            # Transfert du stock de l'ancien vers le nouveau
            for p_id, anc_q in anciens_produits.items():
                StockLivreurModel.retirer_du_stock(old_livreur_id, p_id, anc_q)
                StockLivreurModel.ajouter_stock(new_livreur_id, [{"produit_id": p_id, "quantite": anc_q}])
            
            # Maintenant le livreur de référence pour les deltas est le nouveau
            livreur_id = new_livreur_id
        else:
            livreur_id = old_livreur_id

        # Validation des stocks pour les produits (Deltas)
        magasin_reference = None
        for p_id, nouv_q in nouveaux_produits.items():
            anc_q = anciens_produits.get(p_id, 0)
            delta = nouv_q - anc_q
            
            produit_obj = ProduitModel.get_by_id(p_id)
            if not produit_obj:
                return {"error": f"Article {p_id} introuvable"}, 400
                
            if magasin_reference is None:
                magasin_reference = str(produit_obj["magasin_id"])
            elif str(produit_obj["magasin_id"]) != magasin_reference:
                return {"error": f"Tous les articles doivent appartenir au même magasin."}, 400
                
            if delta > 0:
                if produit_obj["quantite"] < delta:
                    return {"error": f"Stock insuffisant pour l'article '{produit_obj.get('nom', 'Inconnu')}' (disponible: {produit_obj['quantite']}, besoin supplémentaire: {delta})"}, 400
                    
            if delta < 0:
                retrait = abs(delta)
                stock_livreur_actuel = StockLivreurModel.get_produit_quantite(livreur_id, p_id)
                if stock_livreur_actuel < retrait:
                    return {"error": f"Impossible de réduire la quantité car le livreur a déjà écoulé ce stock. (Disponible chez livreur: {stock_livreur_actuel}, à retirer: {retrait})"}, 400
                    
        # Vérifier aussi les produits qui ont été supprimés de la liste
        for p_id, anc_q in anciens_produits.items():
            if p_id not in nouveaux_produits:
                stock_livreur_actuel = StockLivreurModel.get_produit_quantite(livreur_id, p_id)
                if stock_livreur_actuel < anc_q:
                     return {"error": f"Impossible de retirer l'article car le livreur a déjà écoulé ce stock. (Disponible chez livreur: {stock_livreur_actuel}, à retirer: {anc_q})"}, 400
        
        # Execution des modifications
        # ... (le reste du code utilise 'livreur_id' qui pointe maintenant vers le bon livreur)
        acteur = {"admin_id": admin_id}
        tous_ids = set(anciens_produits.keys()).union(set(nouveaux_produits.keys()))
        
        for p_id in tous_ids:
            anc_q = anciens_produits.get(p_id, 0)
            nouv_q = nouveaux_produits.get(p_id, 0)
            delta = nouv_q - anc_q
            
            if delta > 0:
                # ✅ ENRICHISSEMENT
                livreur = UserModel.find_by_id(livreur_id)
                acteur_mod = {
                    "admin_id": admin_id,
                    "livreur_id": str(livreur_id),
                    "livreur_nom": f"{livreur.get('first_name', '')} {livreur.get('name', '')}".strip() if livreur else "Inconnu"
                }
                ProduitModel.decrement_stock(
                    produit_id=p_id,
                    quantite=delta,
                    acteur=acteur_mod,
                    type_mouvement="modification_approvisionnement_ajout"
                )
                StockLivreurModel.ajouter_stock(livreur_id, [{"produit_id": p_id, "quantite": delta}])
            elif delta < 0:
                retrait = abs(delta)
                # ✅ ENRICHISSEMENT
                livreur = UserModel.find_by_id(livreur_id)
                acteur_retrait = {
                    "livreur_id": str(livreur_id),
                    "livreur_nom": f"{livreur.get('first_name', '')} {livreur.get('name', '')}".strip() if livreur else "Inconnu"
                }
                ProduitModel.increment_stock(
                    produit_id=p_id,
                    quantite=retrait,
                    acteur=acteur_retrait,
                    type_mouvement="modification_approvisionnement_retrait"
                )
                StockLivreurModel.retirer_du_stock(livreur_id, p_id, retrait)
                
        # Mettre à jour l'enregistrement d'approvisionnement
        nv_produits_format = [
            {
                "produit_id": ObjectId(p_id),
                "quantite": int(q)
            } for p_id, q in nouveaux_produits.items()
        ]
        
        ApprovisionnementModel.get_collection().update_one(
            {"_id": ObjectId(approvisionnement_id)},
            {"$set": {
                "produits": nv_produits_format,
                # admin_id reste l'auteur d'origine ; on trace qui a modifié et quand.
                "modifie_par": ObjectId(admin_id),
                "updated_at": datetime.utcnow(),
                "livreur_id": ObjectId(new_livreur_id) # ✅ CORRECTION : Mettre à jour le livreur_id en base
            }}
        )
        
        return {"message": "Approvisionnement modifié avec succès"}, 200
        
    except ValidationError as err:
        return {"error": err.messages}, 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"message": f"Erreur lors de la modification : {str(e)}"}, 500


def get_corbeille_approvisionner_service(magasin_id):
    """
    Retourne tous les approvisionnements supprimés du magasin.
    """
    if not magasin_id:
        return [], 200
    approvisionnement_data = ApprovisionnementModel.get_corbeille_by_magasin(magasin_id)
    
    approvisionnement_data_tries = sorted(
        approvisionnement_data,
        key=lambda l:l.get("deleted_at", l.get("created_at", datetime.min)), 
        reverse=True
    )
    approvisionnement_nettoyees = [serialize_approvisionnement(a) for a in approvisionnement_data_tries]
    
    return approvisionnement_nettoyees, 200


def restaurer_approvisionnement_service(approvisionnement_id):
    """
    Restaure un approvisionnement :
    - Récupère les quantités dans le magasin pour le restituer au livreur (si disponible)
    - Enlève la marque deleted_at
    """
    try:
        approvisionnement = ApprovisionnementModel.get_collection().find_one({
            "_id": ObjectId(approvisionnement_id),
            "deleted_at": {"$exists": True}
        })

        if not approvisionnement:
            return {"message": "Approvisionnement introuvable dans la corbeille"}, 404

        produits = approvisionnement.get("produits", [])
        livreur_id = str(approvisionnement.get("livreur_id"))
        admin_id = str(approvisionnement.get("admin_id"))

        # Transformer en dictionnaire pour validation
        produits_format = [
            {"produit_id": str(p["produit_id"]), "quantite": p["quantite"]}
            for p in produits
        ]

        is_valid, error = verifier_produits_approvisionnement_valide(produits_format)
        if not is_valid:
            return {"error": error}, 400

        # Mettre à jour les stocks
        StockLivreurModel.ajouter_stock(livreur_id, produits_format)

        # ✅ ENRICHISSEMENT
        livreur = UserModel.find_by_id(livreur_id)
        acteur_rest = {
            "admin_id": admin_id,
            "livreur_id": str(livreur_id),
            "livreur_nom": f"{livreur.get('first_name', '')} {livreur.get('name', '')}".strip() if livreur else "Inconnu"
        }
        for produit in produits_format:
            ProduitModel.decrement_stock(
                produit_id=produit["produit_id"],
                quantite=produit["quantite"],
                acteur=acteur_rest,
                type_mouvement="restauration_approvisionnement"
            )

        # Enlever la marque deleted_at
        ApprovisionnementModel.get_collection().update_one(
            {"_id": ObjectId(approvisionnement["_id"])},
            {"$unset": {"deleted_at": ""}}
        )

        return {"message": "Approvisionnement restauré avec succès"}, 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"message": f"Erreur lors de la restauration : {str(e)}"}, 500

def supprimer_definitivement_approvisionnement_service(approvisionnement_id):
    """
    Supprime définitivement un approvisionnement de la corbeille.
    """
    try:
        approvisionnement = ApprovisionnementModel.get_collection().find_one({
            "_id": ObjectId(approvisionnement_id),
            "deleted_at": {"$exists": True}
        })

        if not approvisionnement:
            return {"message": "Approvisionnement introuvable dans la corbeille"}, 404

        # Suppression définitive
        ApprovisionnementModel.delete_permanently(approvisionnement_id)

        return {"message": "Approvisionnement supprimé définitivement avec succès"}, 200

    except Exception as e:
        return {"message": f"Erreur lors de la suppression définitive : {str(e)}"}, 500


