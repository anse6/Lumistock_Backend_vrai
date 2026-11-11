# services/livraison_services.py
from services.cloture_service import erreur_vente_verrouillee
from bson import ObjectId
from datetime import datetime
from flask import has_app_context
from models.livraison_model import LivraisonModel, VenteRefusee
from models.stock_livreur_model import StockLivreurModel
from schemas.livraison_schema import LivraisonSchema, LivraisonManagerSchema, RetourLivraisonSchema
from marshmallow import ValidationError
from models.produit_model import ProduitModel
from models.client_model import ClientModel
from models.retour_model import RetourLivraisonModel
from models.user_model import UserModel
from models.ristourne_model import RistourneModel
from services import valorisation_service
from services import reductions_service
from services import tva_service
from services.lignes_vente import valider_lignes_vente, quantites_par_produit
from services import numeros_serie_service
from services.numeros_serie_service import ErreurNumeroSerie


DETTE_ANCIENNE_REFUSEE = (
    "La dette d'avant Lumistock ne s'ajoute plus à une vente (elle serait comptée deux fois) : "
    "enregistrez-la une seule fois sur la fiche du client (« Dette d'ouverture »), "
    "puis encaissez ses versements avec « Encaisser un règlement »."
)


def _reductions_modification(livraison, validated_data, update_fields, montant_lignes, produits_modifies):
    """Recalcule les réductions lors d'une modification et retourne le montant net des produits."""
    if not produits_modifies and "reductions" not in validated_data:
        return montant_lignes  # rien ne change : montant net déjà en base
    brut = montant_lignes if produits_modifies else float(
        livraison.get("montant_brut", livraison.get("montant_produits", 0)) or 0
    )
    source = validated_data["reductions"] if "reductions" in validated_data else (livraison.get("reductions") or [])
    detail, total = reductions_service.appliquer_reductions(
        source, brut, tva_service.decimales_magasin(livraison.get("magasin_id")))
    net = round(brut - total, 2)
    update_fields.update({
        "montant_brut": round(brut, 2),
        "reductions": detail,
        "total_reductions": total,
        "montant_produits": net,
    })
    return net


"""
    Cette fonction gère la création d'une livraison. Elle valide les données d'entrée à l'aide du schéma, 
    vérifie si le livreur a suffisamment de stock, crée la livraison dans la base de données, puis met à 
    jour le stock du livreur.
"""


#########################################################
# Nouvelle version prenant en charge le conditionnement #
#########################################################
def creer_livraison(data):
    """
    Crée une nouvelle livraison en vérifiant si le livreur a assez de stock.
    """
    try:
        schema = LivraisonSchema()
        validated_data = schema.load(data)
        reductions_service.valider_reductions(validated_data.get("reductions"))
        tva_service.tva_vente(validated_data, 0)  # TVA demandée : le pays du magasin doit avoir un taux
    except ValidationError as err:
        return {"error": err.messages}, 400
    except ValueError as err:
        return {"error": str(err)}, 400

    erreur_lignes = valider_lignes_vente(validated_data["produits"])
    if erreur_lignes:
        return {"error": erreur_lignes[0]}, erreur_lignes[1]
    erreur_serie = _refus_articles_suivis_gerant(validated_data["produits"])
    if erreur_serie:
        return erreur_serie
    # La dette d'avant Lumistock se gère sur le compte du client, plus dans une vente.
    if float(validated_data.get("dette_ancienne", 0) or 0) > 0:
        return {"error": DETTE_ANCIENNE_REFUSEE}, 400
    validated_data["dette_ancienne"] = 0.0

    livreur_id = validated_data["livreur_id"]
    
    # Gestion du client par défaut si non fourni
    if not validated_data.get("client_id"):
        validated_data["client_id"] = ClientModel.get_or_create_default_client(validated_data["magasin_id"])
        
    produits_input = validated_data["produits"]
    produits_final = []

    for produit in produits_input:
        produit_id = produit["produit_id"]
        produit_data = ProduitModel.get_by_id(produit_id)
        if not produit_data:
            return {"error": f"Article {produit_id} introuvable"}, 404

        # Gestion avec ou sans conditionnement
        if "conditionnement_id" in produit and "quantite_conditionnement" in produit:
            conditionnement = ProduitModel.get_conditionnement_by_id(produit_id, produit["conditionnement_id"])
            if not conditionnement:
                return {"error": f"Conditionnement introuvable pour l'article {produit_id}"}, 404

            produits_final.append({
                "produit_id": ObjectId(produit_id),
                "nom": produit_data["nom"],
                "quantite": int(produit["quantite_conditionnement"]) * int(conditionnement["facteur"]),
                "prix": float(conditionnement["prix_conditionnement"])/ int(conditionnement["facteur"]),
                "prix_achat": float(produit_data.get("prix_achat", 0)), # Nouveau : capture du prix d'achat
                "conditionnement_id": produit["conditionnement_id"],
                "conditionnement_nom": conditionnement.get("nom"),
                "facteur": int(conditionnement["facteur"]),
                "is_conditionne": True
            })
        else:
            # Priorité au prix envoyé par le frontend, sinon prix de base du produit
            prix_vente = float(produit.get("prix")) if "prix" in produit and produit["prix"] is not None else float(produit_data["prix"])
            produits_final.append({
                "produit_id": ObjectId(produit_id),
                "nom": produit_data["nom"],
                "quantite": int(produit["quantite"]),
                "prix": prix_vente,
                "prix_achat": float(produit_data.get("prix_achat", 0)), # Nouveau : capture du prix d'achat
                "is_conditionne": False
            })

    # Vérifier le stock
    ok, error = verifier_stock_livreur(livreur_id, produits_final)
    if not ok:
        return {"error": error}, 400

    # Coût d'achat de chaque ligne selon la méthode du magasin (CMUP/FIFO/LIFO)
    if has_app_context():
        valorisation_service.valoriser_lignes(produits_final, validated_data["magasin_id"])


    # Créer la livraison
    try:
        livraison_id = LivraisonModel.create_livraison(validated_data, produits_final)
    except VenteRefusee as refus:
        if has_app_context():
            valorisation_service.annuler_lignes(produits_final, "vente abandonnée")
        return {"error": str(refus)}, 400
    if isinstance(livraison_id, tuple):  # erreur renvoyée par le modèle
        return livraison_id

    # Les appels de production sont dans un contexte Flask. Le garde permet
    # aussi d'utiliser cette fonction comme helper pur dans des tests/scripts.
    try:
        StockLivreurModel.retirer_stock(livreur_id, produits_final)
    except RuntimeError as exc:
        # Un appel hors contexte Flask ne peut pas toucher la base. On le
        # tolère pour les helpers unitaires ; toute autre erreur remonte.
        if has_app_context() or "application context" not in str(exc):
            raise

    # ✅ ENRICHISSEMENT : Tracer la sortie pour l'historique détaillé (sans décompte magasin car déjà fait à l'approv)
    if has_app_context():
        client = ClientModel.get_client_by_id(validated_data["client_id"])
        livreur = UserModel.find_by_id(livreur_id)
        acteur = {
            "livreur_id": str(livreur_id),
            "livreur_nom": f"{livreur.get('first_name', '')} {livreur.get('name', '')}".strip() if livreur else "Inconnu",
            "client_id": str(validated_data["client_id"]),
            "client_nom": client.get("nom") if client else "Inconnu",
            "livraison_id": str(livraison_id)
        }

        for produit in produits_final:
            ProduitModel.ajouter_mouvement_stock(
                produit_id=produit["produit_id"],
                mouvement={
                    "type": "livraison livreur",
                    "quantite": -abs(int(produit["quantite"])),
                    "acteur": acteur
                }
            )

    return {"livraison_id": livraison_id}, 201


# Vérifie si le livreur a assez de stock pour chaque produit demandé
def verifier_stock_livreur(livreur_id, produits_demandes):
    """
    Vérifie si le livreur a assez de stock pour chaque produit demandé.
    Retourne (True, None) si tout est bon, sinon (False, message d'erreur).
    """
    stock_dispo = StockLivreurModel.get_stock_livreur(livreur_id)

    # Conversion explicite en string
    stock_map = {
        str(item["produit_id"]): int(item["quantite"])
        for item in stock_dispo if "produit_id" in item and "quantite" in item
    }

    #print("Stock_map:", stock_map)
    #print("Produits demandés:", produits_demandes)

    # Un même produit sur plusieurs lignes est additionné avant la comparaison.
    for pid, qte in quantites_par_produit(produits_demandes).items():
        if pid not in stock_map:
            return False, f"Article {pid} non disponible pour ce livreur."
        if stock_map[pid] < qte:
            return False, f"Quantité insuffisante pour l'article {pid}."

    return True, None


# Vérifie si magasin a assez de stock pour chaque produit demandé
def verifier_stock_magasin(magasin_id, produits_demandes):
    """
    Vérifie si le magasin a assez de stock pour chaque produit demandé.
    Retourne (True, None) si tout est bon, sinon (False, message d'erreur).
    """
     # Validation des données
  
    stock_dispo = ProduitModel.get_all_by_magasin(magasin_id)
    stock_map = {str(item["_id"]): item["quantite"] for item in stock_dispo}
    
    # Un même produit sur plusieurs lignes est additionné avant la comparaison.
    for pid, qte in quantites_par_produit(produits_demandes).items():
        if pid not in stock_map:
            return False, f"Article {pid} non disponible dans ce magasin."
        if stock_map[pid] <qte:
            return False, f"Quantité insuffisante pour l'article {pid}"

    return True, None



MESSAGE_SERIE_GERANT = ("« {nom} » est suivi par numéro de série : la vente par un gérant n'est pas "
                        "encore possible pour ces articles. Vendez-le depuis le magasin.")


def _refus_articles_suivis_gerant(lignes):
    for ligne in lignes:
        produit = ProduitModel.get_by_id(str(ligne.get("produit_id")))
        if numeros_serie_service.est_suivi(produit):
            return {"error": MESSAGE_SERIE_GERANT.format(nom=produit.get("nom"))}, 400
    return None


def _produits_des_lignes(lignes):
    return {str(l["produit_id"]): ProduitModel.get_by_id(str(l["produit_id"])) for l in lignes}


