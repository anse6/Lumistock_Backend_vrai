# routes/rapport_routes.py
"""Rapports de la page Rapports (ventes, encaissements, stock).

Tous les rapports sont limités au magasin de l'utilisateur connecté. Un
livreur ou un gérant ne voit que ses propres ventes et encaissements.
"""
from datetime import datetime

from bson import ObjectId
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from services.rapport_service import (
    periode,
    rapport_encaissements,
    rapport_inventaire,
    rapport_mouvements,
    rapport_stock_annuel,
    rapport_ventes,
)
from utils.decorators import role_required
from utils.inject_magasin_id import inject_magasin_id

rapport_bp = Blueprint("rapport_routes", __name__, url_prefix="/rapports")


def _contexte():
    """(magasin_id, livreur_id ou None) ; lève une exception si non rattaché."""
    magasin_id = inject_magasin_id({})["magasin_id"]
    livreur_id = get_jwt_identity() if get_jwt().get("role") in ("livreur", "gerant") else None
    return magasin_id, livreur_id


def _filtre_id(nom):
    valeur = (request.args.get(nom) or "").strip()
    return valeur if ObjectId.is_valid(valeur) else None


def _periode(defaut="mois"):
    return periode(request.args.get("debut"), request.args.get("fin"), defaut)


def _executer(fonction):
    try:
        return jsonify(fonction()), 200
    except PermissionError as erreur:
        return jsonify({"error": str(erreur)}), 403
    except Exception as erreur:  # magasin introuvable, données invalides…
        return jsonify({"error": str(erreur)}), 400


@rapport_bp.route("/ventes", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur", "gerant")
def ventes():
    """
    Rapport des ventes de la période
    ---
    tags: [Rapports]
    parameters:
      - {name: debut, in: query, type: string, description: "AAAA-MM-JJ (défaut : 1er du mois)"}
      - {name: fin, in: query, type: string, description: "AAAA-MM-JJ (défaut : aujourd'hui)"}
      - {name: client_id, in: query, type: string}
      - {name: vendeur_id, in: query, type: string}
      - {name: produit_id, in: query, type: string}
      - {name: statut, in: query, type: string, enum: [paye, partiel, non_paye]}
    responses:
      200: {description: "Ventes, totaux (HT, TVA, TTC, payé, reste, marge), meilleurs produits"}
    """
    def calcul():
        magasin_id, livreur_id = _contexte()
        debut, fin = _periode()
        statut = request.args.get("statut")
        filtres = {
            "client_id": _filtre_id("client_id"),
            "vendeur_id": _filtre_id("vendeur_id"),
            "produit_id": _filtre_id("produit_id"),
            "statut": statut if statut in ("paye", "partiel", "non_paye") else None,
        }
        return rapport_ventes(magasin_id, debut, fin, filtres, livreur_id)
    return _executer(calcul)


@rapport_bp.route("/encaissements", methods=["GET"])
@jwt_required()
@role_required("admin", "manager", "livreur", "gerant")
def encaissements():
    """
    Encaissements et remboursements de la période, par mode de paiement
    ---
    tags: [Rapports]
    parameters:
      - {name: debut, in: query, type: string}
      - {name: fin, in: query, type: string}
      - {name: mode, in: query, type: string, description: "cash, om, momo, bancaire, credit_client"}
      - {name: sens, in: query, type: string, enum: [entree, sortie]}
    responses:
      200: {description: "Opérations, totaux par mode, solde net"}
    """
    def calcul():
        magasin_id, livreur_id = _contexte()
        debut, fin = _periode()
        sens = request.args.get("sens")
        filtres = {
            "mode": (request.args.get("mode") or "").strip().lower() or None,
            "sens": sens if sens in ("entree", "sortie") else None,
        }
        return rapport_encaissements(magasin_id, debut, fin, filtres, livreur_id)
    return _executer(calcul)


@rapport_bp.route("/mouvements", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def mouvements():
    """
    Journal des mouvements de stock de la période
    ---
    tags: [Rapports]
    parameters:
      - {name: debut, in: query, type: string, description: "défaut : 1er janvier"}
      - {name: fin, in: query, type: string}
      - {name: famille, in: query, type: string, enum: [approvisionnements, ventes, retours, pertes, livreurs, ajustements]}
      - {name: sens, in: query, type: string, enum: [entree, sortie]}
      - {name: produit_id, in: query, type: string}
    responses:
      200: {description: "Mouvements, totaux par nature"}
    """
    def calcul():
        magasin_id, _ = _contexte()
        debut, fin = _periode("annee")
        sens = request.args.get("sens")
        filtres = {
            "famille": request.args.get("famille") or None,
            "sens": sens if sens in ("entree", "sortie") else None,
            "produit_id": _filtre_id("produit_id"),
        }
        return rapport_mouvements(magasin_id, debut, fin, filtres)
    return _executer(calcul)


@rapport_bp.route("/stock-annuel", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def stock_annuel():
    """
    Mouvement de stock annuel par produit
    ---
    tags: [Rapports]
    parameters:
      - {name: annee, in: query, type: integer, description: "défaut : année en cours"}
      - {name: categorie_id, in: query, type: string}
      - {name: produit_id, in: query, type: string}
      - {name: avec_mouvements, in: query, type: boolean, description: "uniquement les produits mouvementés"}
    responses:
      200: {description: "Stock initial, entrées et sorties par nature, stock final et valeurs ; détail mensuel"}
    """
    def calcul():
        magasin_id, _ = _contexte()
        annee = request.args.get("annee", type=int) or datetime.now().year
        if not 2000 <= annee <= datetime.now().year + 1:
            raise ValueError("Année invalide")
        filtres = {
            "categorie_id": _filtre_id("categorie_id"),
            "produit_id": _filtre_id("produit_id"),
            "avec_mouvements": request.args.get("avec_mouvements") in ("1", "true", "oui"),
        }
        return rapport_stock_annuel(magasin_id, annee, filtres)
    return _executer(calcul)


@rapport_bp.route("/inventaire", methods=["GET"])
@jwt_required()
@role_required("admin", "manager")
def inventaire():
    """
    État actuel du stock, valorisé
    ---
    tags: [Rapports]
    parameters:
      - {name: categorie_id, in: query, type: string}
      - {name: etat, in: query, type: string, enum: [en_stock, critique, rupture]}
    responses:
      200: {description: "Produits, quantités, valeurs d'achat et de vente"}
    """
    def calcul():
        magasin_id, _ = _contexte()
        etat = request.args.get("etat")
        filtres = {
            "categorie_id": _filtre_id("categorie_id"),
            "etat": etat if etat in ("en_stock", "critique", "rupture") else None,
        }
        return rapport_inventaire(magasin_id, filtres)
    return _executer(calcul)
