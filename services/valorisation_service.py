# services/valorisation_service.py
"""Valorisation des stocks : CMUP, FIFO (PEPS) et LIFO (DEPS).

Principe
--------
Chaque produit garde la liste de ses *lots d'achat* (champ ``lots``) :
une entrée en stock (création, réapprovisionnement, ajustement, retour)
ajoute un lot avec sa quantité et son prix d'achat unitaire.

Le stock valorisé d'un produit appartient à l'entreprise tant qu'il n'est
pas vendu : il comprend le stock du magasin ET celui déjà remis aux gérants
(un approvisionnement de gérant n'est qu'un transfert interne, il ne
consomme aucun lot). Les lots sont consommés à la vente (livraison) ou lors
d'une perte (avarie, ajustement à la baisse), selon la méthode du magasin :

- CMUP : coût moyen unitaire pondéré des lots restants. La consommation est
  répartie au prorata de chaque lot, ce qui conserve le coût moyen.
- FIFO : les lots les plus anciens sortent en premier.
- LIFO : les lots les plus récents sortent en premier.

Le coût calculé est enregistré sur chaque ligne de livraison
(``prix_achat`` = coût unitaire, ``lots_consommes`` = détail). Les rapports
existants (compte de résultat, bénéfice) utilisent déjà ``quantite × prix_achat``
des lignes : ils suivent donc automatiquement la méthode choisie.
"""

import uuid
from datetime import datetime

from bson import ObjectId

from models.magasin_model import MagasinModel
from models.produit_model import ProduitModel
from models.stock_livreur_model import StockLivreurModel

METHODE_PAR_DEFAUT = "CMUP"
METHODES = {
    "CMUP": {
        "nom": "CMUP — Coût moyen unitaire pondéré",
        "description": "Chaque sortie est valorisée au coût moyen de tout le stock disponible. "
                       "Le coût moyen est recalculé à chaque entrée.",
    },
    "FIFO": {
        "nom": "FIFO / PEPS — Premier entré, premier sorti",
        "description": "Les articles achetés en premier sont vendus en premier : "
                       "les sorties prennent le prix des lots les plus anciens.",
    },
    "LIFO": {
        "nom": "LIFO / DEPS — Dernier entré, premier sorti",
        "description": "Les derniers articles achetés sont vendus en premier : "
                       "les sorties prennent le prix des lots les plus récents.",
    },
}
_EPS = 1e-9


# --------------------------------------------------------------------------
# Méthode du magasin
# --------------------------------------------------------------------------
def get_methode(magasin_id):
    """Méthode de valorisation du magasin (CMUP par défaut)."""
    if not magasin_id or not ObjectId.is_valid(str(magasin_id)):
        return METHODE_PAR_DEFAUT
    magasin = MagasinModel.get_collection().find_one({"_id": ObjectId(str(magasin_id))})
    methode = (magasin or {}).get("methode_valorisation") or METHODE_PAR_DEFAUT
    return methode if methode in METHODES else METHODE_PAR_DEFAUT


def set_methode(magasin_id, methode):
    methode = str(methode or "").upper()
    if methode not in METHODES:
        raise ValueError(f"Méthode inconnue : {methode}. Valeurs possibles : {', '.join(METHODES)}.")
    MagasinModel.get_collection().update_one(
        {"_id": ObjectId(str(magasin_id))},
        {"$set": {"methode_valorisation": methode, "methode_valorisation_modifiee_le": datetime.utcnow()}},
    )
    return methode


def lister_methodes():
    return [{"code": code, **infos} for code, infos in METHODES.items()]


# --------------------------------------------------------------------------
# Lots
# --------------------------------------------------------------------------
def nouveau_lot(quantite, prix_unitaire, origine, date=None):
    return {
        "id": uuid.uuid4().hex[:12],
        "date": date or datetime.utcnow(),
        "quantite_initiale": float(quantite),
        "quantite_restante": float(quantite),
        "prix_unitaire": float(prix_unitaire or 0),
        "origine": origine,
    }