def construire_lignes_magasin(produits_input, prix_impose=False):
    """Lignes d'une vente depuis le stock du magasin, prêtes à enregistrer.

    :param prix_impose: True pour un caissier : le prix de la fiche produit
        s'applique toujours (un prix saisi à la main est ignoré ; les remises
        passent par les réductions, si l'admin les autorise).
    :return: (lignes, None) ou (None, (réponse d'erreur, code HTTP)).
    """
    produits_final = []
    for produit in produits_input:
        produit_id = produit["produit_id"]
        produit_data = ProduitModel.get_by_id(produit_id)
        if not produit_data:
            return None, ({"error": f"Article {produit_id} introuvable"}, 404)

        if "conditionnement_id" in produit and "quantite_conditionnement" in produit:
            conditionnement = ProduitModel.get_conditionnement_by_id(produit_id, produit["conditionnement_id"])
            if not conditionnement:
                return None, ({"error": f"Conditionnement introuvable pour « {produit_data['nom']} »"}, 404)

            produits_final.append({
                "produit_id": produit_id,
                "nom": produit_data["nom"],
                "quantite": int(produit["quantite_conditionnement"]) * int(conditionnement["facteur"]),
                "prix": float(conditionnement["prix_conditionnement"])/ int(conditionnement["facteur"]),
                "prix_achat": float(produit_data.get("prix_achat", 0)), # Nouveau
                "conditionnement_id": produit["conditionnement_id"],
                "conditionnement_nom": conditionnement.get("nom"),
                "facteur": int(conditionnement["facteur"]),
                "is_conditionne": True,
                "type_vente" : "conditionne" # unitaire
            })
        else:
            # Priorité au prix envoyé par le frontend (sauf prix imposé), sinon prix de base du produit
            prix_saisi = produit.get("prix") if not prix_impose else None
            prix_vente = float(prix_saisi) if prix_saisi is not None else float(produit_data["prix"])
            ligne = {
                "produit_id": produit_id,
                "nom": produit_data["nom"],
                "quantite": int(produit["quantite"]),
                "prix": prix_vente,
                "prix_achat": float(produit_data.get("prix_achat", 0)), # Nouveau
                "is_conditionne": False
            }
            if produit.get("numeros_serie") is not None:
                ligne["numeros_serie"] = produit["numeros_serie"]
            produits_final.append(ligne)
    return produits_final, None


def est_livraison_manager(livraison):
    """Livraison faite par un admin/manager depuis le stock du MAGASIN
    (et non par un gérant depuis son propre stock)."""
    return bool(livraison.get("manager_id")) and str(livraison.get("livreur_id")) == str(livraison.get("manager_id"))


# Nouvelle version de création d'une livraison par un manager
def creer_livraison_by_manager(data, vendeur_role=None, session_caisse_id=None, paiement_complet_requis=False):
    """
    Crée une vente depuis le stock du MAGASIN (admin, manager ou caissier).

    Le stock est réservé de façon atomique AVANT d'enregistrer la vente : si
    deux caisses vendent la dernière unité au même moment, une seule passe.
    En cas d'échec après la réservation, le stock et les lots sont remis.

    :param vendeur_role: rôle de la personne qui vend (inscrit sur la vente).
    :param session_caisse_id: session de caisse du caissier, le cas échéant.
    :param paiement_complet_requis: refuse une vente qui ne serait pas
        entièrement payée (client de passage : pas de crédit possible).
    """
    try:
        schema = LivraisonManagerSchema()
        validated_data = schema.load(data)
        reductions_service.valider_reductions(validated_data.get("reductions"))
        tva_service.tva_vente(validated_data, 0)  # TVA demandée : le pays du magasin doit avoir un taux
    except ValidationError as err:
        return {"error": err.messages}, 400
    except ValueError as err:
        return {"error": str(err)}, 400

    erreur_lignes = valider_lignes_vente(validated_data["produits"])
    if erreur_lignes:
        return {"error": erreur_lignes[0]}, erreur_lignes[1]
    # La dette d'avant Lumistock se gère sur le compte du client, plus dans une vente.
    if float(validated_data.get("dette_ancienne", 0) or 0) > 0:
        return {"error": DETTE_ANCIENNE_REFUSEE}, 400
    validated_data["dette_ancienne"] = 0.0

    manager_id = validated_data["manager_id"]
    validated_data["manager_id"] = manager_id
    validated_data["livreur_id"] = manager_id
    
    # Gestion du client par défaut si non fourni
    if not validated_data.get("client_id"):
        validated_data["client_id"] = ClientModel.get_or_create_default_client(validated_data["magasin_id"])

    produits_final, erreur = construire_lignes_magasin(validated_data["produits"])
    if erreur:
        return erreur

    # Articles suivis par numéro de série : chaque unité vendue est désignée.
    try:
        numeros_serie_service.preparer_lignes_vente(produits_final, _produits_des_lignes(produits_final))
    except ErreurNumeroSerie as err:
        return {"error": str(err)}, err.statut

    ok, error = verifier_stock_magasin(data["magasin_id"], produits_final)
    if not ok:
        return {"error": error}, 400

    # Réservation atomique du stock (voir ProduitModel.reserver_stock).
    quantites = quantites_par_produit(produits_final)
    manquant = ProduitModel.reserver_stock(quantites)
    if manquant:
        return {"error": f"Stock insuffisant pour « {manquant} » : il vient peut-être d'être vendu à une autre caisse."}, 409

    # Coût d'achat de chaque ligne selon la méthode du magasin (CMUP/FIFO/LIFO)
    valorisation_service.valoriser_lignes(produits_final, data["magasin_id"])

    validated_data["vendeur_role"] = vendeur_role
    validated_data["session_caisse_id"] = session_caisse_id
    validated_data["paiement_complet_requis"] = paiement_complet_requis

    def abandonner():
        valorisation_service.annuler_lignes(produits_final, "vente abandonnée")
        ProduitModel.liberer_stock(quantites)

    try:
        livraison_id = LivraisonModel.create_livraison(validated_data, produits_final)
    except VenteRefusee as refus:
        abandonner()
        return {"error": str(refus)}, 400
    except Exception:
        abandonner()
        raise
    if isinstance(livraison_id, tuple):  # erreur renvoyée par le modèle
        abandonner()
        return livraison_id

    # ✅ ENRICHISSEMENT : Récupérer les infos du client pour l'historique détaillé
    client = ClientModel.get_client_by_id(validated_data["client_id"])
    acteur = {
        "admin_id": manager_id,
        "client_id": str(validated_data["client_id"]),
        "client_nom": client.get("nom") if client else "Inconnu"
    }
    if vendeur_role:
        acteur["role"] = vendeur_role

    try:
        vendeur = UserModel.find_by_id(manager_id) or {}
        numeros_serie_service.vendre_lignes(
            produits_final, livraison_id,
            {"admin_id": manager_id, "admin_nom": f"{vendeur.get('first_name', '')} {vendeur.get('name', '')}".strip()},
            client_id=validated_data["client_id"], client_nom=acteur["client_nom"])
    except ErreurNumeroSerie as err:
        abandonner()
        return {"error": str(err)}, err.statut  # réponse ≥ 400 : la vente est annulée en base

    for produit in produits_final:
        ProduitModel.tracer_sortie(
            produit_id=produit["produit_id"],
            quantite=produit["quantite"],
            acteur=acteur,
            type_mouvement="vente caisse" if vendeur_role == "caissier" else "livraison manager",
        )

    return {"message": "Livraison créée avec succès", "livraison_id": livraison_id}, 201


def _tva_modification(livraison, validated_data, update_fields, montant_produits):
    """TVA d'une vente modifiée : garde le taux de la vente, sauf (dés)activation demandée."""
    source = dict(livraison)
    if "appliquer_tva" in validated_data:
        source["appliquer_tva"] = validated_data["appliquer_tva"]
    appliquer, taux, montant_tva = tva_service.tva_vente(source, montant_produits)
    update_fields["appliquer_tva"] = appliquer
    update_fields["taux_tva"] = taux
    update_fields["montant_tva"] = montant_tva
    return montant_tva


def _paiement_modification(livraison, validated_data, update_fields, montant_total, deduit_initial):
    """Montant payé, crédit client utilisé, trop-perçu et statut d'une vente modifiée.

    L'argent reçu reste acquis : si le nouveau total est plus bas que ce que
    le client a versé, la différence devient « à rembourser » (champ retour),
    comme après un retour de produits, au lieu de disparaître.

    :param deduit_initial: crédit client utilisé par la vente AVANT la
        modification (compté dans montant_paye, mais pas versé en argent).
    :return: le statut de paiement.
    """
    deja_rembourse = float(livraison.get("montant_rembourse", 0) or 0)
    paiements = validated_data.get("paiements", [])
    if paiements:
        if len(paiements) == 1 and paiements[0].get("montant") in (None, ""):
            recu = montant_total  # un paiement unique sans montant couvre tout
        else:
            # Les paiements sont l'argent reçu ; ce qui a déjà été rendu en est retiré.
            recu = sum(float(p.get("montant", 0) or 0) for p in paiements) - deja_rembourse
        update_fields["paiements"] = paiements
        update_fields["methode_paiement"] = paiements[0]["methode"]
    elif validated_data.get("montant_paye") is not None:
        recu = float(validated_data["montant_paye"])
    else:
        # Rien de nouveau : l'argent gardé plus le trop-perçu pas encore rendu.
        recu = (float(livraison.get("montant_paye", 0) or 0) - deduit_initial
                + float(livraison.get("retour", 0) or 0))
    recu = max(round(recu, 2), 0.0)

    # Le crédit déjà utilisé par la vente est rendu au client, puis repris si besoin.
    client_id = livraison["client_id"]
    client = ClientModel.get_client_by_id(client_id)
    credit = float((client or {}).get("montant_en_caisse", 0) or 0)
    credit += float(livraison.get("montant_deduit_caisse", 0) or 0)

    deduit = 0.0
    if recu >= montant_total:
        trop_percu = round(recu - montant_total, 2)
        montant_paye = montant_total
    else:
        trop_percu = 0.0
        deduit = round(min(credit, montant_total - recu), 2)
        credit -= deduit
        montant_paye = round(recu + deduit, 2)
    if client:
        ClientModel.update_client(client_id, {"montant_en_caisse": round(credit, 2)})

    # Statut calculé UNIQUEMENT par le backend : le frontend ne peut jamais
    # forcer « payé » si le montant payé est inférieur au total.
    action_manager = validated_data.get("action_manager", livraison.get("action_manager", False))
    ristourne = float(validated_data.get("ristourne", livraison.get("ristourne", 0.0)) or 0.0)
    have_ristourne = livraison.get("have_ristourne", False)
    if montant_paye <= 0:
        statut_paiement = "non payé"
    elif montant_paye < montant_total:
        statut_paiement = "partiellement payé"
        if action_manager:
            ristourne = round(montant_total - montant_paye, 2)
            have_ristourne = True
    else:
        statut_paiement = "payé"

    update_fields.update({
        "montant_paye": montant_paye,
        "montant_deduit_caisse": deduit,
        "retour": trop_percu,
        "statut_paiement": statut_paiement,
        "ristourne": ristourne,
        "have_ristourne": have_ristourne,
        "action_manager": action_manager,
    })
    return statut_paiement


