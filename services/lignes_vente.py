# services/lignes_vente.py
"""Contrôles des lignes d'une vente, avant tout calcul de montant ou
mouvement de stock.

Les lignes arrivent du frontend sans schéma strict (``fields.Dict``) : sans ce
contrôle, une quantité négative passait la vérification de stock puis
*augmentait* le stock, et un prix de 0 FCFA était accepté.
"""

import math
from collections import defaultdict


def _entier_positif(valeur):
    """Entier ≥ 1, ou None. Refuse les décimaux (2.5) et les booléens."""
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, int):
        nombre = valeur
    elif isinstance(valeur, float) and math.isfinite(valeur) and valeur.is_integer():
        nombre = int(valeur)
    elif isinstance(valeur, str) and valeur.strip().isdigit():
        nombre = int(valeur.strip())
    else:
        return None
    return nombre if nombre >= 1 else None


def _nombre(valeur):
    """Nombre fini, ou None."""
    if isinstance(valeur, bool):
        return None
    try:
        nombre = float(valeur)
    except (TypeError, ValueError):
        return None
    return nombre if math.isfinite(nombre) else None


def valider_lignes_vente(produits):
    """Vérifie les lignes envoyées pour une vente (création ou modification).

    - chaque ligne vise un produit existant ;
    - la quantité (unités ou conditionnements) est un entier ≥ 1 ;
    - un prix saisi à la main est > 0 et jamais inférieur au prix d'achat de
      la fiche produit (pas de vente à perte). Les remises passent par les
      réductions de la vente, pas par le prix unitaire.

    :return: ``(message, code HTTP)`` à renvoyer, ou None si toutes les
             lignes sont valides.
    """
    from models.produit_model import ProduitModel

    if not isinstance(produits, list) or not produits:
        return "La vente doit contenir au moins un article.", 400

    for ligne in produits:
        if not isinstance(ligne, dict) or not ligne.get("produit_id"):
            return "Ligne d'article invalide.", 400
        produit_id = ligne["produit_id"]
        try:
            produit = ProduitModel.get_by_id(str(produit_id))
        except Exception:  # identifiant mal formé
            produit = None
        if not produit:
            return f"Article {produit_id} introuvable", 404
        nom = produit.get("nom") or str(produit_id)

        if "conditionnement_id" in ligne and "quantite_conditionnement" in ligne:
            if _entier_positif(ligne.get("quantite_conditionnement")) is None:
                return f"Quantité invalide pour « {nom} » : un nombre entier supérieur à 0 est attendu.", 400
            # Le prix d'un conditionnement est celui fixé sur la fiche produit.
            continue

        if _entier_positif(ligne.get("quantite")) is None:
            return f"Quantité invalide pour « {nom} » : un nombre entier supérieur à 0 est attendu.", 400

        if ligne.get("prix") is None:
            continue
        prix = _nombre(ligne.get("prix"))
        if prix is None or prix <= 0:
            return f"Prix de vente invalide pour « {nom} » : il doit être supérieur à 0.", 400
        prix_achat = _nombre(produit.get("prix_achat")) or 0
        if prix_achat > 0 and round(prix, 6) < round(prix_achat, 6):
            # Le montant n'est pas affiché : le gérant ne voit pas les prix d'achat.
            return f"Prix de vente refusé pour « {nom} » : il est inférieur au prix d'achat de l'article.", 400
    return None


def quantites_par_produit(lignes):
    """Quantité totale demandée par produit : un même produit présent sur
    plusieurs lignes (unités + conditionnements, ou doublon) est additionné
    avant la comparaison avec le stock."""
    totaux = defaultdict(int)
    for ligne in lignes:
        totaux[str(ligne["produit_id"])] += int(ligne["quantite"])
    return totaux
