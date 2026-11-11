from models.retour_model import RetourLivraisonModel
from services.cloture_service import erreur_vente_verrouillee
from models.client_model import ClientModel
from bson import ObjectId
from datetime import datetime, timedelta, date
from collections import defaultdict
from dateutil.relativedelta import relativedelta
from models.livraison_model import LivraisonModel
from models.stock_livreur_model import StockLivreurModel
from schemas.livraison_schema import LivraisonSchema, LivraisonManagerSchema, RetourLivraisonSchema
from marshmallow import ValidationError
from models.produit_model import ProduitModel
from models.client_model import ClientModel
from models.retour_model import RetourLivraisonModel
from models.user_model import UserModel
from models.ristourne_model import RistourneModel


def get_week_date_range(year, week):
    """Retourne le lundi et le dimanche de la semaine ISO donnée."""
    first_day = date.fromisocalendar(year, week, 1)
    last_day = first_day + timedelta(days=6)
    return first_day, last_day


def get_stats_retours_par_periode_total(livreur_id, granularite="jour", page=0, magasin_id=None):
    
    if livreur_id:
        retours = RetourLivraisonModel.get_retours_by_livreur(livreur_id)
    else:
        retours = RetourLivraisonModel.get_all_retours(magasin_id)
    
    #print(f"Retours trouvés : {len(retours)}")
    #print("Retours :", retours)
    
    retours_par_periode = defaultdict(float)
    
    for r in retours:
        date_obj = r.get("created_at")
        if not date_obj:
            continue
        montant_retour = r.get("montant_retour", 0)
        
        if granularite == "jour":
            key = date_obj.strftime("%Y-%m-%d")
        elif granularite == "semaine":
            iso_year, iso_week, _ = date_obj.isocalendar()
            key = f"{iso_year}-W{iso_week:02d}"
        elif granularite == "mois":
            key = f"{date_obj.year}-{date_obj.month:02d}"
        elif granularite == "annee":
            key = str(date_obj.year)
        else:
            raise ValueError("Granularité non supportée")
        
        retours_par_periode[key] += montant_retour

    now = date.today()
    periodes = []
    
    if granularite == "jour":
        current_monday = now - timedelta(days=now.weekday())
        start = current_monday + timedelta(weeks=page)
        for i in range(7):
            day = start + timedelta(days=i)
            key = day.strftime("%Y-%m-%d")
            periodes.append((key, day, day))
    
    elif granularite == "semaine":
        current_year, current_week, _ = now.isocalendar()
        start_week = current_week + (page * 4)
        start_year = current_year
        for i in range(4):
            week = start_week + i
            year = start_year
            while week > 52:
                week -= 52
                year += 1
            while week < 1:
                week += 52
                year -= 1
            start, end = get_week_date_range(year, week)
            key = f"{year}-W{week:02d}"
            periodes.append((key, start, end))
    elif granularite == "mois":
        base = now.replace(day=1) + relativedelta(months=page * 4)
        for i in range(4):
            d = base + relativedelta(months=i)
            key = f"{d.year}-{d.month:02d}"
            start = d
            end = (d + relativedelta(months=1)) - timedelta(days=1)
            periodes.append((key, start, end))
    
    elif granularite == "annee":
        start_year = now.year + (page * 4)
        for i in range(4):
            y = start_year + i
            key = str(y)
            start = date(y, 1, 1)
            end = date(y, 12, 31)
            periodes.append((key, start, end))


    # Génération des résulats
    detail = [] 
    retour_total = 0.0
    for key, start, end in periodes:
        montant = retours_par_periode.get(key, 0.0)
        retour_total += montant
        entry = {
            "periode": key,
            "total_retours": round(montant, 2),
            "start": str(start),
            "fin": str(end)
        }
        
        if montant == 0 and end > now:
            entry["incomplet"] = True
        
        if granularite == "semaine":
            entry["plage_dates"] = f"{start} au {end}"
        detail.append(entry)
        
    variation = 0.0
    if len(detail) >= 2:
        montant_precedent = detail[-2]["total_retours"]
        montant_courant = detail[-1]["total_retours"]
        if montant_precedent == 0:
            variation = 100.0 if montant_courant > 0 else 0.0
        else:                                   
            variation = ((montant_courant - montant_precedent) / montant_precedent) * 100
    return {
        "valeur": round(retour_total, 2),
        "variation": round(variation, 2),
        "granularite": granularite,
        "page": page,
        "detail": detail,
    }
    
    
    
    # Service pour gérer le retour d'une livraison