def _stock_total(produit):
    """Unités non vendues : stock magasin + stock déjà remis aux gérants."""
    total = float(produit.get("quantite", 0) or 0)
    for stock in StockLivreurModel.get_collection().find({"produit_id": ObjectId(str(produit["_id"]))}):
        total += float(stock.get("quantite", 0) or 0)
    return total


def charger_lots(produit_id):
    """Retourne (produit, lots). Initialise les lots d'un produit créé avant
    la valorisation : un lot unique au prix d'achat actuel, pour tout le
    stock non vendu (magasin + gérants)."""
    produit = ProduitModel.get_collection().find_one({"_id": ObjectId(str(produit_id))})
    if not produit:
        return None, []
    lots = produit.get("lots")
    if lots is None:
        total = _stock_total(produit)
        lots = [nouveau_lot(total, produit.get("prix_achat", 0), "stock existant", produit.get("created_at"))] if total > 0 else []
        _enregistrer(produit["_id"], lots)
    return produit, lots


def _enregistrer(produit_id, lots):
    """Enregistre les lots et recalcule le coût d'achat de l'article.

    Comme dans Odoo, le coût d'achat (``prix_achat``) n'est pas le dernier
    prix payé : c'est la valeur du stock restant divisée par sa quantité.
    - CMUP : c'est le coût moyen pondéré, recalculé à chaque entrée.
    - FIFO / LIFO : c'est le coût moyen des lots qui restent après les
      sorties (les plus anciens sont partis en FIFO, les plus récents en LIFO).
    Ainsi « quantité × coût d'achat » donne toujours la valeur réelle du stock
    dans les rapports. Sans stock, le dernier coût connu est conservé.
    """
    lots = [lot for lot in lots if lot["quantite_restante"] > _EPS or lot["quantite_initiale"] > _EPS]
    modifs = {"lots": lots}
    restants = [lot for lot in lots if lot["quantite_restante"] > _EPS]
    if sum(lot["quantite_restante"] for lot in restants) > _EPS:
        modifs["prix_achat"] = round(_cout_moyen(restants), 4)
    ProduitModel.get_collection().update_one({"_id": ObjectId(str(produit_id))}, {"$set": modifs})


def _cout_moyen(lots, defaut=0.0):
    quantite = sum(lot["quantite_restante"] for lot in lots)
    if quantite <= _EPS:
        return float(defaut or 0)
    return sum(lot["quantite_restante"] * lot["prix_unitaire"] for lot in lots) / quantite


def entree(produit_id, quantite, prix_unitaire, origine):
    """Ajoute un lot d'achat (réapprovisionnement, ajustement, retour…)."""
    quantite = float(quantite or 0)
    if quantite <= 0:
        return None
    produit, lots = charger_lots(produit_id)
    if produit is None:
        return None
    lot = nouveau_lot(quantite, prix_unitaire, origine)
    _enregistrer(produit_id, lots + [lot])
    return lot


def sortie(produit_id, quantite, methode):
    """Consomme ``quantite`` unités selon la méthode et retourne le coût.

    :return: dict(cout_unitaire, cout_total, lots_consommes, methode)
    """
    quantite = float(quantite or 0)
    produit, lots = charger_lots(produit_id)
    if produit is None or quantite <= 0:
        return {"cout_unitaire": 0.0, "cout_total": 0.0, "lots_consommes": [], "methode": methode}

    disponibles = [lot for lot in lots if lot["quantite_restante"] > _EPS]
    stock = sum(lot["quantite_restante"] for lot in disponibles)
    a_servir = min(quantite, stock)
    consommes = []

    if methode == "CMUP":
        # Prorata de chaque lot : le coût moyen des lots restants est inchangé.
        for lot in disponibles:
            part = round(a_servir * lot["quantite_restante"] / stock, 6) if stock else 0
            part = min(part, lot["quantite_restante"])
            if part > _EPS:
                lot["quantite_restante"] = round(lot["quantite_restante"] - part, 6)
                consommes.append({"lot_id": lot["id"], "quantite": part, "prix_unitaire": lot["prix_unitaire"]})
    else:
        ordre = sorted(disponibles, key=lambda lot: _date(lot["date"]), reverse=(methode == "LIFO"))
        reste = a_servir
        for lot in ordre:
            if reste <= _EPS:
                break
            part = min(reste, lot["quantite_restante"])
            lot["quantite_restante"] = round(lot["quantite_restante"] - part, 6)
            reste = round(reste - part, 6)
            consommes.append({"lot_id": lot["id"], "quantite": part, "prix_unitaire": lot["prix_unitaire"]})

    # Stock insuffisant dans les lots (données anciennes incohérentes) :
    # la part manquante est valorisée au coût moyen, sinon au prix d'achat.
    manque = round(quantite - sum(c["quantite"] for c in consommes), 6)
    if manque > _EPS:
        prix_defaut = _cout_moyen(disponibles, produit.get("prix_achat", 0)) if stock > _EPS else float(produit.get("prix_achat", 0) or 0)
        consommes.append({"lot_id": None, "quantite": manque, "prix_unitaire": prix_defaut})

    _enregistrer(produit_id, lots)
    cout_total = sum(c["quantite"] * c["prix_unitaire"] for c in consommes)
    return {
        "cout_unitaire": round(cout_total / quantite, 4) if quantite else 0.0,
        "cout_total": round(cout_total, 4),
        "lots_consommes": consommes,
        "methode": methode,
    }


