def modifier_livraison_by_manager_service(livraison_id, data):
    from schemas.livraison_schema import UpdateLivraisonManagerSchema
    from models.produit_model import ProduitModel
    from models.client_model import ClientModel
    from models.livraison_model import LivraisonModel

    try:
        schema = UpdateLivraisonManagerSchema()
        validated_data = schema.load(data)
    except ValidationError as err:
        return {"error": err.messages}, 400

    livraison = LivraisonModel.get_livraison_by_id(livraison_id)
    if not livraison:
        return {"error": "Livraison introuvable"}, 404

    update_fields = {}

    if "produits" in validated_data:
        produits_input = validated_data["produits"]
        produits_final = []
        nouveau_montant_produits = 0
        nouvelle_quantite_totale = 0

        # MAPPING Ancien stock pour comparaison/simulation
        stock_dispo = ProduitModel.get_all_by_magasin(livraison["magasin_id"])
        stock_map = {str(item["_id"]): item["quantite"] for item in stock_dispo}
        
        # Ajouter le stock de l'ancienne livraison dans stock_map car il sera restitué
        for old_prod in livraison.get("produits", []):
            pid = str(old_prod["produit_id"])
            stock_map[pid] = stock_map.get(pid, 0) + old_prod["quantite"]

        # 1. Valider et construire produits_final
        for produit in produits_input:
            produit_id = produit["produit_id"]
            produit_data = ProduitModel.get_by_id(produit_id)
            if not produit_data:
                return {"error": f"Produit {produit_id} introuvable"}, 404

            if "conditionnement_id" in produit and "quantite_conditionnement" in produit:
                conditionnement = ProduitModel.get_conditionnement_by_id(produit_id, produit["conditionnement_id"])
                if not conditionnement:
                    return {"error": f"Conditionnement introuvable pour le produit {produit_id}"}, 404

                quantite_reelle = int(produit["quantite_conditionnement"]) * int(conditionnement["facteur"])
                prix_unitaire = float(conditionnement["prix_conditionnement"])/ int(conditionnement["facteur"])
                
                produits_final.append({
                    "produit_id": ObjectId(produit_id),
                    "nom": produit_data["nom"],
                    "quantite": quantite_reelle,
                    "prix": prix_unitaire,
                    "conditionnement_id": produit["conditionnement_id"],
                    "facteur": int(conditionnement["facteur"]),
                    "is_conditionne": True,
                    "type_vente" : "conditionne"
                })
                nouveau_montant_produits += quantite_reelle * prix_unitaire
                nouvelle_quantite_totale += quantite_reelle
            else:
                prix_vente = float(produit.get("prix")) if "prix" in produit and produit["prix"] is not None else float(produit_data["prix"])
                quantite = int(produit["quantite"])
                produits_final.append({
                    "produit_id": ObjectId(produit_id),
                    "nom": produit_data["nom"],
                    "quantite": quantite,
                    "prix": prix_vente,
                    "is_conditionne": False
                })
                nouveau_montant_produits += quantite * prix_vente
                nouvelle_quantite_totale += quantite

        # 2. Vérifier si les produits demandés rentrent dans stock_map combiné
        for produit in produits_final:
            pid = str(produit["produit_id"])
            qte_demandee = produit["quantite"]
            if pid not in stock_map or stock_map[pid] < qte_demandee:
                return {"error": f"Quantité insuffisante pour le produit {pid} dans le magasin."}, 400

        # 3. Effectuer la restitution et la déduction réelles
        acteur = {"admin_id": str(livraison.get("manager_id", ""))}
        
        # Restitution
        for old_prod in livraison.get("produits", []):
            ProduitModel.increment_stock(
                produit_id=str(old_prod["produit_id"]),
                quantite=old_prod["quantite"],
                acteur=acteur,
                type_mouvement="restauration modif livraison"
            )
            
        # Déduction
        for new_prod in produits_final:
            ProduitModel.decrement_stock(
                produit_id=str(new_prod["produit_id"]),
                quantite=new_prod["quantite"],
                acteur=acteur,
                type_mouvement="modification livraison manager"
            )

        update_fields["produits"] = produits_final
        update_fields["montant_produits"] = nouveau_montant_produits
        update_fields["quantite"] = nouvelle_quantite_totale
    else:
        nouveau_montant_produits = livraison.get("montant_produits", livraison.get("montant_total", 0) - livraison.get("dette_ancienne", 0))

    # Gestion de la dette ancienne
    dette_ancienne = float(validated_data.get("dette_ancienne", livraison.get("dette_ancienne", 0)) or 0)
    nouveau_montant_total = nouveau_montant_produits + dette_ancienne

    update_fields["dette_ancienne"] = dette_ancienne
    update_fields["montant_total"] = nouveau_montant_total

    # Calcul du montant payé (si pas fourni on garde celui d'avant, sans le déduit de caisse)
    paiements = validated_data.get("paiements", [])
    if paiements:
        montant_paye_manuel = 0.0
        # Si un seul paiement sans montant, il couvre tout le montant total
        if len(paiements) == 1 and (paiements[0].get("montant") is None or paiements[0].get("montant") == ""):
            montant_paye_manuel = nouveau_montant_total
        else:
            for p in paiements:
                m = float(p.get("montant", 0) or 0)
                montant_paye_manuel += m
        update_fields["paiements"] = paiements
        update_fields["methode_paiement"] = paiements[0]["methode"]
    elif "montant_paye" in validated_data and validated_data["montant_paye"] is not None:
        montant_paye_manuel = float(validated_data["montant_paye"])
    else:
        montant_paye_manuel = livraison.get("montant_paye", 0) - livraison.get("montant_deduit_caisse", 0)

    # Récupérer client
    client_id = livraison["client_id"]
    client = ClientModel.get_client_by_id(client_id)
    montant_en_caisse = float(client.get("montant_en_caisse", 0.0) or 0.0) if client else 0.0

    # Restituer l'ancien montant déduit
    ancien_deduit = livraison.get("montant_deduit_caisse", 0)
    montant_en_caisse += ancien_deduit
    
    montant_deduit_caisse = 0.0
    reste_a_payer = nouveau_montant_total - montant_paye_manuel

    if montant_en_caisse > 0 and reste_a_payer > 0:
        if montant_en_caisse >= reste_a_payer:
            montant_deduit_caisse = reste_a_payer
            montant_en_caisse -= reste_a_payer
        else:
            montant_deduit_caisse = montant_en_caisse
            montant_en_caisse = 0.0
            
    # Mettre à jour le client
    ClientModel.update_client(client_id, {"montant_en_caisse": montant_en_caisse})

    nouveau_montant_paye = montant_paye_manuel + montant_deduit_caisse
    update_fields["montant_paye"] = nouveau_montant_paye
    update_fields["montant_deduit_caisse"] = montant_deduit_caisse

    # Statut paiement
    action_manager = validated_data.get("action_manager", livraison.get("action_manager", False))
    ristourne = float(validated_data.get("ristourne", livraison.get("ristourne", 0.0)) or 0.0)
    have_ristourne = livraison.get("have_ristourne", False)

    if nouveau_montant_paye == 0 and validated_data.get("statut_paiement") != "payé":
        statut_paiement = "non payé"
    elif 0 < nouveau_montant_paye < nouveau_montant_total:
        if action_manager:
            ristourne = nouveau_montant_total - nouveau_montant_paye
            have_ristourne = True
            statut_paiement = "partiellement payé"
        else:
            statut_paiement = "partiellement payé"
            if validated_data.get("statut_paiement"):
                 statut_paiement = validated_data["statut_paiement"]
    else:
        statut_paiement = "payé"
        nouveau_montant_paye = nouveau_montant_total
        update_fields["montant_paye"] = nouveau_montant_paye

    update_fields["statut_paiement"] = statut_paiement
    update_fields["ristourne"] = ristourne
    update_fields["have_ristourne"] = have_ristourne
    update_fields["action_manager"] = action_manager

    # Statut livraison
    if "statut_livraison" in validated_data:
        update_fields["statut_livraison"] = validated_data["statut_livraison"]
    else:
        update_fields["statut_livraison"] = "livrée" if statut_paiement == "payé" else "en cours"
        
    for k in ["relicat", "retour", "methode_paiement", "date_livraison"]:
        if k in validated_data:
            update_fields[k] = validated_data[k]

    # Mise à jour en base
    LivraisonModel.update_livraison_full(livraison_id, update_fields)
    
    return {"message": "Livraison modifiée avec succès", "livraison_id": str(livraison_id)}, 200