def creer_retour_livraison(data):
    """
    Crée un retour de livraison avec gestion dynamique de la ristourne,
    incluant le retour de conditionnements complets (quantite_conditionnement),
    sans casser le comportement existant.
    """
    try:
        schema = RetourLivraisonSchema()
        validated_data = schema.load(data)
    except ValidationError as err:
        return {"error": err.messages}, 400

    livraison_id = validated_data["livraison_id"]
    produits = validated_data["produits"]

    livraison = LivraisonModel.get_livraison_by_id(livraison_id)
    if not livraison:
        return {"error": "Livraison introuvable"}, 404
    # Un retour est autorisé même sur une vente d'une période clôturée : la
    # comptabilité le compte à SA date (période en cours), la période close
    # ne change pas (voir compta_service).

    produits_livres = livraison.get("produits", [])
    client_id = livraison.get("client_id")
    validated_data["client_id"] = client_id
    # Le stock d'une vente de gérant revient toujours à CE gérant, jamais à
    # celui indiqué par le client HTTP.
    if livraison.get("livreur_id"):
        validated_data["livreur_id"] = str(livraison["livreur_id"])

    # Indexation des produits livrés
    produits_livres_dict = {}
    for p in produits_livres:
        key = (
            str(p["produit_id"]),
            str(p.get("conditionnement_id")) if p.get("is_conditionne") else "none"
        )
        produits_livres_dict[key] = p

    montant_ristourne_retour = 0
    total_retournes = {}
    # Articles suivis par numéro de série : quelles unités reviennent.
    from services import numeros_serie_service as ns
    retours_serie = {}

    for produit in produits:
        key = (
            str(produit["produit_id"]),
            str(produit.get("conditionnement_id")) if produit.get("is_conditionne") else "none"
        )
        produit_livre = produits_livres_dict.get(key)

        if not produit_livre:
            return {"error": f"Article non trouvé dans la livraison (clé: {key})"}, 400

        # Quantité réelle à retourner, en unités : « quantite » (unités) et/ou
        # « quantite_conditionnement » (conditionnements entiers × facteur).
        try:
            quantite_retour = int(produit.get("quantite", 0) or 0)
            if "quantite_conditionnement" in produit:
                facteur = int(produit_livre.get("facteur", 1) or 1)
                quantite_retour += int(produit["quantite_conditionnement"] or 0) * facteur
        except (TypeError, ValueError):
            return {"error": "Quantité retournée invalide"}, 400

        if quantite_retour <= 0:
            return {"error": f"Quantité retournée invalide pour l'article {produit['produit_id']}"}, 400

        quantite_livree = produit_livre["quantite"]
        deja_retournee = total_retournes.get(key, 0)

        if quantite_retour + deja_retournee > quantite_livree:
            return {
                "error": f"Retour impossible : la quantité ({quantite_retour}) dépasse la quantité livrée ({quantite_livree}) pour l'article {produit['produit_id']}"
            }, 400

        total_retournes[key] = deja_retournee + quantite_retour

        produit_db = ProduitModel.get_by_id(str(produit["produit_id"]))
        if produit_livre.get("numeros_serie") or ns.est_suivi(produit_db):
            try:
                numeros = ns._liste(produit.get("numeros_serie"))
            except ns.ErreurNumeroSerie as err:
                return {"error": str(err)}, 400
            vendus = {ns.cle(n): n for n in produit_livre.get("numeros_serie") or []}
            nom = produit_livre.get("nom") or produit["produit_id"]
            if len(numeros) != quantite_retour:
                return {"error": f"« {nom} » est suivi par numéro de série : choisissez les {quantite_retour} "
                                 f"numéro(s) retourné(s) ({len(numeros)} choisi(s))."}, 400
            hors_vente = [n for n in numeros if ns.cle(n) not in vendus]
            if hors_vente:
                return {"error": f"Numéro(s) de série qui ne font pas partie de cette vente : "
                                 f"{', '.join(hors_vente[:5])}."}, 400
            deja = {ns.cle(n) for n in retours_serie.get(key, [])}
            if any(ns.cle(n) in deja for n in numeros) or len({ns.cle(n) for n in numeros}) != len(numeros):
                return {"error": "Un numéro de série est retourné deux fois."}, 400
            retours_serie.setdefault(key, []).extend(vendus[ns.cle(n)] for n in numeros)
            produit["numeros_serie"] = [vendus[ns.cle(n)] for n in numeros]


    # Réintégration du stock
    # Les articles retournés reviennent dans le stock valorisé au coût auquel
    # ils étaient sortis (avant tout mouvement de stock).
    from services import valorisation_service
    for key, q_retournee in total_retournes.items():
        valorisation_service.restituer_partiel_ligne(produits_livres_dict[key], q_retournee)

    # Même règle que l'annulation : une vente de gérant (même validée ensuite
    # par un manager, qui y inscrit son manager_id) rend le stock au gérant.
    from services.livraison_service import est_livraison_manager
    if not est_livraison_manager(livraison):
        # On s'assure que chaque produit a bien une clé "quantite"
        for produit in produits:
            quantite = produit.get("quantite", 0)
            if "quantite_conditionnement" in produit:
                key = (
                    str(produit["produit_id"]),
                    str(produit.get("conditionnement_id")) if produit.get("is_conditionne") else "none"
                )
                facteur = produits_livres_dict[key].get("facteur", 1)
                quantite += produit["quantite_conditionnement"] * facteur
                produit["quantite"] = quantite  # Mise à jour de la quantité
       
        StockLivreurModel.ajouter_stock(validated_data["livreur_id"], produits)
        # ✅ ENRICHISSEMENT
        client = ClientModel.get_client_by_id(client_id)
        livreur = UserModel.find_by_id(validated_data.get("livreur_id"))
        acteur_retour_livreur = {
            "admin_id": validated_data.get("manager_id"),
            "client_id": str(client_id) if client_id else None,
            "client_nom": client.get("nom") if client else "Inconnu",
            "livreur_id": str(validated_data.get("livreur_id")),
            "livreur_nom": f"{livreur.get('first_name', '')} {livreur.get('name', '')}".strip() if livreur else "Inconnu"
        }
        for produit in produits:
            ProduitModel.ajouter_mouvement_stock(produit["produit_id"], {
                "type": "retour produit dans stock livreur",
                "quantite": produit.get("quantite", 0),
                "acteur": acteur_retour_livreur
            })
    else:
        # ✅ ENRICHISSEMENT
        client = ClientModel.get_client_by_id(client_id)
        acteur_retour_magasin = {
            "admin_id": validated_data.get("manager_id"),
            "client_id": str(client_id) if client_id else None,
            "client_nom": client.get("nom") if client else "Inconnu"
        }
        for produit in produits:
            quantite = produit.get("quantite", 0)
            if "quantite_conditionnement" in produit:
                key = (
                    str(produit["produit_id"]),
                    str(produit.get("conditionnement_id")) if produit.get("is_conditionne") else "none"
                )
                facteur = produits_livres_dict[key].get("facteur", 1)
                quantite += produit["quantite_conditionnement"] * facteur
            
            ProduitModel.increment_stock(
                produit_id=produit["produit_id"],
                quantite=quantite,
                acteur=acteur_retour_magasin,
                type_mouvement="retour dans stock magasin"
            )

    # ─── CORRECTION BUG : inclure la dette_ancienne dans le recalcul ───────────
    # La dette_ancienne fait partie du montant_total original et ne doit pas
    # disparaître lors d'un retour partiel de produits.
    dette_ancienne = float(livraison.get("dette_ancienne", 0) or 0)

    for key, numeros in retours_serie.items():
        try:
            ns.rentrer(key[0], numeros, "retour", {"admin_id": validated_data.get("manager_id")},
                       vente_id=livraison_id, motif=validated_data.get("motif"))
        except ns.ErreurNumeroSerie as err:
            return {"error": str(err)}, err.statut
        revenus = {ns.cle(n) for n in numeros}
        ligne = produits_livres_dict[key]
        ligne["numeros_serie"] = [n for n in ligne.get("numeros_serie") or [] if ns.cle(n) not in revenus]
        ligne.setdefault("numeros_serie_retournes", []).extend(numeros)

    # Mise à jour des produits livrés (on soustrait les quantités retournées)
    nouvelle_quantite = 0
    nouveau_montant_produits = 0
    for key, q_retournee in total_retournes.items():
        produit_livre = produits_livres_dict[key]
        produit_livre["quantite"] = max(0, produit_livre["quantite"] - q_retournee)

    for p in produits_livres:
        nouvelle_quantite += p["quantite"]
        nouveau_montant_produits += p["quantite"] * p["prix"]

    # Les réductions commerciales s'appliquent au montant restant après retour.
    from services.reductions_service import appliquer_reductions
    # Arrondi à l'unité de la devise : rendre 1 bouteille d'une palette à
    # 1 700/6 ne doit pas laisser 1 416,67 FCFA ni rembourser 283,33.
    from services.tva_service import arrondir, decimales_magasin
    montant_brut = arrondir(nouveau_montant_produits, decimales_magasin(livraison.get("magasin_id")))
    reductions, total_reductions = appliquer_reductions(
        livraison.get("reductions") or [], montant_brut, decimales_magasin(livraison.get("magasin_id")))
    nouveau_montant_produits = round(montant_brut - total_reductions, 2)

    # TVA recalculée au taux de la vente sur le montant restant.
    from services.tva_service import tva_vente
    appliquer_tva, taux_tva, nouveau_montant_tva = tva_vente(livraison, nouveau_montant_produits)

    # Valeur nette des articles retournés (après réductions, TVA comprise).
    ancien_montant_produits = float(livraison.get("montant_produits", 0) or 0)
    ancien_montant_tva = float(livraison.get("montant_tva", 0) or 0)
    valeur_retour = round(max((ancien_montant_produits + ancien_montant_tva)
                              - (nouveau_montant_produits + nouveau_montant_tva), 0.0), 2)

    # Frais de retour : le magasin peut garder un pourcentage de la valeur
    # retournée (politique du magasin, ou choix au cas par cas). Ils restent dus
    # par le client : le montant à rembourser baisse d'autant.
    pourcentage = validated_data.get("retenue_pourcentage")
    if pourcentage is None:
        from services.cloture_service import politique_retour
        pourcentage = politique_retour(livraison.get("magasin_id"))
    decimales = decimales_magasin(livraison.get("magasin_id"))
    frais_retour = round(valeur_retour * float(pourcentage or 0) / 100, decimales)
    frais_cumules = round(float(livraison.get("frais_retour", 0) or 0) + frais_retour, 2)
    # Les frais sont calculés sur un montant TVA comprise : quand la vente a de
    # la TVA, ils en contiennent aussi (prestation taxable). Part HT = recette
    # (compte 7078), part TVA = TVA collectée.
    if appliquer_tva and taux_tva:
        frais_retour_ht = round(frais_retour / (1 + float(taux_tva) / 100), decimales)
    else:
        frais_retour_ht = frais_retour
    frais_retour_tva = round(frais_retour - frais_retour_ht, 2)

    # ✅ montant_total = produits restants + TVA + dette_ancienne (toujours incluse) + frais de retour
    nouveau_montant_total = round(nouveau_montant_produits + nouveau_montant_tva + dette_ancienne + frais_cumules, 2)

    # Coût d'achat des articles retournés (ils reviennent en stock).
    cout_retour = round(sum(q * float(produits_livres_dict[k].get("prix_achat", 0) or 0)
                            for k, q in total_retournes.items()), 2)

    # Le client avait peut-être payé plus que le nouveau total : ce trop-perçu
    # est « à rembourser ». Il n'est PAS crédité automatiquement sur son compte
    # (sinon un remboursement ultérieur le paierait deux fois) : le
    # remboursement choisit entre restitution et crédit du compte client.
    ancien_montant_paye = float(livraison.get("montant_paye", 0) or 0)
    surplus_client = round(max(0.0, ancien_montant_paye - nouveau_montant_total), 2)
    nouveau_montant_paye = min(ancien_montant_paye, nouveau_montant_total)

    # Nouveau statut de paiement
    if nouveau_montant_paye <= 0:
        statut_paiement = "non payé"
    elif nouveau_montant_paye < nouveau_montant_total:
        statut_paiement = "partiellement payé"
    else:
        statut_paiement = "payé"

    # Montant total restant à rembourser au client (cumul des retours).
    a_rembourser = round(float(livraison.get("retour", 0) or 0) + surplus_client, 2)

    # Enregistrement du retour
    validated_data['montant_retour'] = valeur_retour
    # Historique lisible : nom du produit et quantité retournée en unités.
    validated_data['produits'] = [
        {
            "produit_id": str(produits_livres_dict[key]["produit_id"]),
            "nom": produits_livres_dict[key].get("nom"),
            "quantite": q,
            "is_conditionne": bool(produits_livres_dict[key].get("is_conditionne")),
            "conditionnement_id": produits_livres_dict[key].get("conditionnement_id"),
            "conditionnement_nom": produits_livres_dict[key].get("conditionnement_nom"),
        }
        for key, q in total_retournes.items()
    ]
    validated_data['montant_a_rembourser'] = surplus_client
    # Détail comptable du retour (compté à sa date, pas à celle de la vente).
    validated_data['montant_ht'] = round(max(ancien_montant_produits - nouveau_montant_produits, 0), 2)
    validated_data['montant_tva'] = round(max(ancien_montant_tva - nouveau_montant_tva, 0), 2)
    validated_data['cout_retour'] = cout_retour
    validated_data['retenue_pourcentage'] = float(pourcentage or 0)
    validated_data['frais_retour'] = frais_retour
    validated_data['frais_retour_ht'] = frais_retour_ht
    validated_data['frais_retour_tva'] = frais_retour_tva
    retour_id = RetourLivraisonModel.enregistrer_retour(validated_data, magasin_id=livraison.get("magasin_id"))


    LivraisonModel.get_collection().update_one(
        {"_id": ObjectId(livraison_id)},
        {
            "$set": {
                "produits": produits_livres,
                "quantite": nouvelle_quantite,
                "montant_produits": nouveau_montant_produits,   # ✅ CORRECTION : champ montant_produits mis à jour
                "montant_brut": montant_brut,
                "reductions": reductions,
                "total_reductions": total_reductions,
                "montant_tva": nouveau_montant_tva,
                "montant_total": nouveau_montant_total,          # ✅ CORRECTION : inclut dette_ancienne
                "frais_retour": frais_cumules,
                "montant_paye": nouveau_montant_paye,
                "retour": a_rembourser,
                # Valeur cumulée des marchandises retournées (ne baisse pas au remboursement).
                "montant_retourne": round(float(livraison.get("montant_retourne", 0) or 0) + valeur_retour, 2),
                "have_retour": True,
                "statut_paiement": statut_paiement,
                "updated_at": datetime.utcnow()
            }
        }
    )

    return {
        "message": "Retour enregistré avec succès",
        "retour_id": retour_id,
        "montant_retour": valeur_retour,
        "montant_a_rembourser": surplus_client,
        "total_a_rembourser": a_rembourser,
        "frais_retour": frais_retour,
        "frais_retour_ht": frais_retour_ht,
        "frais_retour_tva": frais_retour_tva,
        "retenue_pourcentage": float(pourcentage or 0),
    }, 201