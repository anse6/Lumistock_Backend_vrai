# scripts/recalculer_couts.py
"""Recalcule le coût d'achat de chaque article d'après ses lots.

Avant novembre 2026, un réapprovisionnement remplaçait le coût d'achat de
l'article par le dernier prix payé. Le coût suit maintenant la méthode de
valorisation du magasin (CMUP / FIFO / LIFO) : ce script aligne les articles
déjà enregistrés.

    python -m scripts.recalculer_couts              # aperçu, rien n'est modifié
    python -m scripts.recalculer_couts --appliquer  # enregistre les nouveaux coûts
"""
import sys

from app import create_app
from extensions import db
from models.produit_model import ProduitModel
from services import valorisation_service


class _Apercu(Exception):
    """Annule la transaction en mode aperçu."""


def main(appliquer=False):
    # Pas de planificateur de messages dans un script ponctuel.
    app = create_app({"MESSAGERIE_PLANIFICATEUR": False})
    with app.app_context():
        produits = list(ProduitModel.get_collection().find({"deleted_at": {"$exists": False}}))
        changes = 0
        for p in produits:
            ancien = float(p.get("prix_achat", 0) or 0)
            nouveau = None
            try:
                with db.transaction():
                    nouveau = valorisation_service.recalculer_cout(p["_id"])
                    if "dernier_prix_achat" not in p:
                        ProduitModel.get_collection().update_one(
                            {"_id": p["_id"]}, {"$set": {"dernier_prix_achat": ancien}})
                    if not appliquer:
                        raise _Apercu()
            except _Apercu:
                pass
            nouveau = float(nouveau or 0)
            if abs(nouveau - ancien) > 0.005:
                changes += 1
                print(f"{p.get('nom')!s:<40} {ancien:>12.2f} -> {nouveau:>12.2f}")
        print(f"\n{len(produits)} articles parcourus, {changes} coûts d'achat différents.")
        print("Enregistré." if appliquer else "Aperçu seulement : relancez avec --appliquer pour enregistrer.")


if __name__ == "__main__":
    main("--appliquer" in sys.argv)
