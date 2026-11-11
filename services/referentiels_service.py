"""Fournisseurs et catégories : chiffres affichés dans leurs listes."""
from collections import Counter

from bson import ObjectId

from extensions import db


def compter_produits(magasin_id, champ):
    """Nombre de produits actifs (hors corbeille) du magasin par valeur de ``champ``.

    :param champ: "categorie_id" ou "fournisseur_id".
    :return: {identifiant (str): nombre de produits}
    """
    produits = db.tables.produits.find({"magasin_id": ObjectId(str(magasin_id))}, {champ: 1, "deleted_at": 1})
    return Counter(str(p[champ]) for p in produits if p.get(champ) and not p.get("deleted_at"))