def _lignes_modifiees(produits_input, lignes_avant=None):
    """Lignes d'une vente modifiée : (lignes, montant brut, quantité) ou (None, erreur).

    Un conditionnement déjà présent dans la vente garde le prix et le nombre
    d'unités de la vente d'origine : changer ensuite le prix du carton sur la
    fiche (ou le supprimer) ne modifie pas une vente passée."""
    avant = {(str(l.get("produit_id")), str(l.get("conditionnement_id"))): l
             for l in (lignes_avant or []) if l.get("is_conditionne") and l.get("conditionnement_id")}
    lignes, montant, quantite_totale = [], 0.0, 0
    for produit in produits_input:
        produit_id = produit["produit_id"]
        produit_data = ProduitModel.get_by_id(produit_id)
        if not produit_data:
            return None, ({"error": f"Article {produit_id} introuvable"}, 404)
        ligne = {
            "produit_id": ObjectId(produit_id),
            "nom": produit_data["nom"],
            "prix_achat": float(produit_data.get("prix_achat", 0)),
        }
        if "conditionnement_id" in produit and "quantite_conditionnement" in produit:
            origine = avant.get((str(produit_id), str(produit["conditionnement_id"])))
            if origine:
                facteur = int(origine.get("facteur") or 1)
                prix_unitaire = float(origine.get("prix") or 0)
                nom_cond = origine.get("conditionnement_nom")
            else:
                conditionnement = ProduitModel.get_conditionnement_by_id(produit_id, produit["conditionnement_id"])
                if not conditionnement:
                    return None, ({"error": f"Conditionnement introuvable pour « {produit_data['nom']} »"}, 404)
                facteur = int(conditionnement["facteur"])
                prix_unitaire = float(conditionnement["prix_conditionnement"]) / facteur
                nom_cond = conditionnement.get("nom")
            ligne.update({
                "quantite": int(produit["quantite_conditionnement"]) * facteur,
                "prix": prix_unitaire,
                "conditionnement_id": produit["conditionnement_id"],
                "conditionnement_nom": nom_cond,
                "facteur": facteur,
                "is_conditionne": True,
                "type_vente": "conditionne",
            })
        else:
            prix = produit.get("prix")
            ligne.update({
                "quantite": int(produit["quantite"]),
                "prix": float(prix) if prix is not None else float(produit_data["prix"]),
                "is_conditionne": False,
            })
            if produit.get("numeros_serie") is not None:
                ligne["numeros_serie"] = produit["numeros_serie"]
        lignes.append(ligne)
        montant += ligne["quantite"] * ligne["prix"]
        quantite_totale += ligne["quantite"]
    return (lignes, montant, quantite_totale), None


def _remplacer_lignes_stock(livraison, anciennes, nouvelles, depuis_magasin):
    """Rend au stock les anciennes lignes de la vente puis y prend les nouvelles.

    Le stock est celui du magasin (vente d'un admin, manager ou caissier) ou
    celui du gérant qui a fait la vente. Retourne une erreur si le stock ne
    suffit pas (en comptant ce que la vente rend).
    """
    if depuis_magasin:
        disponible = {str(p["_id"]): p["quantite"] for p in ProduitModel.get_all_by_magasin(livraison["magasin_id"])}
        ou = "dans le magasin"
    else:
        disponible = {str(p["produit_id"]): p["quantite"]
                      for p in StockLivreurModel.get_stock_livreur(livraison["livreur_id"]) if "produit_id" in p}
        ou = "dans votre stock"
    for ligne in anciennes:
        pid = str(ligne["produit_id"])
        disponible[pid] = disponible.get(pid, 0) + ligne["quantite"]
    for pid, demande in quantites_par_produit(nouvelles).items():
        if disponible.get(pid, 0) < demande:
            return {"error": f"Quantité insuffisante pour l'article {pid} {ou}."}, 400

    produits_par_id = _produits_des_lignes(list(anciennes) + list(nouvelles))
    if depuis_magasin:
        try:
            client_vente = ClientModel.get_client_by_id(livraison["client_id"]) or {}
            numeros_serie_service.modifier_lignes(
                anciennes, nouvelles, produits_par_id, livraison["_id"],
                {"admin_id": livraison.get("manager_id")},
                client_id=livraison["client_id"], client_nom=client_vente.get("nom"))
        except ErreurNumeroSerie as err:
            return {"error": str(err)}, err.statut
    elif any(numeros_serie_service.est_suivi(p) for p in produits_par_id.values()):
        nom = next(p.get("nom") for p in produits_par_id.values() if numeros_serie_service.est_suivi(p))
        return {"error": MESSAGE_SERIE_GERANT.format(nom=nom)}, 400

    valorisation_service.annuler_lignes(anciennes, "modification livraison")
    valorisation_service.valoriser_lignes(nouvelles, livraison.get("magasin_id"))

    client = ClientModel.get_client_by_id(livraison["client_id"]) or {}
    if depuis_magasin:
        acteur = {"admin_id": str(livraison.get("manager_id", "")), "client_id": str(livraison["client_id"]),
                  "client_nom": client.get("nom") or "Inconnu"}
        for ligne in anciennes:
            ProduitModel.increment_stock(produit_id=str(ligne["produit_id"]), quantite=ligne["quantite"],
                                         acteur=acteur, type_mouvement="restauration modif livraison")
        for ligne in nouvelles:
            ProduitModel.decrement_stock(produit_id=str(ligne["produit_id"]), quantite=ligne["quantite"],
                                         acteur=acteur, type_mouvement="modification livraison manager")
        return None

    StockLivreurModel.ajouter_stock(livraison["livreur_id"], anciennes)
    StockLivreurModel.retirer_stock(livraison["livreur_id"], nouvelles)
    livreur = UserModel.find_by_id(livraison["livreur_id"])
    acteur = {
        "livreur_id": str(livraison["livreur_id"]),
        "livreur_nom": f"{livreur.get('first_name', '')} {livreur.get('name', '')}".strip() if livreur else "Inconnu",
        "client_id": str(livraison["client_id"]),
        "client_nom": client.get("nom") or "Inconnu",
        "livraison_id": str(livraison["_id"]),
    }
    # L'historique du produit montre la reprise des anciennes quantités, puis
    # la nouvelle sortie (sinon il paraîtrait sortir deux fois).
    for ligne in anciennes:
        ProduitModel.ajouter_mouvement_stock(produit_id=ligne["produit_id"], mouvement={
            "type": "reprise modification livraison livreur", "quantite": abs(int(ligne["quantite"])), "acteur": acteur})
    for ligne in nouvelles:
        ProduitModel.ajouter_mouvement_stock(produit_id=ligne["produit_id"], mouvement={
            "type": "modification livraison livreur", "quantite": -abs(int(ligne["quantite"])), "acteur": acteur})
    return None


def modifier_livraison_service(livraison_id, data, depuis_magasin):
    """Modifie une vente : client, produits, réductions, TVA, paiements.

    :param depuis_magasin: True pour une vente faite depuis le stock du
        magasin (admin, manager, caissier), False pour une vente de gérant
        (son propre stock).
    """
    from schemas.livraison_schema import UpdateLivraisonManagerSchema

    try:
        validated_data = UpdateLivraisonManagerSchema().load(data)
        reductions_service.valider_reductions(validated_data.get("reductions"))
    except ValidationError as err:
        return {"error": err.messages}, 400
    except ValueError as err:
        return {"error": str(err)}, 400

    livraison = LivraisonModel.get_livraison_by_id(livraison_id)
    if not livraison:
        return {"error": "Livraison introuvable"}, 404
    erreur_cloture = erreur_vente_verrouillee(livraison)
    if erreur_cloture:
        return erreur_cloture
    if validated_data.get("appliquer_tva") and not livraison.get("taux_tva") \
            and tva_service.tva_du_magasin(livraison.get("magasin_id"))["taux"] is None:
        return {"error": "TVA impossible : le pays du magasin n'est pas renseigné ou son taux est inconnu."}, 400

    update_fields = {}
    # Crédit client utilisé par la vente avant modification (lu avant un
    # éventuel changement de client, qui le remet à zéro).
    deduit_initial = float(livraison.get("montant_deduit_caisse", 0) or 0)

    # Changement de client : le crédit de son compte utilisé par la vente est
    # rendu à l'ancien client ; le nouveau paiera avec le sien si besoin.
    ancien_client_id = str(livraison["client_id"])
    nouveau_client_id = validated_data.get("client_id", ancien_client_id)
    if nouveau_client_id != ancien_client_id:
        if deduit_initial > 0:
            ancien = ClientModel.get_client_by_id(ancien_client_id)
            if ancien:
                ClientModel.update_client(ancien_client_id, {
                    "montant_en_caisse": float(ancien.get("montant_en_caisse", 0) or 0) + deduit_initial})
        livraison["client_id"] = ObjectId(nouveau_client_id)
        livraison["montant_deduit_caisse"] = 0.0
        update_fields["client_id"] = ObjectId(nouveau_client_id)

    if "produits" in validated_data:
        erreur_lignes = valider_lignes_vente(validated_data["produits"])
        if erreur_lignes:
            return {"error": erreur_lignes[0]}, erreur_lignes[1]
        resultat, erreur = _lignes_modifiees(validated_data["produits"], livraison.get("produits"))
        if erreur:
            return erreur
        produits_final, nouveau_montant_produits, nouvelle_quantite_totale = resultat
        erreur = _remplacer_lignes_stock(livraison, livraison.get("produits", []), produits_final, depuis_magasin)
        if erreur:
            return erreur
        update_fields["produits"] = produits_final
        update_fields["montant_produits"] = nouveau_montant_produits
        update_fields["quantite"] = nouvelle_quantite_totale
    else:
        nouveau_montant_produits = float(livraison.get("montant_produits", 0) or 0)

    try:
        nouveau_montant_produits = _reductions_modification(
            livraison, validated_data, update_fields, nouveau_montant_produits, "produits" in validated_data
        )
    except ValueError as err:
        return {"error": str(err)}, 400

    # Anciennes ventes : la dette incluse peut être baissée ou retirée, pas augmentée.
    dette_ancienne = float(livraison.get("dette_ancienne", 0.0) or 0.0)
    if "dette_ancienne" in validated_data:
        demandee = float(validated_data["dette_ancienne"] or 0.0)
        if demandee > dette_ancienne + 0.005:
            return {"error": DETTE_ANCIENNE_REFUSEE}, 400
        dette_ancienne = demandee

    montant_tva = _tva_modification(livraison, validated_data, update_fields, nouveau_montant_produits)
    # Les frais de retour retenus restent dus par le client.
    nouveau_montant_total = round(nouveau_montant_produits + montant_tva + dette_ancienne
                                  + float(livraison.get("frais_retour", 0) or 0), 2)
    update_fields["dette_ancienne"] = dette_ancienne
    update_fields["montant_total"] = nouveau_montant_total

    statut_paiement = _paiement_modification(
        livraison, validated_data, update_fields, nouveau_montant_total, deduit_initial)
    # Ancien champ, gardé pour les écrans et exports qui le lisent encore :
    # il suit le paiement (l'état de la vente est donné par etat_vente).
    update_fields["statut_livraison"] = "livrée" if statut_paiement == "payé" else "en cours"

    for k in ["relicat", "methode_paiement", "date_livraison"]:
        if k in validated_data:
            update_fields[k] = validated_data[k]

    LivraisonModel.update_livraison_full(livraison_id, update_fields)
    return {"message": "Livraison modifiée avec succès", "livraison_id": str(livraison_id)}, 200


def modifier_livraison_by_manager_service(livraison_id, data):
    """Vente faite depuis le stock du magasin (admin, manager, caissier)."""
    return modifier_livraison_service(livraison_id, data, depuis_magasin=True)


def modifier_livraison_by_livreur_service(livraison_id, data):
    """Vente faite par un gérant depuis son propre stock."""
    return modifier_livraison_service(livraison_id, data, depuis_magasin=False)


ETATS_VENTE = {
    "validee": "Validée",
    "retour_partiel": "Retour partiel",
    "retournee": "Retournée",
    "annulee": "Annulée",
}


