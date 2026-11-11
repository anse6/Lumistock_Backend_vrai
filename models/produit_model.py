from bson import ObjectId
from datetime import datetime
from extensions import db
import os


def cle_ean13(douze_chiffres):
    """Chiffre de contrôle d'un EAN-13 à partir de ses 12 premiers chiffres."""
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(douze_chiffres))
    return str((10 - total % 10) % 10)


def normaliser_code_barre(code):
    """Code tel qu'il est enregistré : sans espaces autour, None si vide."""
    if code is None:
        return None
    code = str(code).strip()
    return code or None


def variantes_code_barre(code):
    """Le code et ses écritures équivalentes (UPC-A 12 chiffres = EAN-13 avec un 0 devant)."""
    code = normaliser_code_barre(code)
    if not code:
        return []
    variantes = [code]
    if code.isdigit() and len(code) == 12:
        variantes.append("0" + code)
    if code.isdigit() and len(code) == 13 and code.startswith("0"):
        variantes.append(code[1:])
    return variantes


class ProduitModel:
    
    
    @staticmethod
    def get_collection():
        return db.tables.produits
    
    
    @staticmethod
    def create_produit(data, image_filename=None):
        quantite = int(data["quantite"])
        
        #  Vérifier si un produit avec le même nom existe déjà dans ce magasin
        if ProduitModel.nom_existe(data["magasin_id"], data["nom"]):
            raise ValueError("Cet article existe déjà dans ce magasin")

        prix_unitaire = float(data["prix"])
        
        # Conditionnements : vérifiés (nom, facteur ≥ 2, prix > 0, pas sous le coût).
        from services.conditionnements import normaliser
        conditionnement_nettoyes = normaliser(data.get("conditionnements") or [], prix_unitaire,
                                              float(data.get("prix_achat", 0) or 0))

        def safe_oid(val):
            if val and val not in ["undefined", "null", ""] and ObjectId.is_valid(val):
                return ObjectId(val)
            return None

        description = data.get("description", "")
        if description in ["undefined", "null"]:
            description = ""

        produit = {
            "nom": data["nom"],
            "description": description,
            "image": image_filename,
            "prix": float(data["prix"]),
            # Le frontend historique n'envoie pas toujours ce champ.
            "prix_achat": float(data.get("prix_achat", 0) or 0),
            "dernier_prix_achat": float(data.get("prix_achat", 0) or 0),
            "unite": data["unite"],  # e.g., "kg", "litre"
            "quantite": quantite,
            "quantite_initiale": quantite,  # pour traçabilité/statistique
            "etat": "en stock" if quantite > 0 else "en rupture",
            "categorie_id": safe_oid(data.get("categorie_id")),
            "fournisseur_id": safe_oid(data.get("fournisseur_id")),
            "magasin_id": safe_oid(data.get("magasin_id")),
            "mouvements_stock": [],  # traçabilité des mouvements
            "conditionnements": conditionnement_nettoyes,  # conditionnements
            "code_barre": normaliser_code_barre(data.get("code_barre")),
            "date_peremption": data.get("date_peremption"),
            "seuil_alerte": int(data.get("seuil_alerte")) if data.get("seuil_alerte") else None,
            "reference_fournisseur": data.get("reference_fournisseur"),
            # Lot d'achat initial pour la valorisation des stocks (CMUP/FIFO/LIFO)
            "lots": ProduitModel._lots_initiaux(quantite, float(data.get("prix_achat", 0) or 0)),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()  #
            
        }
        result = ProduitModel.get_collection().insert_one(produit)
        return str(result.inserted_id)
    
    
    # Récupérer tous les produits d'un magasin   
    @staticmethod
    def get_all_by_magasin(magasin_id):
        return list(ProduitModel.get_collection().find({"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}}))


    @staticmethod
    def get_conditionnement_by_id(produit_id, conditionnement_id):
        """
        Récupère un conditionnement spécifique à partir de son ID pour un produit donné.
        """
        produit = ProduitModel.get_collection().find_one({"_id": ObjectId(produit_id)})
        if not produit:
            return None

        for cond in produit.get("conditionnements", []):
            if str(cond["_id"]) == str(conditionnement_id):
                return cond

        return None


    @staticmethod
    def _lots_initiaux(quantite, prix_achat, origine="stock initial"):
        from services.valorisation_service import nouveau_lot
        return [nouveau_lot(quantite, prix_achat, origine)] if float(quantite or 0) > 0 else []

    @staticmethod
    def get_for_magasin(produit_id, magasin_id, in_corbeille=False):
        """Retourne le produit seulement s'il appartient au magasin indiqué.

        :param in_corbeille: False = produit actif, True = produit à la corbeille.
        Un produit d'un autre magasin est traité comme inexistant (None).
        """
        if not ObjectId.is_valid(str(produit_id)) or not magasin_id:
            return None
        produit = ProduitModel.get_collection().find_one({
            "_id": ObjectId(str(produit_id)),
            "magasin_id": ObjectId(str(magasin_id)),
            "deleted_at": {"$exists": in_corbeille},
        })
        return produit

    @staticmethod
    def nom_existe(magasin_id, nom, exclude_id=None):
        produit = ProduitModel.get_collection().find_one({"nom": nom, "magasin_id": ObjectId(str(magasin_id))})
        return bool(produit) and str(produit["_id"]) != str(exclude_id)

    @staticmethod
    def code_barre_existe(magasin_id, code_barre, exclude_id=None, exclude_cond_id=None):
        """Un code-barre est unique dans un magasin : produits actifs et cartons
        (conditionnements) compris. exclude_id / exclude_cond_id : l'élément
        que l'on est en train de modifier."""
        code = normaliser_code_barre(code_barre)
        if not code:
            return False
        for produit in ProduitModel.get_all_by_magasin(magasin_id):
            meme_produit = str(produit["_id"]) == str(exclude_id)
            if produit.get("code_barre") == code and not (meme_produit and exclude_cond_id is None):
                return True
            for cond in produit.get("conditionnements") or []:
                if cond.get("code_barre") != code:
                    continue
                if meme_produit and exclude_cond_id is not None and str(cond.get("_id")) == str(exclude_cond_id):
                    continue
                return True
        return False

    @staticmethod
    def get_by_id(produit_id):
        if not isinstance(produit_id, ObjectId):
            try:
                produit_id = ObjectId(produit_id)
            except Exception as e:
                print(f"Erreur de conversion en ObjectId : {e}")
                return None
        result = ProduitModel.get_collection().find_one({"_id": produit_id, "deleted_at": {"$exists": False}})
        
        return result



        # Mettre à jour un produit
    @staticmethod
    def update_produit(produit_id, data):
        update_data = {k: data[k] for k in data if k in ["nom", "description", "prix", "prix_achat", "unite", "quantite", "categorie_id", "fournisseur_id", "image", "code_barre", "date_peremption", "seuil_alerte", "reference_fournisseur"]}

        # Quantité et état
        if "quantite" in update_data:
            update_data["etat"] = "en stock" if int(update_data["quantite"]) > 0 else "en rupture"
            update_data["quantite"] = int(update_data["quantite"])
            # La quantité doit rester pour être mise à jour par $set
            pass
            
        if "code_barre" in update_data:
            update_data["code_barre"] = normaliser_code_barre(update_data["code_barre"])

        # Prix
        if "prix" in update_data:
            update_data["prix"] = float(update_data["prix"])
        
        # Prix Achat
        if "prix_achat" in update_data:
            update_data["prix_achat"] = float(update_data["prix_achat"])

        def safe_oid(val):
            if val and val not in ["undefined", "null", ""] and ObjectId.is_valid(val):
                return ObjectId(val)
            return None

        # Description
        if "description" in update_data:
            if update_data["description"] in ["undefined", "null"]:
                update_data["description"] = ""

        # Catégorie
        if "categorie_id" in update_data:
            update_data["categorie_id"] = safe_oid(update_data["categorie_id"])

        # Fournisseur
        if "fournisseur_id" in update_data:
            update_data["fournisseur_id"] = safe_oid(update_data["fournisseur_id"])

        # Conditionnements : vérifiés comme à la création ; un conditionnement
        # existant garde son identifiant (les ventes y font référence) et son code-barre.
        from services.conditionnements import normaliser, recalculer_references
        produit_actuel = ProduitModel.get_by_id(produit_id) or {}
        prix_unitaire = float(update_data.get("prix", produit_actuel.get("prix", 0)) or 0)
        if "conditionnements" in data:
            update_data["conditionnements"] = normaliser(
                data["conditionnements"], prix_unitaire, float(produit_actuel.get("prix_achat", 0) or 0),
                anciens=produit_actuel.get("conditionnements"))
        elif "prix" in update_data and produit_actuel.get("conditionnements"):
            update_data["conditionnements"] = recalculer_references(produit_actuel["conditionnements"], prix_unitaire)

        # Mise à jour MongoDB
        return ProduitModel.get_collection().find_one_and_update(
            {"_id": ObjectId(produit_id)},
            {"$set": update_data}
        )






    # Supprimer un produit et son image associée
    @staticmethod
    def delete_produit(produit_id):
        # Soft delete: on marque le produit comme supprimé (sera géré par TTL plus tard)
        # On ne supprime pas l'image pour permettre la restauration
        produit = ProduitModel.get_collection().find_one_and_update(
            {"_id": ObjectId(produit_id)},
            {"$set": {"deleted_at": datetime.utcnow()}},
            return_document=True
        )
        return produit



    #  Traçabilité des mouvements
    @staticmethod
    def ajouter_mouvement_stock(produit_id, mouvement):
        ProduitModel.get_collection().update_one(
            {"_id": ObjectId(produit_id)},
            {"$push": {"mouvements_stock": {
                **mouvement,
                "date": datetime.utcnow()
            }}}
        )



    @staticmethod
    def _maj_etat(produit_id):
        """Met l'état (en stock / en rupture) d'accord avec la quantité actuelle."""
        collection = ProduitModel.get_collection()
        oid = ObjectId(produit_id)
        collection.update_one({"_id": oid, "quantite": {"$gt": 0}}, {"$set": {"etat": "en stock"}})
        collection.update_one({"_id": oid, "quantite": {"$lte": 0}}, {"$set": {"etat": "en rupture"}})

    @staticmethod
    def reserver_stock(quantites):
        """Retire du stock du magasin, de façon atomique, les quantités {produit_id: quantité}.

        Chaque retrait n'a lieu que si le stock suffit ENCORE au moment de
        l'écriture : deux caisses qui vendent la dernière unité en même temps
        ne peuvent pas la vendre deux fois. Si un produit manque, ce qui a déjà
        été retiré est remis et le nom du produit manquant est renvoyé.

        Aucun mouvement n'est tracé ici : l'appelant les enregistre une fois la
        vente créée (voir ``tracer_sortie``).

        :return: None si tout est réservé, sinon le nom du produit insuffisant.
        """
        collection = ProduitModel.get_collection()
        reserves = []
        for produit_id, quantite in quantites.items():
            resultat = collection.find_one_and_update(
                {"_id": ObjectId(produit_id), "quantite": {"$gte": quantite}},
                {"$inc": {"quantite": -quantite}},
            )
            if resultat is None:
                ProduitModel.liberer_stock(dict(reserves))
                produit = collection.find_one({"_id": ObjectId(produit_id)}, {"nom": 1})
                return (produit or {}).get("nom") or "Article"
            reserves.append((produit_id, quantite))
        for produit_id, _ in reserves:
            ProduitModel._maj_etat(produit_id)
        return None

    @staticmethod
    def liberer_stock(quantites):
        """Remet en stock des quantités réservées par ``reserver_stock`` (vente abandonnée)."""
        for produit_id, quantite in quantites.items():
            ProduitModel.get_collection().update_one({"_id": ObjectId(produit_id)}, {"$inc": {"quantite": quantite}})
            ProduitModel._maj_etat(produit_id)

    @staticmethod
    def tracer_sortie(produit_id, quantite, acteur=None, type_mouvement="sortie", details=None):
        """Trace la sortie d'une quantité déjà retirée par ``reserver_stock``."""
        ProduitModel.ajouter_mouvement_stock(produit_id, {
            "type": type_mouvement,
            "quantite": -quantite,
            "date": datetime.utcnow(),
            "acteur": acteur,
            **(details or {}),
        })

    #  Décrémenter stock + tracer mouvement
    @staticmethod
    def decrement_stock(produit_id, quantite, acteur=None, type_mouvement="sortie", details=None):
        # $inc atomique : deux sorties simultanées ne s'écrasent plus
        # (avant : lecture puis écriture de la nouvelle valeur, une sortie pouvait être perdue).
        produit = ProduitModel.get_collection().find_one_and_update(
            {"_id": ObjectId(produit_id)}, {"$inc": {"quantite": -quantite}})
        if not produit:
            return None
        ProduitModel._maj_etat(produit_id)

        ProduitModel.ajouter_mouvement_stock(produit_id, {
            "type": type_mouvement,
            "quantite": -quantite,
            "date": datetime.utcnow(),
            "acteur": acteur,
            **(details or {}),
        })

        return ProduitModel.get_by_id(produit_id)

    # Incrémenter stock + tracer mouvement
    @staticmethod
    def increment_stock(produit_id, quantite, acteur=None, type_mouvement="entrée", details=None):
        produit = ProduitModel.get_collection().find_one_and_update(
            {"_id": ObjectId(produit_id)}, {"$inc": {"quantite": quantite}})
        if not produit:
            return None
        ProduitModel._maj_etat(produit_id)

        ProduitModel.ajouter_mouvement_stock(produit_id, {
            "type": type_mouvement,
            "quantite": quantite,
            "acteur": acteur,  # admin_id, manager_id, etc.
            **(details or {}),
        })

        return ProduitModel.get_by_id(produit_id)

    
    #  Récupérer les mouvements de stock d'un produit
    @staticmethod
    def get_mouvements_stock(produit_id, type_mouvement=None):
        produit = ProduitModel.get_by_id(produit_id)
        if not produit:
            return []
        mouvements = produit.get("mouvements_stock", [])
        if type_mouvement:
            mouvements = [m for m in mouvements if m.get("type") == type_mouvement]
        return mouvements



    # Supprimer les mouvements de stock d'un produit
    @staticmethod
    def delete_mouvements_stock(produit_id):
        """
        Supprime tous les mouvements de stock d'un produit.
        """
        result = ProduitModel.get_collection().update_one(
            {"_id": ObjectId(produit_id)},
            {"$unset": {"mouvements_stock": ""}}
        )
        return result.modified_count > 0
    
    
    
    
    # Récupérer les produits d'une catégorie
    @staticmethod
    def get_by_categorie(categorie_id):
        return list(ProduitModel.get_collection().find({"categorie_id": ObjectId(categorie_id), "deleted_at": {"$exists": False}}))



    # Récupérer une liste de produits à partir d'une liste d'IDs
    @staticmethod
    def get_produits_by_ids(produit_ids):
        """
        Récupère une liste de produits à partir d'une liste d'IDs (str).
        """
        # Conversion en ObjectId si nécessaire
        try:
            object_ids = [ObjectId(pid) for pid in produit_ids]
        except Exception:
            object_ids = produit_ids  # au cas où les IDs sont déjà strings utilisables

        # Requête MongoDB
        produits = ProduitModel.get_collection().find({ "_id": { "$in": object_ids }, "deleted_at": {"$exists": False} })

        # Convertir les documents en dictionnaires Python
        return list(produits)

    
    
    # Methode permettant d'importer ou de créer des produits provenant d'un fichier excel
    @staticmethod
    def create_from_excel(row, magasin_id, categorie_id):
        produit = {
            "nom": row["nom"],
            "description": row.get("description", ""),
            "prix": row["prix"],
            "prix_achat": row.get("prix_achat", 0),
            "dernier_prix_achat": row.get("prix_achat", 0),
            "unite": row["unite"],
            "quantite": row["quantite"],
            "quantite_initiale": row["quantite"],
            "etat": "en stock" if row["quantite"] > 0 else "en rupture",
            "categorie_id": ObjectId(categorie_id) if categorie_id else None,
            "magasin_id": ObjectId(magasin_id),
            "mouvements_stock": [],
            "conditionnements": [],
            "lots": ProduitModel._lots_initiaux(row["quantite"], float(row.get("prix_achat", 0) or 0), "import Excel"),
            "created_at": datetime.utcnow()
        }
        return ProduitModel.get_collection().insert_one(produit)


    @staticmethod
    def approvisionner_produit(produit_id, quantite, acteur, prix_unitaire=None, facture_id=None):
        # Un produit à la corbeille ne doit pas recevoir de stock.
        produit = ProduitModel.get_collection().find_one({"_id": ObjectId(produit_id), "deleted_at": {"$exists": False}})
        if not produit:
            return None

        nouvelle_quantite = produit["quantite"] + quantite

        # Ajout du mouvement dans la liste
        mouvement = {
            "type": "approvisionnement magasin",
            "quantite": quantite,
            "date": datetime.utcnow(),
            "acteur": acteur
        }
        if prix_unitaire is not None:
            # Prix d'achat du lot : valorise les achats en comptabilité.
            mouvement["prix_unitaire"] = float(prix_unitaire)
        if facture_id:
            # Achat à crédit : payé par les règlements de la facture fournisseur.
            mouvement["facture_id"] = str(facture_id)

        ProduitModel.get_collection().update_one(
            {"_id": ObjectId(produit_id)},
            {
                "$set": {
                    "quantite": nouvelle_quantite,
                    "etat": "en stock"
                },
                "$push": {
                    "mouvements_stock": mouvement
                }
            }
        )

        return ProduitModel.get_by_id(produit_id)
    
    
    @staticmethod
    def get_corbeille_produits(magasin_id=None):
        query = {"deleted_at": {"$exists": True}}
        if magasin_id:
            query["magasin_id"] = ObjectId(magasin_id)
        return list(ProduitModel.get_collection().find(query))

    @staticmethod
    def get_all_mouvements_by_magasin(magasin_id, start_date=None, end_date=None, type_mouvement=None):
        """
        Récupère tous les mouvements de stock de tous les produits d'un magasin.
        Utilise une agrégation MongoDB pour unwind le tableau mouvements_stock.
        """
        # ✅ CORRECTION : On inclut aussi les mouvements des produits supprimés pour les rapports complets
        match_query = { "magasin_id": ObjectId(magasin_id) }
        
        pipeline = [
            { "$match": match_query },
            { "$unwind": "$mouvements_stock" }
        ]
        
        # Filtres sur les mouvements (après unwind)
        match_movement = {}
        if start_date and end_date:
            match_movement["mouvements_stock.date"] = { "$gte": start_date, "$lte": end_date }
            
        # ✅ CORRECTION : Gérer 'tous'
        if type_mouvement and type_mouvement != "tous":
            match_movement["mouvements_stock.type"] = type_mouvement
            
        if match_movement:
            pipeline.append({ "$match": match_movement })
            
        pipeline.append({
            "$project": {
                "_id": 0,
                "produit_id": { "$toString": "$_id" },
                "nom": 1,
                "type": "$mouvements_stock.type",
                "quantite": "$mouvements_stock.quantite",
                "date": "$mouvements_stock.date",
                "acteur": "$mouvements_stock.acteur"
            }
        })
        
        pipeline.append({ "$sort": { "date": -1 } })
        
        return list(ProduitModel.get_collection().aggregate(pipeline))

    @staticmethod
    def get_by_barcode(magasin_id, barcode):
        """Produit actif du magasin portant ce code (produit seul, sans les cartons)."""
        produit, _ = ProduitModel.trouver_par_code(magasin_id, barcode)
        return produit

    @staticmethod
    def trouver_par_code(magasin_id, barcode):
        """Retrouve ce qu'un lecteur a scanné : (produit, conditionnement ou None).

        Le code est cherché sur le produit, puis sur ses cartons ; on essaie aussi
        ses variantes (UPC-A à 12 chiffres <-> EAN-13 commençant par 0)."""
        variantes = variantes_code_barre(barcode)
        if not variantes:
            return None, None
        for code in variantes:
            produit = ProduitModel.get_collection().find_one({
                "magasin_id": ObjectId(str(magasin_id)),
                "code_barre": code,
                "deleted_at": {"$exists": False},
            })
            if produit:
                return produit, None
        for produit in ProduitModel.get_all_by_magasin(magasin_id):
            for cond in produit.get("conditionnements") or []:
                if cond.get("code_barre") in variantes:
                    return produit, cond
        return None, None

    @staticmethod
    def generate_internal_barcode(magasin_id):
        """Code interne unique au format EAN-13 : « 200 » + 9 chiffres + clé.

        Les préfixes 20 à 29 sont réservés à l'usage interne des magasins : ils ne
        croisent jamais un code imprimé par un fabricant. Rien que des chiffres,
        donc lisible quel que soit le clavier (AZERTY / QWERTY) du lecteur."""
        import random
        while True:
            base = "200" + "".join(random.choices("0123456789", k=9))
            code = base + cle_ean13(base)
            if not ProduitModel.code_barre_existe(magasin_id, code):
                return code

    @staticmethod
    def delete_permanently(produit_id):
        return ProduitModel.get_collection().delete_one({"_id": ObjectId(produit_id)})