def restituer(produit_id, lots_consommes, quantite=None, origine="annulation"):
    """Remet en stock des unités sorties, au coût auquel elles étaient sorties.

    :param quantite: None = tout restituer ; sinon restitution partielle
                     (les dernières consommations sont rendues en premier).
    :return: la liste ``lots_consommes`` restante (non restituée).
    """
    consommes = [dict(c) for c in (lots_consommes or [])]
    if not consommes:
        return []
    produit, lots = charger_lots(produit_id)
    if produit is None:
        return consommes
    par_id = {lot["id"]: lot for lot in lots}
    a_rendre = sum(c["quantite"] for c in consommes) if quantite is None else float(quantite)

    restants = []
    for conso in reversed(consommes):
        if a_rendre <= _EPS:
            restants.insert(0, conso)
            continue
        part = min(a_rendre, conso["quantite"])
        lot = par_id.get(conso.get("lot_id"))
        if lot is not None:
            lot["quantite_restante"] = round(lot["quantite_restante"] + part, 6)
        else:
            nouveau = nouveau_lot(part, conso.get("prix_unitaire", 0), origine)
            lots.append(nouveau)
            par_id[nouveau["id"]] = nouveau
        a_rendre = round(a_rendre - part, 6)
        if conso["quantite"] - part > _EPS:
            restants.insert(0, {**conso, "quantite": round(conso["quantite"] - part, 6)})
    _enregistrer(produit_id, lots)
    return restants


def cout_modifiable(lots):
    """Le coût se corrige à la main seulement tant que l'article n'a reçu
    qu'un seul lot d'achat (erreur de saisie à la création). Ensuite il est
    calculé à partir des achats, comme dans Odoo."""
    return len([lot for lot in lots if lot.get("quantite_initiale", 0) > _EPS]) <= 1


def corriger_cout(produit_id, nouveau_cout):
    """Corrige le coût du lot initial (et donc le coût d'achat de l'article).

    :raises ValueError: si l'article a déjà reçu plusieurs lots.
    """
    produit, lots = charger_lots(produit_id)
    if produit is None:
        return None
    if not cout_modifiable(lots):
        raise ValueError(
            "Le coût d'achat est calculé automatiquement à partir des approvisionnements "
            "(méthode de valorisation du magasin) : il ne se modifie pas à la main."
        )
    for lot in lots:
        lot["prix_unitaire"] = float(nouveau_cout or 0)
    _enregistrer(produit_id, lots)
    ProduitModel.get_collection().update_one(
        {"_id": ObjectId(str(produit_id))},
        {"$set": {"prix_achat": float(nouveau_cout or 0), "dernier_prix_achat": float(nouveau_cout or 0)}},
    )
    return float(nouveau_cout or 0)