def etat_vente(livraison):
    """État de la vente (cycle de vie du document, comme dans Odoo).

    Indépendant du paiement : une vente validée peut être non payée,
    partiellement payée ou payée. Les marchandises sortent du stock à la
    validation : il n'y a pas d'étape « en cours de livraison ».
    """
    if livraison.get("deleted_at"):
        return "annulee"
    if livraison.get("have_retour"):
        restant = sum(int(p.get("quantite", 0) or 0) for p in livraison.get("produits") or [])
        return "retournee" if restant == 0 else "retour_partiel"
    return "validee"


def _iso_utc(valeur):
    """Date UTC naïve de la base -> texte ISO avec « Z » (déjà texte : inchangé)."""
    if isinstance(valeur, datetime):
        return valeur.isoformat() + ("Z" if valeur.tzinfo is None else "")
    return str(valeur) if valeur is not None and not isinstance(valeur, str) else valeur


def serialize_livraison(livraison):
    # Récupération des informations du client
    client_data = ClientModel.get_client_by_id(livraison["client_id"])
    if client_data:
        client_data.pop("_id", None)  # Supprimer l'ID
    # Récupérer le nom du livreur
    
    livreur_data = UserModel.get_user_by_id(livraison["livreur_id"])
    if livreur_data:
        livreur_data.pop("_id", None)  # Supprimer l'ID
    # extraire juste le nom du livreur
    nom_livreur = livreur_data.get("name", "") + " " + livreur_data.get("first_name", "") if livreur_data else ""
    # Rôle du vendeur : inscrit sur la vente (récentes), sinon celui de son compte.
    vendeur_role = livraison.get("vendeur_role") or (livreur_data or {}).get("role") or ""
    if vendeur_role == "livreur":
        vendeur_role = "gerant"

    # Arrondir les montants à 2 chiffres après la virgule
    def round_float(val):
        try:
            return round(float(val), 2)
        except (TypeError, ValueError):
            return 0.0

    montant_total = round_float(livraison.get("montant_total", 0))
    montant_paye = round_float(livraison.get("montant_paye", 0))
    retour = round_float(livraison.get("retour", 0))
    ristourne = round_float(livraison.get("ristourne", 0))

    montant_restant = round_float(montant_total - montant_paye) if montant_paye < montant_total else 0.0

    # Sérialisation de la livraison
    livraison_serialized = {
        "id": str(livraison["_id"]),
        "livreur_id": str(livraison["livreur_id"]),
        "nom_livreur": nom_livreur,
        "vendeur_role": vendeur_role,
        "session_caisse_id": str(livraison["session_caisse_id"]) if livraison.get("session_caisse_id") else None,
        "magasin_id": str(livraison["magasin_id"]),
        "client_id": str(livraison["client_id"]),
        "client": client_data,
        "produits": livraison["produits"],
        "montant_total": montant_total,
        "montant_produits": round_float(livraison.get("montant_produits", montant_total)),  # Montant des produits seuls
        "montant_brut": round_float(livraison.get("montant_brut", livraison.get("montant_produits", montant_total))),
        "reductions": livraison.get("reductions", []),
        "total_reductions": round_float(livraison.get("total_reductions", 0)),
        "appliquer_tva": bool(livraison.get("appliquer_tva")),
        "taux_tva": round_float(livraison.get("taux_tva", 0)),
        "montant_tva": round_float(livraison.get("montant_tva", 0)),
        "dette_ancienne": round_float(livraison.get("dette_ancienne", 0)),                  # Dette ancienne incluse (0 si aucune)
        "montant_paye": montant_paye,
        "montant_deduit_caisse": round_float(livraison.get("montant_deduit_caisse", 0)),
        "montant_restant": montant_restant,
        "quantite": livraison["quantite"],
        "statut_paiement": livraison["statut_paiement"],
        "statut_livraison": livraison["statut_livraison"],
        "etat": etat_vente(livraison),
        "etat_libelle": ETATS_VENTE[etat_vente(livraison)],
        "abandon_reste": livraison.get("abandon_reste"),
        "abandon_en_attente": abandon_en_attente(livraison),
        "retour": retour,
        "ristourne": ristourne,
        "have_ristourne": livraison.get("have_ristourne", False),
        "have_retour": livraison.get("have_retour", False),
        "remboursements": livraison.get("remboursements", []),
        # Cumul remboursé : l'historique fait foi (les premiers remboursements
        # sont antérieurs au compteur montant_rembourse).
        "montant_rembourse": round_float(max(
            float(livraison.get("montant_rembourse", 0) or 0),
            sum(float(r.get("montant", 0) or 0) for r in (livraison.get("remboursements") or [])),
        )),
        "montant_retourne": round_float(livraison.get("montant_retourne", 0)),
        "frais_retour": round_float(livraison.get("frais_retour", 0)),
        "methode_paiement": livraison.get("methode_paiement", "cash"),
        "paiements": livraison.get("paiements", []),
        # Dates enregistrées en UTC : le « Z » permet au navigateur de les
        # afficher (et de les filtrer par jour) à l'heure locale du magasin.
        "date_commande": _iso_utc(livraison["date_commande"]),
        "created_at": _iso_utc(livraison["created_at"]),
        "updated_at": _iso_utc(livraison["updated_at"]),
        # Vente annulée (corbeille) : quand, par qui, et l'argent rendu au client.
        "deleted_at": _iso_utc(livraison.get("deleted_at")),
        "annulation": livraison.get("annulation"),
    }

    # Ajout des retours si have_ristourne est True
    if livraison.get("have_retour") is True:
        retours = RetourLivraisonModel.get_retours_by_livraison(livraison["_id"])
        livraison_serialized["retours"] = [
            {
                "id": str(r["_id"]),
                "produits": r.get("produits", []),
                "motif": r.get("motif", ""),
                "montant_retour": r.get("montant_retour"),
                "montant_a_rembourser": r.get("montant_a_rembourser"),
                "manager_id": str(r["manager_id"]) if r.get("manager_id") else None,
                "livreur_id": str(r["livreur_id"]) if r.get("livreur_id") else None,
                "created_at": r.get("created_at")
            } for r in retours
        ]
    else:
        livraison_serialized["retours"] = []

    return livraison_serialized


# Retourne toutes les livraison d'un livreur 
def get_livraison_livreur(livreur_id):
    """
    Récupérer les livraisons d'un livreur
    """
    livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)
    # Trie les livraisons par date de création (plus récente en premier)
      
    livraisons = sorted(
        livraisons,
        key=lambda l: l.get("created_at", datetime.min),
        reverse=True
    )
    livraisons_nettoyees = [serialize_livraison(l) for l in livraisons]
    return livraisons_nettoyees, 200


def get_client_livraisons_service(client_id, livreur_id=None):
    """
    Récupérer toutes les livraisons d'un client, triées par date décroissante.
    Si livreur_id est fourni, filtre uniquement ses livraisons.
    """
    livraisons = LivraisonModel.get_livraisons_by_client(client_id, livreur_id=livreur_id)
    
    # Tri par date de création (plus récente en premier)
    livraisons = sorted(
        livraisons,
        key=lambda l: l.get("created_at", datetime.min),
        reverse=True
    )
    
    livraisons_nettoyees = [serialize_livraison(l) for l in livraisons]
    return livraisons_nettoyees, 200


#get_livraison_managers
def get_livraison_managers(magasin_id=None, vendeur_id=None):
    """
    Retourne toutes les livraisons d'un magasin (celles d'un seul vendeur si vendeur_id est donné).
    """
    livraisons = LivraisonModel.get_all_livraisons(magasin_id=magasin_id, livreur_id=vendeur_id)
    
    if isinstance(livraisons, tuple): # Cas d'erreur retourné par le model
        return livraisons
    
    # chaque livraison enrichir avec les informations des conditionnements si présents
    for livraison in livraisons:
        produits_enrichis = []
        for item in livraison["produits"]:
            produit_id = item.get("produit_id")
            produit_info = ProduitModel.get_by_id(produit_id)

            conditionnement_nom = item.get("conditionnement_nom")
            if not conditionnement_nom and item.get("is_conditionne", False) and item.get("conditionnement_id"):
                conditionnement = ProduitModel.get_conditionnement_by_id(
                    produit_id,
                    item["conditionnement_id"]
                )
                if conditionnement:
                    conditionnement_nom = conditionnement.get("nom")

            enriched_item = {
                "produit_id": str(produit_id),
                "quantite": item.get("quantite"),
                "prix": item.get("prix"),
                "nom": produit_info.get("nom") if produit_info else item.get("nom"),
                "categorie": produit_info.get("categorie") if produit_info else None,
                "unite": produit_info.get("unite") if produit_info else None,
                "image": produit_info.get("image") if produit_info else None,

                # Champs pour retours conditionnés
                "is_conditionne": item.get("is_conditionne", False),
                "conditionnement_id": item.get("conditionnement_id"),
                "conditionnement_nom": conditionnement_nom,  # ajout ici
                "facteur": item.get("facteur"),
            }

            produits_enrichis.append(enriched_item)

        livraison["produits"] = produits_enrichis
    
    # Trie les livraisons par date de création (plus récente en premier)
    livraisons = sorted(
        livraisons,
        key=lambda l: l.get("created_at", datetime.min),
        reverse=True
    )
    
    livraisons_nettoyees = [serialize_livraison(l) for l in livraisons]
    return livraisons_nettoyees, 200



# Retourne les 5 dernières livraisons d'un livreur
def get_livraisons_recentes_par_livreur(livreur_id, limit=5):
    """
    Retourne les N livraisons les plus récentes d’un livreur.
    """
    # Récupère toutes les livraisons du livreur
    livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)

    # Trie les livraisons par date de création (plus récente en premier)
    livraisons_triees = sorted(
        livraisons,
        key=lambda l: l.get("created_at", datetime.min),
        reverse=True
    )

    # Prend uniquement les 'limit' premières
    livraisons_recentes = livraisons_triees[:limit]

    # Sérialise les livraisons
    return [serialize_livraison(l) for l in livraisons_recentes], 200


