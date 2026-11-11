# models/livraison_model.py

from bson import ObjectId
from datetime import datetime
from extensions import db
from utils.inject_magasin_id import inject_magasin_id  # Ajouté
from services.notification_service import notifier_manager_ristourne  # Ajouté
from models.ristourne_model import RistourneModel


class VenteRefusee(Exception):
    """Vente refusée après calcul des montants (rien n'a été enregistré)."""


class LivraisonModel:

    @staticmethod
    def get_collection():
        from extensions import db
        return db.tables.livraisons
    
    
    @staticmethod
    def create_livraison(data, produits):
        """
        Enregistre une livraison avec ou sans conditionnement.
        """
        try:
            data = inject_magasin_id(data)
        except Exception as e:
            return {"error": str(e)}, 401

        montant_total = 0
        quantite_totale = 0
        #print(data)

        # Calcul du montant total et de la quantité
        for produit in produits:
            quantite = int(produit["quantite"])
            prix_unitaire = float(produit["prix"])
            montant_total += quantite * prix_unitaire
            quantite_totale += quantite

        # Réductions commerciales (rabais, remise, ristourne) en cascade
        from services.reductions_service import appliquer_reductions
        montant_brut = round(montant_total, 2)
        from services.tva_service import decimales_magasin
        reductions, total_reductions = appliquer_reductions(
            data.get("reductions"), montant_brut, decimales_magasin(data.get("magasin_id")))
        montant_total = round(montant_brut - total_reductions, 2)

        # Ajouter la dette ancienne au montant total si elle existe
        dette_ancienne = float(data.get("dette_ancienne", 0) or 0)
        montant_produits = montant_total  # Montant net des produits (après réductions), hors dette

        # TVA du pays du magasin sur le montant net (jamais sur la dette ancienne)
        from services.tva_service import tva_vente
        appliquer_tva, taux_tva, montant_tva = tva_vente(data, montant_produits)
        montant_total = round(montant_produits + montant_tva + dette_ancienne, 2)

        from models.client_model import ClientModel
        client = ClientModel.get_client_by_id(data["client_id"])
        montant_en_caisse = float(client.get("montant_en_caisse", 0.0) or 0.0) if client else 0.0
        
        montant_deduit_caisse = 0.0
        nouveau_montant_en_caisse = None

        paiements = data.get("paiements", [])
        if paiements:
            montant_paye_manuel = 0.0
            # Si un seul paiement sans montant, il couvre tout le montant total
            if len(paiements) == 1 and (paiements[0].get("montant") is None or paiements[0].get("montant") == ""):
                montant_paye_manuel = montant_total
            else:
                for p in paiements:
                    m = float(p.get("montant", 0) or 0)
                    montant_paye_manuel += m
        elif "montant_paye" in data and data["montant_paye"] is not None and data["montant_paye"] != "":
            montant_paye_manuel = float(data["montant_paye"])
        else:
            if montant_en_caisse > 0:
                montant_paye_manuel = 0.0
            else:
                montant_paye_manuel = montant_total

        reste_a_payer = montant_total - montant_paye_manuel

        if montant_en_caisse > 0 and reste_a_payer > 0:
            if montant_en_caisse >= reste_a_payer:
                montant_deduit_caisse = reste_a_payer
                nouveau_montant_en_caisse = montant_en_caisse - reste_a_payer
            else:
                montant_deduit_caisse = montant_en_caisse
                nouveau_montant_en_caisse = 0.0

        montant_paye = montant_paye_manuel + montant_deduit_caisse

        relicat = data.get("relicat", 0)
        ristourne = float(data.get("ristourne", 0.0) or 0.0)
        have_ristourne = False
        #print("Montant total:", montant_total)
        #print("Montant payé:", montant_paye)
        #print("action_manager:", data.get("action_manager"))
        # Détermination du statut de paiement
        
        if montant_paye == 0 and data.get("statut_paiement") != "payé":
            statut_paiement = "non payé"
        elif 0 < montant_paye < montant_total:
            if data.get("action_manager") == True:
                ristourne = montant_total - montant_paye
                have_ristourne = True
                statut_paiement = "partiellement payé"
            else:
                statut_paiement = "partiellement payé"
        elif montant_paye <= 0 and montant_total > 0:
            # « payé » demandé sans rien encaisser : la vente était enregistrée
            # comme réglée alors qu'aucun argent n'était reçu.
            raise VenteRefusee("Aucun montant encaissé : indiquez la somme reçue, ou choisissez "
                               "« non payé » pour une vente à crédit.")
        else:
            statut_paiement = "payé"
            montant_paye = montant_total
            
            



        # Un paiement unique sans montant couvre tout : on l'inscrit pour que
        # les encaissements (caisse, rapports) le comptent.
        if len(paiements) == 1 and paiements[0].get("montant") in (None, "") and statut_paiement == "payé":
            paiements = [{**paiements[0], "montant": montant_total}]
        # Argent reçu sans détail des paiements (seulement « montant_paye », ou
        # rien du tout = payé comptant) : il est inscrit comme un paiement, sinon
        # les encaissements (caisse, rapports) ne le comptaient pas.
        elif not paiements and montant_paye_manuel > 0:
            paiements = [{"methode": data.get("methode_paiement") or "cash",
                          "montant": round(min(montant_paye_manuel, montant_total), 2)}]

        # L'argent inscrit doit être celui gardé : la monnaie rendue n'est pas un
        # encaissement. Un trop-perçu serait compté dans les encaissements
        # (rapports, caisse) sans être dû par le client.
        if montant_paye_manuel > montant_total + 0.01:
            if data.get("vendeur_role") == "caissier":
                raise VenteRefusee("Le montant encaissé dépasse le total à payer : inscrivez la somme gardée, "
                                   "la monnaie rendue n'est pas un encaissement.")
            raise VenteRefusee(f"Le montant payé ({montant_paye_manuel:g}) dépasse le total à payer "
                               f"({montant_total:g}) : inscrivez la somme gardée, sans la monnaie rendue.")

        # Client de passage (vente au comptoir) : pas de crédit possible.
        if data.get("paiement_complet_requis") and statut_paiement != "payé":
            raise VenteRefusee(
                "Vente à crédit impossible pour un client de passage : encaissez la totalité "
                "ou choisissez un client enregistré.")

        # Le crédit du client n'est débité qu'une fois la vente acceptée.
        if nouveau_montant_en_caisse is not None:
            ClientModel.update_client(data["client_id"], {"montant_en_caisse": nouveau_montant_en_caisse})

        # Détermination du statut de livraison initial
        statut_livraison = "en cours" if statut_paiement != "payé" else "livrée"

        # recupérer l'utilisateur et vérifierson role
        
        manager_id = ObjectId(data["manager_id"]) if data.get("manager_id") else None
        livreur_id = ObjectId(data["livreur_id"]) if data.get("livreur_id") else None
        livraison = {
            "livreur_id": livreur_id,
            "manager_id": manager_id,
            "magasin_id": ObjectId(data["magasin_id"]),
            "client_id": ObjectId(data["client_id"]),
            "produits": produits,
            "montant_total": montant_total,
            "montant_produits": montant_produits,   # Montant net des produits (après réductions, hors dette)
            "montant_brut": montant_brut,           # Montant des produits avant réductions
            "reductions": reductions,               # Détail : rabais, remise, ristourne
            "total_reductions": total_reductions,
            "appliquer_tva": appliquer_tva,         # TVA appliquée à la vente
            "taux_tva": taux_tva,                   # Taux du pays du magasin à la date de la vente (%)
            "montant_tva": montant_tva,             # TVA sur le montant net des produits
            "dette_ancienne": dette_ancienne,        # Dette ancienne du client (0 si aucune)
            "montant_paye": montant_paye,
            "montant_deduit_caisse": montant_deduit_caisse,
            "quantite": quantite_totale,
            "statut_paiement": statut_paiement,
            "statut_livraison": statut_livraison,
            "date_commande": datetime.utcnow(),
            "date_livraison": datetime.utcnow(),
            "relicat": relicat,
            "ristourne": ristourne,
            "retour": 0.0,  # Montant du retour
            "have_ristourne": have_ristourne,
            "have_retour": False,
            "action_manager": False,  # Indique si le manager a validé la livraison
            "methode_paiement": data.get("methode_paiement", paiements[0]["methode"] if paiements else "cash"),
            "paiements": paiements,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        # Qui a vendu (admin, manager, caissier) et dans quelle session de caisse.
        if data.get("vendeur_role"):
            livraison["vendeur_role"] = data["vendeur_role"]
        if data.get("session_caisse_id"):
            livraison["session_caisse_id"] = ObjectId(data["session_caisse_id"])

        result = LivraisonModel.get_collection().insert_one(livraison)
        if livraison["have_ristourne"]:
            notifier_manager_ristourne(result.inserted_id, livraison["client_id"], livraison["livreur_id"], livraison["montant_total"], livraison["montant_paye"], livraison["ristourne"], livraison["created_at"])
            
            # Enregistrement de la ristourne
            RistourneModel.enregistrer_ristourne(
                result.inserted_id,
                livraison["livreur_id"],
                livraison["client_id"],
                livraison["montant_total"],
                livraison["montant_paye"],
                livraison["ristourne"],
                motif="Client très fidèle",
                magasin_id=ObjectId(data["magasin_id"])
            )
        return str(result.inserted_id)


    @staticmethod
    def get_livraison_by_id(livraison_id):
        return LivraisonModel.get_collection().find_one({"_id": ObjectId(livraison_id)})

    @staticmethod
    def get_livraisons_by_livreur(livreur_id):
        return list(LivraisonModel.get_collection().find({"livreur_id": ObjectId(livreur_id), "deleted_at": {"$exists": False}}))

    @staticmethod
    def get_livraisons_by_client(client_id, livreur_id=None):
        query = {"client_id": ObjectId(client_id), "deleted_at": {"$exists": False}}
        if livreur_id:
            query["livreur_id"] = ObjectId(livreur_id)
        return list(LivraisonModel.get_collection().find(query))

    @staticmethod
    def rembourser(livraison_id):
        result = LivraisonModel.get_collection().update_one(
            {"_id" : ObjectId(livraison_id)},
            {"$set" : {"relicat" : 0}}
        )
        if result.modified_count > 0:
            return {"message" : "Remboursement client terminé avec succès"}
        else:
            return {"message" : "Aucune mise à jour effectuée."}
        
       

    @staticmethod
    def get_all_livraisons(magasin_id=None, livreur_id=None):
        if magasin_id is None:
            try:
                data = inject_magasin_id({})
                magasin_id = data["magasin_id"]
            except Exception as e:
                return {"error": str(e)}, 401
        
        query = {"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}}
        if livreur_id:
            query["livreur_id"] = ObjectId(livreur_id)
            
        return list(LivraisonModel.get_collection().find(query))

       
    @staticmethod
    def get_all_livraisons_plateforme():
        """
        Retourne toutes les livraisons de la plateforme, sans filtrer par magasin.
        """
        try:
            return list(LivraisonModel.get_collection().find({"deleted_at": {"$exists": False}}))
        except Exception as e:
            print(f"[ERREUR] Échec de récupération des livraisons plateforme : {e}")
            return []


    @staticmethod
    def update_livraison(livraison_id, data):
        update_data = {k: data[k] for k in data if k in ["statut_paiement", "statut_livraison", "date_livraison"]}
        update_data["updated_at"] = datetime.utcnow()

        return LivraisonModel.get_collection().find_one_and_update(
            {"_id": ObjectId(livraison_id)},
            {"$set": update_data}
        )

    @staticmethod
    def update_livraison_full(livraison_id, update_data):
        update_data["updated_at"] = datetime.utcnow()
        return LivraisonModel.get_collection().find_one_and_update(
            {"_id": ObjectId(livraison_id)},
            {"$set": update_data},
            return_document=True
        )

    @staticmethod
    def delete_livraison(livraison_id):
        return LivraisonModel.get_collection().find_one_and_delete({"_id": ObjectId(livraison_id)})

    @staticmethod
    def get_livraison_for_admin_or_manager(manager_id):
        return list(LivraisonModel.get_collection().find({"manager_id": ObjectId(manager_id), "deleted_at": {"$exists": False}}))

    @staticmethod
    def get_livraisons_between_dates(start_date, end_date, livreur_id=None, magasin_id=None):
        query = {
            "date_livraison": {"$gte": start_date, "$lte": end_date},
            "deleted_at": {"$exists": False}
        }
        if livreur_id:
            query["livreur_id"] = ObjectId(livreur_id)
        if magasin_id is None:
            try:
                data = inject_magasin_id({})
                magasin_id = data["magasin_id"]
            except Exception as e:
                return {"error": str(e)}, 401
        query["magasin_id"] = ObjectId(magasin_id)
        return list(LivraisonModel.get_collection().find(query))

    @staticmethod
    def ajouter_paiement(livraison_id, montant, methode=None, par=None):
        from models.client_model import ClientModel

        livraison = LivraisonModel.get_collection().find_one({"_id": ObjectId(livraison_id), "deleted_at": {"$exists": False}})
        if not livraison:
            return None

        montant_total = livraison["montant_total"]
        ancien_montant_paye = livraison.get("montant_paye", 0)

        if ancien_montant_paye >= montant_total:
            return {"message": "Le montant payé est déjà égal au montant total."}

        nouveau_montant_paye = ancien_montant_paye + montant
        surplus = 0.0

        # --- Option C : trop-versé → crédit dans montant_en_caisse du client ---
        if nouveau_montant_paye > montant_total:
            surplus = round(nouveau_montant_paye - montant_total, 2)
            nouveau_montant_paye = montant_total  # on cap au montant dû

            # Créditer le surplus dans le compte du client
            client_id = livraison.get("client_id")
            if client_id:
                client = ClientModel.get_client_by_id(client_id)
                if client:
                    ancien_credit = float(client.get("montant_en_caisse", 0) or 0)
                    nouveau_credit = round(ancien_credit + surplus, 2)
                    ClientModel.update_client(client_id, {"montant_en_caisse": nouveau_credit})
        # -----------------------------------------------------------------------

        if nouveau_montant_paye == 0:
            statut_paiement = "non payé"
        elif 0 < nouveau_montant_paye < montant_total:
            statut_paiement = "partiellement payé"
        else:
            statut_paiement = "payé"

        statut_livraison = livraison["statut_livraison"]
        if statut_paiement == "payé":
            statut_livraison = "livrée"

        update_data = {
            "montant_paye": nouveau_montant_paye,
            "statut_paiement": statut_paiement,
            "statut_livraison": statut_livraison,
            "updated_at": datetime.utcnow()
        }
        # Le versement est tracé avec sa date et son mode : il compte dans les
        # encaissements du jour où il est reçu, et la modification de la vente
        # (qui additionne les paiements) ne le perd plus.
        versement = {
            "methode": methode or livraison.get("methode_paiement") or "cash",
            "montant": round(nouveau_montant_paye - ancien_montant_paye, 2),
            "date": datetime.utcnow(),
        }
        if surplus > 0:
            versement["surplus_credite"] = surplus
        if par:
            versement["par"] = str(par)
        historique = list(livraison.get("paiements") or [])
        if not historique and ancien_montant_paye > 0:
            # Vente enregistrée sans détail des paiements : le versement initial
            # est tracé d'abord (daté de la vente), pour ne pas le perdre.
            historique.append({"methode": livraison.get("methode_paiement") or "cash",
                               "montant": round(ancien_montant_paye, 2)})
        update_data["paiements"] = historique + [versement]

        resultat = LivraisonModel.get_collection().find_one_and_update(
            {"_id": ObjectId(livraison_id)},
            {"$set": update_data},
            return_document=True
        )

        # Injecter le surplus dans le résultat pour que le service puisse l'exposer
        if resultat is not None and surplus > 0:
            resultat["surplus_credite"] = surplus

        return resultat
        
 

    #collection = db.tables.livraisons

    @staticmethod
    def get_corbeille_livraisons(magasin_id=None):
        query = {"deleted_at": {"$exists": True}}
        if magasin_id:
            query["magasin_id"] = ObjectId(magasin_id)
        return list(LivraisonModel.get_collection().find(query))

    @staticmethod
    def get_corbeille_livraisons_livreur(livreur_id):
        return list(LivraisonModel.get_collection().find({"livreur_id": ObjectId(livreur_id), "deleted_at": {"$exists": True}}))