def recalculer_cout(produit_id):
    """Recalcule le coût d'achat d'un article d'après ses lots (données
    enregistrées avant que le coût ne suive la méthode de valorisation)."""
    produit, lots = charger_lots(produit_id)
    if produit is None:
        return None
    _enregistrer(produit_id, lots)
    return ProduitModel.get_collection().find_one({"_id": ObjectId(str(produit_id))}).get("prix_achat")


def _date(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except ValueError:
        return datetime.min


# --------------------------------------------------------------------------
# Lignes de livraison
# --------------------------------------------------------------------------
def valoriser_lignes(lignes, magasin_id):
    """Calcule le coût de chaque ligne vendue et l'inscrit sur la ligne."""
    methode = get_methode(magasin_id)
    for ligne in lignes:
        resultat = sortie(ligne["produit_id"], ligne.get("quantite", 0), methode)
        ligne["prix_achat"] = resultat["cout_unitaire"]
        ligne["cout_achat_total"] = resultat["cout_total"]
        ligne["lots_consommes"] = resultat["lots_consommes"]
        ligne["methode_valorisation"] = methode
    return lignes


def annuler_lignes(lignes, origine="annulation livraison"):
    """Remet en stock les lignes d'une livraison annulée ou modifiée.

    Une ligne enregistrée avant la valorisation (sans ``lots_consommes``)
    revient comme un nouveau lot à son prix d'achat enregistré.
    """
    for ligne in lignes:
        quantite = float(ligne.get("quantite", 0) or 0)
        if quantite <= 0:
            continue
        consommes = ligne.get("lots_consommes")
        if consommes is None:
            consommes = [{"lot_id": None, "quantite": quantite, "prix_unitaire": float(ligne.get("prix_achat", 0) or 0)}]
        restituer(ligne["produit_id"], consommes, origine=origine)
        ligne["lots_consommes"] = []
    return lignes


def restituer_partiel_ligne(ligne, quantite, origine="retour client"):
    """Retour client : remet ``quantite`` unités en stock au coût de sortie."""
    quantite = float(quantite or 0)
    if quantite <= 0:
        return ligne
    consommes = ligne.get("lots_consommes")
    if consommes is None:
        consommes = [{"lot_id": None, "quantite": float(ligne.get("quantite", 0) or 0),
                      "prix_unitaire": float(ligne.get("prix_achat", 0) or 0)}]
    ligne["lots_consommes"] = restituer(ligne["produit_id"], consommes, quantite=quantite, origine=origine)
    return ligne


def etat_stock(produit_id, magasin_id):
    """Résumé de la valorisation d'un produit (pour l'interface)."""
    produit, lots = charger_lots(produit_id)
    if produit is None:
        return None
    methode = get_methode(magasin_id)
    restants = [lot for lot in lots if lot["quantite_restante"] > _EPS]
    quantite = sum(lot["quantite_restante"] for lot in restants)
    valeur = sum(lot["quantite_restante"] * lot["prix_unitaire"] for lot in restants)
    ordre = sorted(restants, key=lambda lot: _date(lot["date"]), reverse=(methode == "LIFO"))
    if methode == "CMUP":
        prochain = _cout_moyen(restants, produit.get("prix_achat", 0))
    else:
        prochain = ordre[0]["prix_unitaire"] if ordre else float(produit.get("prix_achat", 0) or 0)
    return {
        "methode": methode,
        "methode_nom": METHODES[methode]["nom"],
        "cout_achat": round(_cout_moyen(restants, produit.get("prix_achat", 0)), 4),
        "dernier_prix_achat": round(float(produit.get("dernier_prix_achat", produit.get("prix_achat", 0)) or 0), 4),
        "cout_modifiable": cout_modifiable(lots),
        "quantite_valorisee": round(quantite, 4),
        "valeur_stock": round(valeur, 2),
        "cout_moyen": round(_cout_moyen(restants, produit.get("prix_achat", 0)), 4),
        "cout_prochaine_sortie": round(prochain, 4),
        "lots": [
            {**lot, "date": lot["date"].isoformat() if isinstance(lot["date"], datetime) else lot["date"]}
            for lot in sorted(lots, key=lambda lot: _date(lot["date"]))
        ],
    }