# Avoir les détails completes d'une livraison pour un livreur
def details_livraison_service(livreur_id, livraison_id):
    """
    Retourne les détails enrichis d'une livraison pour un livreur donné,
    incluant les noms de conditionnements si présents.
    """
    # 1. Récupération de la livraison
    livraison = LivraisonModel.get_livraison_by_id(livraison_id)
    if not livraison:
        return {"error": "Livraison introuvable"}, 404

    # 2. Vérification de propriété (optionnelle)
    # if str(livraison["livreur_id"]) != str(livreur_id):
    #     return {"error": "Accès non autorisé à cette livraison"}, 403

    # 3. Enrichir chaque produit
    produits_enrichis = []
    for item in livraison["produits"]:
        produit_id = item.get("produit_id")
        produit_info = ProduitModel.get_by_id(produit_id)

        conditionnement_nom = item.get("conditionnement_nom")
        if not conditionnement_nom and item.get("is_conditionne", False) and item.get("conditionnement_id"):
            conditionnement = ProduitModel.get_conditionnement_by_id(
                produit_id,
                item["conditionnement_id"]
            )
            if conditionnement:
                conditionnement_nom = conditionnement.get("nom")

        enriched_item = {
            "produit_id": str(produit_id),
            "quantite": item.get("quantite"),
            "prix": item.get("prix"),
            "nom": produit_info.get("nom") if produit_info else item.get("nom"),
            "categorie": produit_info.get("categorie") if produit_info else None,
            "unite": produit_info.get("unite") if produit_info else None,
            "image": produit_info.get("image") if produit_info else None,

            # Champs pour retours conditionnés
            "is_conditionne": item.get("is_conditionne", False),
            "conditionnement_id": item.get("conditionnement_id"),
            "conditionnement_nom": conditionnement_nom,  # ajout ici
            "facteur": item.get("facteur"),
            "quantite_magasin": produit_info.get("quantite", 0) if produit_info else 0, # ajout de la quantite dispo en magasin
            "quantite_livreur": StockLivreurModel.get_produit_quantite(livraison["livreur_id"], produit_id),
            # Pour l'écran de modification : cartons proposés et unités vendues.
            "conditionnements": (produit_info or {}).get("conditionnements") or [],
            "prix_vente_fiche": (produit_info or {}).get("prix"),
            "suivi_serie": numeros_serie_service.est_suivi(produit_info) if produit_info else False,
        }
        if item.get("numeros_serie") is not None:
            enriched_item["numeros_serie"] = list(item.get("numeros_serie") or [])

        produits_enrichis.append(enriched_item)

    # 4. Sérialiser la livraison
    livraison_detail = serialize_livraison(livraison)

    # 5. Injecter les produits enrichis
    livraison_detail["produits"] = produits_enrichis

    return livraison_detail, 200







def get_livraisons_filtrees_par_livreur(livreur_id, filters=None):
    """-1356886
    Récupère les livraisons d’un livreur avec des filtres dynamiques :
    - colonnes simples : status, client_id, etc.
    - date: exacte (via `date`) ou plage (`date_min`, `date_max`)
    - montant_total: plage (`montant_min`, `montant_max`)
    """
    livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)
    
    def match_filters(livraison):
        created_at = livraison.get("created_at")
        montant_total = livraison.get("montant_total", 0)

        for key, value in filters.items():
            if key == "date":
                if not created_at or created_at.strftime("%Y-%m-%d") != value:
                    return False
            elif key == "date_min":
                if not created_at or created_at < datetime.strptime(value, "%Y-%m-%d"):
                    return False
            elif key == "date_max":
                if not created_at or created_at > datetime.strptime(value, "%Y-%m-%d"):
                    return False
            elif key == "montant_min":
                if montant_total < float(value):
                    return False
            elif key == "montant_max":
                if montant_total > float(value):
                    return False
            else:
                # Comparaison directe pour d’autres attributs
                if str(livraison.get(key)) != str(value):
                    return False
        return True
    
    if filters:
        livraisons = [l for l in livraisons if match_filters(l)]

    livraisons = sorted(
        livraisons,
        key=lambda l: l.get("created_at", datetime.min),
        reverse=True
    )

    livraisons_nettoyees = [serialize_livraison(l) for l in livraisons]
    return livraisons_nettoyees, 200



  
# Retourne les 5 dernières livraisons d'un magasin
def get_livraisons_recentes_total(magasin_id=None, limit=5):
    """
    Retourne les N livraisons les plus récentes d’un magasin.
    """
    # Récupère toutes les livraisons du magasin
    livraisons = LivraisonModel.get_all_livraisons(magasin_id=magasin_id)

    # Trie les livraisons par date de création (plus récente en premier)
    livraisons_triees = sorted(
        livraisons,
        key=lambda l: l.get("created_at", datetime.min),
        reverse=True
    )

    # Prend uniquement les 'limit' premières
    livraisons_recentes = livraisons_triees[:limit]

    # Sérialise les livraisons
    return [serialize_livraison(l) for l in livraisons_recentes], 200






# Livraison livreur avec filtres dynamiques
def get_livraison_livreurs(livreur_id, filters=None):
    livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)
    livraisons = sorted(livraisons, key=lambda l: l.get("created_at", datetime.min), reverse=True)

    livraisons_nettoyees = [serialize_livraison(l) for l in livraisons]

    if filters:
        def match(l):
            if filters["statut_paiement"] and l["statut_paiement"] != filters["statut_paiement"]:
                return False
            if filters["statut_livraison"] and l["statut_livraison"] != filters["statut_livraison"]:
                return False
            if filters.get("etat") and l["etat"] != filters["etat"]:
                return False
            if filters["date_commande"]:
                try:
                    date_str = l["date_commande"].split("T")[0]
                    if date_str != filters["date_commande"]:
                        return False
                except Exception:
                    return False
            if filters["client_nom"]:
                client_nom = (l["client"] or {}).get("nom", "").lower()
                if filters["client_nom"].lower() not in client_nom:
                    return False
            return True

        livraisons_nettoyees = list(filter(match, livraisons_nettoyees))

    return livraisons_nettoyees, 200




# Supprimer une livraison et entrainer la réintégration des produits dans le stock du livreur
def supprimer_livraison_service(livraison_id, data=None, user_id=None):
    """
    Annule une vente (mise à la corbeille, restaurable) :
    - les articles retournent dans le stock (magasin ou gérant) ;
    - le crédit du compte client utilisé pour payer lui est restitué ;
    - l'argent payé par le client (et un trop-perçu de retour non remboursé)
      lui est rendu selon data["mode"] (especes / om / momo / bancaire, ou
      credit_client = crédité sur son compte) ; l'annulation est tracée.
    """
    data = data or {}
    livraison = LivraisonModel.get_livraison_by_id(livraison_id)
    
    
    if not livraison:
        return {"error": "Livraison introuvable"}, 404
    erreur_cloture = erreur_vente_verrouillee(livraison)
    if erreur_cloture:
        return erreur_cloture

    a_rendre = montant_a_rendre_annulation(livraison)
    mode = str(data.get("mode") or "especes").lower()
    if mode == "cash":
        mode = "especes"
    if mode not in MODES_REMBOURSEMENT:
        return {"error": "Mode de remboursement invalide"}, 400
    reference, telephone, erreur = (None, None, None)
    if a_rendre > 0:
        reference, telephone, erreur = _canal_remboursement(mode, data)
        if erreur:
            return {"error": erreur}, 400
        if mode == "credit_client" and not ClientModel.get_client_by_id(livraison.get("client_id")):
            return {"error": "Client introuvable : impossible de créditer son compte"}, 400

    auteur = UserModel.get_user_by_id(user_id) if user_id else None
    annulation = {
        "date": datetime.utcnow().isoformat(),
        "par": str(user_id) if user_id else None,
        "par_nom": f"{auteur.get('name', '')} {auteur.get('first_name', '')}".strip() if auteur else None,
        "motif": str(data.get("motif") or "").strip()[:200],
        "montant_rendu": a_rendre,
        "mode": mode if a_rendre > 0 else None,
        "mode_libelle": MODES_REMBOURSEMENT[mode] if a_rendre > 0 else None,
        "reference": reference or None,
        "telephone": telephone or None,
        "credit_restitue": round(float(livraison.get("montant_deduit_caisse", 0) or 0), 2),
    }
    if a_rendre > 0 and mode == "credit_client":
        client = ClientModel.get_client_by_id(livraison["client_id"])
        ClientModel.update_client(livraison["client_id"], {
            "montant_en_caisse": round(float(client.get("montant_en_caisse", 0) or 0) + a_rendre, 2)})
    message = "Vente annulée : les articles sont revenus en stock"
    if a_rendre > 0:
        message += (f" et {a_rendre:g} CFA ont été crédités sur le compte du client" if mode == "credit_client"
                    else f". {a_rendre:g} CFA sont à rendre au client ({MODES_REMBOURSEMENT[mode]})")
    reponse = {"message": message, "montant_rendu": a_rendre, "annulation": annulation}
    
    # vérifier s'il y'a une ristourne lié à cette livraison et la supprimer

    ristourne = RistourneModel.find_by_livraison_id(livraison_id)
    
    if ristourne:
        RistourneModel.delete_by_id(ristourne["_id"])

    # Rembourser le montant_en_caisse si y en a eu un de déduit
    montant_deduit_caisse = livraison.get("montant_deduit_caisse", 0)
    if montant_deduit_caisse > 0:
        client_id = livraison["client_id"]
        client = ClientModel.get_client_by_id(client_id)
        if client:
            nouveau_montant = float(client.get("montant_en_caisse", 0) or 0) + montant_deduit_caisse
            ClientModel.update_client(client_id, {"montant_en_caisse": nouveau_montant})

    # Les articles reviennent dans le stock valorisé, au coût auquel ils étaient sortis.
    valorisation_service.annuler_lignes(livraison["produits"], "annulation livraison")

    if est_livraison_manager(livraison):
        # Livraison faite depuis le stock du magasin : les articles y retournent.
        numeros_serie_service.rendre_lignes(livraison["produits"], "annulation",
                                            {"admin_id": user_id}, vente_id=livraison_id)
        acteur_magasin = {
            "admin_id": str(livraison.get("manager_id")),
            "client_id": str(livraison.get("client_id")),
            "livraison_id": str(livraison_id),
        }
        for produit in livraison["produits"]:
            ProduitModel.increment_stock(
                produit_id=str(produit["produit_id"]),
                quantite=int(produit["quantite"]),
                acteur=acteur_magasin,
                type_mouvement="annulation livraison manager",
            )
        LivraisonModel.get_collection().update_one(
            {"_id": ObjectId(livraison_id)},
            {"$set": {"deleted_at": datetime.utcnow(), "produits": livraison["produits"], "annulation": annulation}},
        )
        return reponse, 200

    # Réintégrer les produits dans le stock du livreur
    StockLivreurModel.ajouter_stock(livraison["livreur_id"], livraison["produits"])
    
    # ✅ ENRICHISSEMENT : Tracer l'annulation (restitution virtuelle)
    client_id = livraison["client_id"]
    client = ClientModel.get_client_by_id(client_id)
    livreur_id_val = livraison["livreur_id"]
    livreur = UserModel.find_by_id(livreur_id_val)
    acteur = {
        "livreur_id": str(livreur_id_val),
        "livreur_nom": f"{livreur.get('first_name', '')} {livreur.get('name', '')}".strip() if livreur else "Inconnu",
        "client_id": str(client_id),
        "client_nom": client.get("nom") if client else "Inconnu",
        "livraison_id": str(livraison_id)
    }

    for produit in livraison["produits"]:
        ProduitModel.ajouter_mouvement_stock(
            produit_id=produit["produit_id"],
            mouvement={
                "type": "suppression livraison livreur",
                "quantite": abs(int(produit["quantite"])), # Restitution virtuelle
                "acteur": acteur
            }
        )

    # Masquer la livraison au lieu de la supprimer (Soft Delete)
    LivraisonModel.get_collection().update_one(
        {"_id": ObjectId(livraison_id)},
        {"$set": {"deleted_at": datetime.utcnow(), "produits": livraison["produits"], "annulation": annulation}},
    )
    
    return reponse, 200

def restaurer_livraison_service(livraison_id):
    """
    Restaure une livraison de la corbeille en re-déduisant le stock du livreur
    et en re-déduisant l'argent dans la caisse du client (si applicable).
    """
    livraison = LivraisonModel.get_collection().find_one({"_id": ObjectId(livraison_id), "deleted_at": {"$exists": True}})
    
    if not livraison:
        return {"error": "Livraison introuvable dans la corbeille"}, 404
    erreur_cloture = erreur_vente_verrouillee(livraison)
    if erreur_cloture:
        return erreur_cloture
        
    produits_demandes = livraison.get("produits", [])
    livreur_id = livraison.get("livreur_id")
    client_id = livraison.get("client_id")

    annulation = livraison.get("annulation") or {}
    # De l'argent rendu en main propre (espèces, mobile money, virement) ne peut
    # pas être repris : la vente restaurée paraîtrait payée alors que le client
    # a été remboursé, et la sortie de caisse disparaîtrait des rapports.
    rendu = float(annulation.get("montant_rendu", 0) or 0)
    if rendu > 0 and annulation.get("mode") not in (None, "credit_client"):
        return {"error": f"Impossible de restaurer : {rendu:g} CFA ont été rendus au client "
                         f"({annulation.get('mode_libelle') or annulation.get('mode')}) à l'annulation. "
                         "Enregistrez une nouvelle vente."}, 400

    # L'argent crédité sur le compte du client à l'annulation lui est repris.
    credit_annulation = float(annulation.get("montant_rendu", 0) or 0) if annulation.get("mode") == "credit_client" else 0.0
    if credit_annulation > 0:
        client = ClientModel.get_client_by_id(client_id)
        solde = float((client or {}).get("montant_en_caisse", 0) or 0)
        if solde < credit_annulation:
            return {"error": f"Impossible de restaurer : le client a déjà utilisé le crédit de {credit_annulation:g} CFA reçu à l'annulation (solde {solde:g} CFA)."}, 400

    def reprendre_credit_annulation():
        if credit_annulation > 0:
            client = ClientModel.get_client_by_id(client_id)
            ClientModel.update_client(client_id, {
                "montant_en_caisse": round(float(client.get("montant_en_caisse", 0) or 0) - credit_annulation, 2)})

    if est_livraison_manager(livraison):
        # Livraison du magasin : on reprend les articles dans le stock du magasin.
        for produit in produits_demandes:
            produit_db = ProduitModel.get_by_id(str(produit["produit_id"]))
            if not produit_db or int(produit_db.get("quantite", 0)) < int(produit["quantite"]):
                return {"error": f"Impossible de restaurer. Stock magasin insuffisant pour l'article {produit.get('nom', produit['produit_id'])}."}, 400
            if produit.get("numeros_serie"):
                try:
                    numeros_serie_service.verifier_disponibles(produit_db, produit["numeros_serie"],
                                                               len(produit["numeros_serie"]))
                except ErreurNumeroSerie as err:
                    return {"error": f"Impossible de restaurer : {err}"}, 400
        client_restaure = ClientModel.get_client_by_id(client_id) or {}
        numeros_serie_service.vendre_lignes(produits_demandes, livraison_id, {"admin_id": livraison.get("manager_id")},
                                            client_id=client_id, client_nom=client_restaure.get("nom"),
                                            type_="restauration")
        valorisation_service.valoriser_lignes(produits_demandes, livraison.get("magasin_id"))
        acteur_magasin = {"admin_id": str(livraison.get("manager_id")), "client_id": str(client_id),
                          "livraison_id": str(livraison_id)}
        for produit in produits_demandes:
            ProduitModel.decrement_stock(
                produit_id=str(produit["produit_id"]),
                quantite=int(produit["quantite"]),
                acteur=acteur_magasin,
                type_mouvement="restauration livraison manager",
            )
        montant_deduit_caisse = livraison.get("montant_deduit_caisse", 0)
        if montant_deduit_caisse > 0:
            client = ClientModel.get_client_by_id(client_id)
            if client:
                nouveau_montant = max(0.0, float(client.get("montant_en_caisse", 0) or 0) - montant_deduit_caisse)
                ClientModel.update_client(client_id, {"montant_en_caisse": nouveau_montant})
        reprendre_credit_annulation()
        LivraisonModel.get_collection().update_one(
            {"_id": ObjectId(livraison_id)},
            {"$unset": {"deleted_at": "", "annulation": ""}, "$set": {"produits": produits_demandes}},
        )
        return {"message": "Livraison restaurée avec succès"}, 200

    # 1. Vérifier si le livreur a suffisamment de stock pour qu'on puisse restaurer la livraison
    stock_dispo = StockLivreurModel.get_stock_livreur(livreur_id)
    stock_map = {str(item["produit_id"]): int(item.get("quantite", 0)) for item in stock_dispo if "produit_id" in item}

    for produit in produits_demandes:
        pid = str(produit["produit_id"])
        qte = int(produit["quantite"])
        if pid not in stock_map or stock_map[pid] < qte:
            return {"error": f"Impossible de restaurer. Le livreur n'a pas assez de stock pour l'article {produit.get('nom', pid)}."}, 400

    # 2. Retirer le stock du livreur (qui lui avait été rendu lors de la suppression)
    valorisation_service.valoriser_lignes(produits_demandes, livraison.get("magasin_id"))
    StockLivreurModel.retirer_stock(livreur_id, produits_demandes)

    # ✅ ENRICHISSEMENT : Tracer la restauration (sortie virtuelle)
    client = ClientModel.get_client_by_id(client_id)
    livreur_obj = UserModel.find_by_id(livreur_id)
    acteur = {
        "livreur_id": str(livreur_id),
        "livreur_nom": f"{livreur_obj.get('first_name', '')} {livreur_obj.get('name', '')}".strip() if livreur_obj else "Inconnu",
        "client_id": str(client_id),
        "client_nom": client.get("nom") if client else "Inconnu",
        "livraison_id": str(livraison_id)
    }

    for produit in produits_demandes:
        ProduitModel.ajouter_mouvement_stock(
            produit_id=produit["produit_id"],
            mouvement={
                "type": "restauration livraison livreur",
                "quantite": -abs(int(produit["quantite"])),
                "acteur": acteur
            }
        )

    # 3. Retirer l'argent du client (qui lui avait été remboursé lors de la suppression)
    montant_deduit_caisse = livraison.get("montant_deduit_caisse", 0)
    if montant_deduit_caisse > 0:
        client = ClientModel.get_client_by_id(client_id)
        if client:
            # On retire l'argent de sa caisse pour repayer la livraison
            nouveau_montant = max(0.0, float(client.get("montant_en_caisse", 0) or 0) - montant_deduit_caisse)
            ClientModel.update_client(client_id, {"montant_en_caisse": nouveau_montant})

    reprendre_credit_annulation()

    # 4. Enlever la marque de suppression (Restauration)
    LivraisonModel.get_collection().update_one(
        {"_id": ObjectId(livraison_id)},
        {"$unset": {"deleted_at": "", "annulation": ""}, "$set": {"produits": produits_demandes}},
    )

    return {"message": "Livraison restaurée avec succès"}, 200
 
def supprimer_definitivement_livraison_service(livraison_id, user_id):
    """
    Supprime définitivement une livraison qui est déjà dans la corbeille.
    - Si Livreur : Le produit reste dans son stock personnel.
    - Si Admin/Manager : Le produit retourne au stock Magasin (retiré du livreur).
    """
    if not ObjectId.is_valid(livraison_id):
        return {"error": "ID de livraison invalide"}, 400

    user = UserModel.get_user_by_id(user_id)
    if not user:
        return {"error": "Utilisateur introuvable"}, 404

    livraison = LivraisonModel.get_collection().find_one({
        "_id": ObjectId(livraison_id), 
        "deleted_at": {"$exists": True}
    })

    if not livraison:
        return {"error": "Livraison introuvable dans la corbeille"}, 404

    # Vérification des permissions
    role_user = user.get("role")
    if role_user in ("livreur", "gerant") and str(livraison.get("livreur_id")) != str(user_id):
        return {"error": "Vous n'avez pas l'autorisation de supprimer cette livraison"}, 403

    # LOGIQUE DE STOCK
    if est_livraison_manager(livraison):
        # Livraison du magasin : les articles sont déjà revenus au magasin lors
        # de la mise à la corbeille. Rien à déplacer.
        pass
    elif role_user in ["admin", "manager"]:
        # Retour au MAGASIN (on retire du livreur pour remettre au stock général)
        produits = livraison.get("produits", [])
        livreur_id = livraison.get("livreur_id")
        livreur_obj = UserModel.find_by_id(livreur_id)
        
        acteur = {
            "admin_id": str(user_id),
            "livreur_id": str(livreur_id),
            "livreur_nom": f"{livreur_obj.get('first_name', '')} {livreur_obj.get('name', '')}".strip() if livreur_obj else "Inconnu",
            "motif": "Suppression définitive par Admin (retour magasin)"
        }

        for produit in produits:
            p_id = str(produit["produit_id"])
            qte = int(produit["quantite"])
            
            # On retire du livreur (car c'était déjà revenu chez lui à l'annulation)
            stock_livreur_qte = StockLivreurModel.get_produit_quantite(livreur_id, p_id)
            qte_a_retirer = min(stock_livreur_qte, qte)

            if qte_a_retirer > 0:
                StockLivreurModel.retirer_du_stock(livreur_id, p_id, qte_a_retirer)
                ProduitModel.increment_stock(
                    produit_id=p_id,
                    quantite=qte_a_retirer,
                    acteur=acteur,
                    type_mouvement="retour_magasin_via_suppression_definitive"
                )
    else:
        # C'est un LIVREUR : Le stock reste chez lui. 
        # Comme l'annulation (soft delete) a déjà remis les produits dans son stock,
        # on n'a rien besoin de faire de plus pour le stock.
        pass

    # 2. Suppression définitive de l'enregistrement
    LivraisonModel.delete_livraison(livraison_id)

    return {"message": "Livraison supprimée définitivement avec succès"}, 200


def ajouter_paiement_service(livraison_id, montant, methode=None, par=None):
    """
    Service de mise à jour du montant payé pour une livraison.
    Si le montant versé dépasse le total dû, le surplus est crédité
    dans le compte (montant_en_caisse) du client pour de futures livraisons.
    : param livraison_id: ID de la livraison
    : param montant : montant versé à ajouter
    """

    if not ObjectId.is_valid(livraison_id):
        return {"error": "ID de livraison invalide"}, 400

    if not isinstance(montant, (int, float)) or montant <= 0:
        return {"error": "Le montant doit être un nombre positif"}, 400

    if methode is not None and str(methode).strip().lower() not in ("cash", "om", "momo", "bancaire"):
        return {"error": "Mode de paiement invalide (cash, om, momo ou bancaire)"}, 400

    resultat = LivraisonModel.ajouter_paiement(
        livraison_id, montant, methode=str(methode).strip().lower() if methode else None, par=par)
    if not resultat:
        return {"error": "Livraison non trouvée"}, 404

    if "message" in resultat and "surplus_credite" not in resultat:
        return {"message": resultat["message"]}, 400

    # Récupérer le surplus éventuellement crédité (injecté par le modèle)
    surplus_credite = resultat.pop("surplus_credite", 0)

    # Nettoyage pour retour JSON
    resultat["_id"] = str(resultat["_id"])
    resultat["livreur_id"] = str(resultat["livreur_id"])
    resultat["client_id"] = str(resultat["client_id"])
    resultat["magasin_id"] = str(resultat["magasin_id"])
    resultat["created_at"] = resultat["created_at"].isoformat()
    resultat["updated_at"] = resultat["updated_at"].isoformat()

    reponse = {
        "message": "Paiement mis à jour avec succès",
        "livraison": resultat
    }

    # Informer le frontend si un surplus a été crédité au client
    if surplus_credite > 0:
        reponse["surplus_credite"] = surplus_credite
        reponse["info"] = (
            f"Le client a versé {montant} FCFA pour une dette de {resultat.get('montant_total')} FCFA. "
            f"Le surplus de {surplus_credite} FCFA a été crédité sur son compte."
        )

    return reponse, 200




 
    
# service permettant de rembourser un retour (ancienne version, conservée
# pour compatibilité : préférer rembourser_service)
def rembourser_ristourne_client(livraison_id):
    """
    Rembourse la ristourne accordée à un client pour une livraison donnée.
    """
    livraison = LivraisonModel.get_collection().find_one({"_id": ObjectId(livraison_id)})

    if not livraison:
        return {"error": "Livraison introuvable"}, 404

    retour = livraison.get("retour", 0)
    if retour <= 0:
        return {"error": "Aucun retour à rembourser"}, 400

    if not livraison.get("have_retour") or livraison.get("have_retour") is False:
        return {"error": "Le retour a déjà été remboursé"}, 400

    # Exemple : tu peux ici créer une ligne dans un historique ou envoyer une notification

    # Effectuer le remboursement (à adapter selon la logique métier, caisse, ou solde)
    # Ici on met juste à jour l'état
    LivraisonModel.get_collection().update_one(
        {"_id": ObjectId(livraison_id)},
        {
            "$set": {
                "have_retour": False,
                "retour": 0,  # On remet le retour à 0
                "updated_at": datetime.utcnow()
            }
        }
    )

    return {"message": "Retour remboursé avec succès", "status": 200}


# Service permettant de rembourser le relicat d'un client

MODES_REMBOURSEMENT = {
    "especes": "Espèces",
    "om": "Orange Money",
    "momo": "MTN MoMo",
    "bancaire": "Virement bancaire",
    "credit_client": "Crédit sur le compte client",
}
MODES_AVEC_REFERENCE = ("om", "momo", "bancaire")


def _canal_remboursement(mode, data):
    """Référence et numéro de l'argent rendu au client.

    La référence est obligatoire pour Orange Money, MoMo et virement, le numéro
    pour Orange Money et MoMo. Numéro au format international (+indicatif) ;
    sans « + », il est camerounais (+237 et 9 chiffres).
    :return: (reference, telephone, message d'erreur ou None)
    """
    reference = str(data.get("reference") or "").strip()[:100]
    brut = str(data.get("telephone") or "").strip()
    telephone = "".join(c for c in brut if c.isdigit())
    if telephone and not brut.startswith("+") and not (telephone.startswith("237") and len(telephone) > 9):
        telephone = "237" + telephone
    if mode in MODES_AVEC_REFERENCE and not reference:
        return None, None, f"La référence de la transaction est obligatoire pour un remboursement par {MODES_REMBOURSEMENT[mode]}"
    if mode in ("om", "momo") and not telephone:
        return None, None, "Le numéro de téléphone qui reçoit le remboursement est obligatoire"
    if telephone.startswith("237") and len(telephone) != 12:
        return None, None, "Le numéro de téléphone doit contenir exactement 9 chiffres (après +237)"
    if telephone and not 8 <= len(telephone) <= 15:  # norme E.164
        return None, None, "Numéro de téléphone invalide (indicatif du pays + numéro)"
    return reference, (f"+{telephone}" if telephone else ""), None


def montant_a_rendre_annulation(livraison):
    """Argent à rendre au client si la vente est annulée.

    Ce qu'il a réellement payé (hors crédit de son compte, restitué à part)
    plus le trop-perçu d'un retour pas encore remboursé.
    """
    paye = float(livraison.get("montant_paye", 0) or 0)
    deduit = float(livraison.get("montant_deduit_caisse", 0) or 0)
    return round(max(paye - deduit, 0.0) + float(livraison.get("retour", 0) or 0), 2)


def rembourser_service(livraison_id, data=None, user_id=None):
    """Rembourse au client le trop-perçu né des retours (champ « retour »).

    - mode : especes / om / momo / bancaire (argent rendu) ou credit_client
      (ajouté au compte du client, utilisable sur ses prochains achats) ;
    - montant : facultatif, tout le restant dû par défaut (remboursement
      partiel possible) ;
    - reference : numéro de transaction, obligatoire pour Orange Money,
      MoMo et virement (preuve de l'envoi) ;
    - telephone : numéro qui a reçu l'argent (Orange Money / MoMo).
    L'historique des retours est conservé et chaque remboursement est tracé.
    """
    data = data or {}
    livraison = LivraisonModel.get_livraison_by_id(livraison_id)
    if not livraison:
        return {"error": "Livraison introuvable"}, 404

    du = round(float(livraison.get("retour", 0) or 0), 2)
    if du <= 0:
        return {"error": "Aucun montant à rembourser pour cette vente"}, 400

    mode = str(data.get("mode") or "especes").lower()
    if mode == "cash":
        mode = "especes"
    if mode not in MODES_REMBOURSEMENT:
        return {"error": "Mode de remboursement invalide"}, 400

    try:
        montant = du if data.get("montant") in (None, "") else round(float(data["montant"]), 2)
    except (TypeError, ValueError):
        return {"error": "Montant de remboursement invalide"}, 400
    if montant <= 0:
        return {"error": "Le montant à rembourser doit être positif"}, 400
    if montant > du:
        return {"error": f"Le montant dépasse ce qui est dû au client ({du:g})"}, 400

    reference, telephone, erreur = _canal_remboursement(mode, data)
    if erreur:
        return {"error": erreur}, 400

    if mode == "credit_client":
        client = ClientModel.get_client_by_id(livraison.get("client_id")) if livraison.get("client_id") else None
        if not client:
            return {"error": "Client introuvable : impossible de créditer son compte"}, 400
        credit = float(client.get("montant_en_caisse", 0) or 0)
        ClientModel.update_client(livraison["client_id"], {"montant_en_caisse": round(credit + montant, 2)})

    auteur = UserModel.get_user_by_id(user_id) if user_id else None
    remboursement = {
        "date": datetime.utcnow().isoformat(),
        "montant": montant,
        "mode": mode,
        "mode_libelle": MODES_REMBOURSEMENT[mode],
        "reference": reference or None,
        "telephone": telephone or None,
        "motif": str(data.get("motif") or "").strip()[:200],
        "par": str(user_id) if user_id else None,
        "par_nom": f"{auteur.get('name', '')} {auteur.get('first_name', '')}".strip() if auteur else None,
    }
    restant = round(du - montant, 2)
    LivraisonModel.get_collection().update_one(
        {"_id": ObjectId(livraison_id)},
        {"$set": {
            "retour": restant,
            # Cumul déjà rendu au client : « retour » ne garde que ce qui reste dû.
            "montant_rembourse": round(float(livraison.get("montant_rembourse", 0) or 0) + montant, 2),
            "remboursements": (livraison.get("remboursements") or []) + [remboursement],
            "updated_at": datetime.utcnow(),
        }},
    )
    return {
        "message": f"Remboursement de {montant:g} effectué ({MODES_REMBOURSEMENT[mode]})",
        "montant": montant,
        "restant": restant,
        "mode": mode,
        "remboursement": remboursement,
    }, 200


def _appliquer_ristourne(livraison, montant_ttc, motif):
    """Ajoute une ristourne aux réductions de la vente et recalcule ses montants.

    ``montant_ttc`` est ce que le client ne paiera pas : il est converti en
    réduction hors taxe (type « ristourne » de reductions_service), si bien
    que le total, la TVA et la comptabilité baissent ensemble.
    :return: les champs de montant à enregistrer sur la vente.
    """
    decimales = tva_service.decimales_magasin(livraison.get("magasin_id"))
    lignes = livraison.get("produits") or []
    brut = float(livraison.get("montant_brut") or 0) or round(
        sum(int(p["quantite"]) * float(p["prix"]) for p in lignes), 2)
    taux = float(livraison.get("taux_tva") or 0) if livraison.get("appliquer_tva") else 0.0
    reductions = livraison.get("reductions") or []
    autres = [{"type": r["type"], "mode": r.get("mode", "montant"), "valeur": r.get("valeur", 0),
               "motif": r.get("motif", "")} for r in reductions if r.get("type") != "ristourne"]
    deja = sum(float(r.get("montant", 0) or 0) for r in reductions if r.get("type") == "ristourne")
    hors_produits = float(livraison.get("dette_ancienne", 0) or 0) + float(livraison.get("frais_retour", 0) or 0)
    cible = float(livraison.get("montant_total", 0) or 0) - montant_ttc

    # Avec la TVA et l'arrondi de la devise, on ajuste la réduction pour
    # tomber sur le total visé (quelques passes suffisent).
    valeur = deja + montant_ttc / (1 + taux / 100)
    for _ in range(4):
        ristourne = {"type": "ristourne", "mode": "montant", "valeur": round(max(valeur, 0), 6), "motif": motif}
        detail, total_reductions = reductions_service.appliquer_reductions(autres + [ristourne], brut, decimales)
        net = round(brut - total_reductions, 2)
        _, _, montant_tva = tva_service.tva_vente(livraison, net)
        total = round(net + montant_tva + hors_produits, 2)
        ecart = total - cible
        if abs(ecart) < 0.005 or net <= 0:
            break
        valeur += ecart / (1 + taux / 100)
    return {
        "montant_brut": round(brut, 2),
        "reductions": detail,
        "total_reductions": total_reductions,
        "montant_produits": net,
        "montant_tva": montant_tva,
        "montant_total": total,
    }


def _ristourne_sur_vente(livraison, montant_ttc, motif):
    """Applique la ristourne puis répartit un éventuel trop-perçu : le crédit
    client utilisé lui est rendu d'abord, le reste devient « à rembourser »."""
    champs = _appliquer_ristourne(livraison, montant_ttc, motif)
    total = champs["montant_total"]
    paye = float(livraison.get("montant_paye", 0) or 0)
    deduit = float(livraison.get("montant_deduit_caisse", 0) or 0)
    retour = float(livraison.get("retour", 0) or 0)
    if paye > total:
        excedent = round(paye - total, 2)
        rendu_credit = min(excedent, deduit)
        if rendu_credit > 0:
            client = ClientModel.get_client_by_id(livraison["client_id"])
            if client:
                ClientModel.update_client(livraison["client_id"], {
                    "montant_en_caisse": round(float(client.get("montant_en_caisse", 0) or 0) + rendu_credit, 2)})
            deduit = round(deduit - rendu_credit, 2)
        retour = round(retour + excedent - rendu_credit, 2)
        paye = total
    if paye <= 0:
        statut = "non payé"
    elif paye < total - 0.005:
        statut = "partiellement payé"
    else:
        statut, paye = "payé", total
    champs.update({
        "montant_paye": paye,
        "montant_deduit_caisse": deduit,
        "retour": retour,
        "statut_paiement": statut,
        "statut_livraison": "livrée" if statut == "payé" else "en cours",
        "updated_at": datetime.utcnow(),
    })
    LivraisonModel.get_collection().update_one({"_id": livraison["_id"]}, {"$set": champs})
    return champs


def _plafond_ristourne(livraison):
    """Montant maximal d'une ristourne : la valeur des produits, TVA comprise
    (la dette ancienne et les frais de retour ne sont pas des produits)."""
    return round(float(livraison.get("montant_produits", 0) or 0) + float(livraison.get("montant_tva", 0) or 0), 2)


def accorder_ristourne_service(livreur_id, livraison_id, montant_ristourne):
    """
    Accorde une ristourne sur une vente : ce que le client doit baisse du
    montant accordé (le total est réduit, le montant déjà payé ne change pas ;
    s'il dépasse le nouveau total, la différence est à rembourser).
    """
    if not ObjectId.is_valid(livraison_id):
        return {"error": "ID de livraison invalide"}, 400

    if isinstance(montant_ristourne, bool) or not isinstance(montant_ristourne, (int, float)) \
            or montant_ristourne <= 0:
        return {"error": "Le montant de la ristourne doit être un nombre positif"}, 400

    livraison = LivraisonModel.get_collection().find_one({
        "_id": ObjectId(livraison_id),
        "livreur_id": ObjectId(livreur_id),
        "deleted_at": {"$exists": False},
    })

    if not livraison:
        return {"error": "Livraison introuvable ou accès non autorisé"}, 404
    erreur_cloture = erreur_vente_verrouillee(livraison)
    if erreur_cloture:
        return erreur_cloture
    plafond = _plafond_ristourne(livraison)
    if montant_ristourne > plafond + 0.005:
        return {"error": f"La ristourne ne peut pas dépasser la valeur des articles ({plafond:g})."}, 400

    try:
        champs = _ristourne_sur_vente(livraison, float(montant_ristourne), "Accordée par le vendeur")
    except ValueError as err:
        return {"error": str(err)}, 400

    # Enregistrement dans la collection ristournes pour le suivi
    RistourneModel.enregistrer_ristourne(
        livraison_id=ObjectId(livraison_id),
        livreur_id=ObjectId(livreur_id),
        client_id=livraison["client_id"],
        montant_total=livraison.get("montant_total", 0),
        montant_paye=livraison.get("montant_paye", 0),
        ristourne=montant_ristourne,
        motif="Accordée par le livreur",
        magasin_id=livraison.get("magasin_id")
    )

    return {"message": "Ristourne accordée avec succès", "montant_total": champs["montant_total"],
            "a_rembourser": champs["retour"]}, 200


def _reste_a_payer(livraison):
    return round(float(livraison.get("montant_total", 0) or 0) - float(livraison.get("montant_paye", 0) or 0), 2)


def _vente_active(livraison_id):
    if not ObjectId.is_valid(str(livraison_id)):
        return None
    return LivraisonModel.get_collection().find_one(
        {"_id": ObjectId(str(livraison_id)), "deleted_at": {"$exists": False}})


def abandon_en_attente(livraison):
    """Une demande d'abandon du reste attend la décision d'un manager.

    Les ventes enregistrées avec l'ancienne case « a couché » (have_ristourne
    sans décision) sont aussi des demandes en attente.
    """
    abandon = livraison.get("abandon_reste") or {}
    if abandon:
        return abandon.get("statut") == "en_attente"
    return bool(livraison.get("have_ristourne")) and float(livraison.get("ristourne", 0) or 0) > 0 \
        and livraison.get("statut_paiement") != "payé"


def _nom_utilisateur(user_id):
    user = UserModel.get_user_by_id(user_id) if user_id else None
    return f"{user.get('first_name', '')} {user.get('name', '')}".strip() if user else None


def _abandonner(livraison, user_id, motif):
    """Abandonne le reste impayé de la vente : il devient une ristourne
    (réduction), le total baisse jusqu'au montant payé et la vente est soldée."""
    erreur_cloture = erreur_vente_verrouillee(livraison)
    if erreur_cloture:
        return erreur_cloture
    abandon = min(_reste_a_payer(livraison), _plafond_ristourne(livraison))
    if abandon <= 0:
        return {"error": "Cette vente n'a pas de reste à abandonner."}, 400
    try:
        _ristourne_sur_vente(livraison, abandon, motif or "Reste abandonné")
    except ValueError as err:
        return {"error": str(err)}, 400
    maintenant = datetime.utcnow()
    precedent = livraison.get("abandon_reste") or {}
    LivraisonModel.get_collection().update_one({"_id": livraison["_id"]}, {"$set": {
        "abandon_reste": {
            **precedent,
            "statut": "valide",
            "montant": abandon,
            "motif": motif or precedent.get("motif") or "",
            "valide_par": str(user_id),
            "valide_par_nom": _nom_utilisateur(user_id),
            "valide_le": maintenant.isoformat(),
        },
        "action_manager": False,
        "have_ristourne": True,
        "ristourne": abandon,  # montant abandonné, affiché sur la vente
        "updated_at": maintenant,
    }})
    if not RistourneModel.find_by_livraison_id(livraison["_id"]):
        RistourneModel.enregistrer_ristourne(
            livraison["_id"], livraison.get("livreur_id"), livraison.get("client_id"),
            livraison.get("montant_total", 0), livraison.get("montant_paye", 0), abandon,
            motif=motif or "Reste abandonné", magasin_id=livraison.get("magasin_id"))
    return {"message": f"Reste de {abandon:g} abandonné : la vente est soldée.", "montant_abandonne": abandon}, 200


def abandonner_reste_service(livraison_id, user_id, motif=None):
    """Admin ou manager : abandonne directement le reste impayé (client qui ne
    paiera pas le reste, geste commercial...)."""
    livraison = _vente_active(livraison_id)
    if not livraison:
        return {"error": "Livraison introuvable."}, 404
    return _abandonner(livraison, user_id, str(motif or "").strip()[:200])


def demander_abandon_service(livraison_id, user_id, motif=None):
    """Gérant : demande l'abandon du reste ; un manager valide ou refuse."""
    livraison = _vente_active(livraison_id)
    if not livraison:
        return {"error": "Livraison introuvable."}, 404
    reste = _reste_a_payer(livraison)
    if reste <= 0:
        return {"error": "Cette vente n'a pas de reste à abandonner."}, 400
    if abandon_en_attente(livraison):
        return {"error": "Une demande est déjà en attente pour cette vente."}, 400
    motif = str(motif or "").strip()[:200]
    if not motif:
        return {"error": "Indiquez pourquoi le reste devrait être abandonné."}, 400
    maintenant = datetime.utcnow()
    LivraisonModel.get_collection().update_one({"_id": livraison["_id"]}, {"$set": {
        "abandon_reste": {"statut": "en_attente", "montant": reste, "motif": motif, "demande_par": str(user_id),
                          "demande_par_nom": _nom_utilisateur(user_id), "demande_le": maintenant.isoformat()},
        "have_ristourne": True,
        "ristourne": reste,
        "updated_at": maintenant,
    }})
    from models.notification_model import NotificationModel
    client = ClientModel.get_client_by_id(livraison["client_id"]) or {}
    NotificationModel.create_notification({
        "title": "Demande d'abandon du reste",
        "message": (f"{_nom_utilisateur(user_id) or 'Un gérant'} demande d'abandonner le reste de {reste:g} "
                    f"de la vente du client {client.get('nom') or 'inconnu'} : « {motif} ». "
                    "Ouvrez la vente pour valider ou refuser."),
        "type": "warning",
        "lien": f"/admin/dashboard/deliverys/detaillivrason/{livraison['_id']}",
        "magasin_id": str(livraison["magasin_id"]),
    })
    return {"message": "Demande envoyée : un manager doit la valider.", "montant": reste}, 200


def valider_ristourne_service(livraison_id, manager_id):
    """Le manager accepte une demande d'abandon du reste (ou une ancienne vente
    « a couché ») : le reste devient une ristourne et la vente est soldée."""
    livraison = _vente_active(livraison_id)
    if not livraison:
        return {"error": "Livraison introuvable."}, 404
    if not abandon_en_attente(livraison):
        return {"error": "Aucune demande d'abandon en attente sur cette vente."}, 400
    motif = (livraison.get("abandon_reste") or {}).get("motif") or "Reste abandonné, validé par le manager"
    return _abandonner(livraison, manager_id, motif)


def refuser_abandon_service(livraison_id, manager_id):
    """Le manager refuse la demande : le client doit toujours le reste."""
    livraison = _vente_active(livraison_id)
    if not livraison:
        return {"error": "Livraison introuvable."}, 404
    if not abandon_en_attente(livraison):
        return {"error": "Aucune demande d'abandon en attente sur cette vente."}, 400
    RistourneModel.delete_by_livraison_id(livraison_id)
    maintenant = datetime.utcnow()
    LivraisonModel.get_collection().update_one({"_id": livraison["_id"]}, {"$set": {
        "abandon_reste": {**(livraison.get("abandon_reste") or {}), "statut": "refuse", "refuse_par": str(manager_id),
                          "refuse_par_nom": _nom_utilisateur(manager_id), "refuse_le": maintenant.isoformat()},
        "action_manager": False,
        "have_ristourne": False,
        "ristourne": 0.0,
        "updated_at": maintenant,
    }})
    return {"message": "Demande refusée : le client doit toujours le reste."}, 200


def get_livraisons_par_methode_paiement_service(methode_paiement, periode=None, livreur_id=None, magasin_id=None):
    """
    Récupère les livraisons filtrées par méthode de paiement (cash, momo, etc.)
    avec un filtre optionnel par période (jour, semaine, mois, annee).
    Si livreur_id est fourni (rôle livreur), filtre uniquement ses livraisons.
    Retourne { total_general, livraisons } conforme au frontend.
    """
    try:
        from datetime import timedelta
        now = datetime.utcnow()

        # Calcul de la date de début selon la période
        date_debut = None
        if periode == "jour":
            date_debut = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif periode == "semaine":
            date_debut = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        elif periode == "mois":
            date_debut = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif periode == "annee":
            date_debut = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

        # Récupère les livraisons selon le rôle
        if livreur_id:
            # Livreur : uniquement ses propres livraisons
            livraisons = LivraisonModel.get_livraisons_by_livreur(livreur_id)
        else:
            # Admin / Manager : toutes les livraisons du magasin
            livraisons = LivraisonModel.get_all_livraisons(magasin_id=magasin_id)

        # Filtrage par méthode de paiement (insensible à la casse)
        filtrees_brutes = [
            l for l in livraisons
            if l.get("methode_paiement", "cash").lower() == methode_paiement.lower()
        ]

        # Filtrage par période si spécifiée
        if date_debut:
            filtrees_brutes = [
                l for l in filtrees_brutes
                if l.get("created_at") and l["created_at"] >= date_debut
            ]

        # Sérialisation
        filtrees = [serialize_livraison(l) for l in filtrees_brutes]

        # Calcul du total général
        total_general = sum(l.get("montant_total", 0) for l in filtrees)

        return {
            "total_general": round(total_general, 2),
            "livraisons": filtrees
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


