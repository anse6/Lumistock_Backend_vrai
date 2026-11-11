"""Droits d'accès configurables par magasin (page Paramètres > Accès).

Chaque droit du CATALOGUE a :
- les rôles qui l'ont par défaut (le fonctionnement d'avant cette page) ;
- les rôles pour lesquels l'admin peut l'activer ou le retirer — seulement
  ceux qui ont un écran pour s'en servir.

L'admin a toujours tous les droits (il ne peut pas se bloquer lui-même), et
le staff Lumistock aussi. Les choix de l'admin sont enregistrés dans le
magasin (`permissions_roles` : {droit: [rôles]}) ; un droit absent garde sa
valeur par défaut, ce qui permet d'en ajouter de nouveaux sans migration.
"""

from flask import g
from flask_jwt_extended import get_jwt_identity
from bson import ObjectId

ROLES_CONFIGURABLES = ("manager", "gerant", "caissier")
LIBELLES_ROLES = {"manager": "Manager", "gerant": "Gérant", "caissier": "Caissier"}

GROUPES = {
    "produits": "Articles et stock",
    "clients": "Clients",
    "ventes": "Ventes et caisse",
    "finances": "Finances",
    "administration": "Équipe et magasin",
}

# cle: (groupe, libellé, description, rôles par défaut, rôles configurables)
_CATALOGUE = [
    ("produits.creer", "produits", "Créer des articles",
     "Ajouter un nouvel article au catalogue.", ("admin",), ("manager",)),
    ("produits.importer", "produits", "Importer des articles",
     "Créer ou mettre à jour des articles depuis un fichier Excel.", ("admin", "manager"), ("manager",)),
    ("produits.modifier", "produits", "Modifier les articles",
     "Changer le nom, le prix, la catégorie… d'un article.", ("admin", "manager"), ("manager",)),
    ("produits.supprimer", "produits", "Supprimer et restaurer des articles",
     "Mettre un article à la corbeille, le restaurer ou le supprimer définitivement.", ("admin",), ("manager",)),
    ("stock.approvisionner", "produits", "Approvisionner le stock",
     "Enregistrer, modifier ou supprimer des approvisionnements.", ("admin", "manager"), ("manager",)),
    ("stock.avaries", "produits", "Déclarer des avaries et périmés",
     "Sortir du stock des articles abîmés ou périmés.", ("admin", "manager"), ("manager",)),
    ("stock.soldes", "produits", "Solder les articles avariés",
     "Remettre en vente à prix réduit des unités déclarées en avarie.", ("admin", "manager"), ("manager",)),

    ("clients.supprimer", "clients", "Supprimer et restaurer des clients",
     "Mettre un client à la corbeille ou le restaurer.", ("admin",), ("manager",)),
    ("clients.retirer_avance", "clients", "Rendre l'avance d'un client",
     "Retirer de l'argent de l'avance (compte) d'un client.", ("admin",), ("manager",)),
    ("clients.dette_ouverture", "clients", "Modifier une dette d'avant Lumistock",
     "Corriger ou supprimer la dette d'ouverture d'un client. Sans ce droit, on peut seulement la saisir "
     "une première fois.", ("admin", "manager"), ("manager", "gerant")),

    ("ventes.modifier", "ventes", "Modifier une vente",
     "Changer les articles, quantités ou paiements d'une vente déjà enregistrée.",
     ("admin", "manager", "gerant"), ("manager", "gerant")),
    ("ventes.annuler", "ventes", "Annuler une vente",
     "Annuler une vente et remettre ses articles en stock. À la caisse, seulement dans les minutes qui "
     "suivent la vente.", ("admin", "manager", "gerant", "caissier"), ("manager", "gerant", "caissier")),
    ("ventes.abandon_reste", "ventes", "Abandonner le reste impayé",
     "Solder une vente sans le reste dû, sans validation. Sans ce droit, le gérant envoie une demande "
     "qu'un responsable valide.", ("admin", "manager"), ("manager", "gerant")),
    ("ventes.remises_caisse", "ventes", "Accorder des remises à la caisse",
     "Le caissier peut appliquer une remise ou un rabais sur une vente.", ("admin",), ("caissier",)),

    ("finances.comptabilite", "finances", "Voir le module Comptabilité",
     "Bilan, journal, rapprochement bancaire et factures fournisseurs (menu « Comptabilité »).",
     ("admin",), ("manager",)),
    ("finances.parametres", "finances", "Paramètres comptables",
     "Fiscalité, impôts, financements, politique de retour, verrouillage et clôture des exercices, "
     "annulation d'un paiement fournisseur.", ("admin",), ("manager",)),

    ("equipe.gerer", "administration", "Gérer l'équipe",
     "Créer, modifier, désactiver des comptes et réinitialiser leur mot de passe (jamais celui de "
     "l'administrateur).", ("admin", "manager"), ("manager",)),
    ("magasin.modifier", "administration", "Modifier les informations du magasin",
     "Nom, logo, adresse, téléphone, informations légales.", ("admin",), ("manager",)),
    ("stock.methode", "administration", "Changer la méthode de valorisation du stock",
     "CMUP, FIFO ou LIFO : change le calcul des marges.", ("admin",), ("manager",)),
    ("messagerie.parametres", "administration", "Paramètres de la messagerie",
     "Fournisseur SMS/email, relances automatiques et envois de test.", ("admin",), ("manager",)),
]

CATALOGUE = {
    cle: {"cle": cle, "groupe": groupe, "libelle": libelle, "description": description,
          "defaut": defaut, "configurables": configurables}
    for cle, groupe, libelle, description, defaut, configurables in _CATALOGUE
}


class ErreurPermissions(ValueError):
    pass


def _role(role):
    """`livreur` est l'ancien nom du gérant."""
    return "gerant" if role == "livreur" else role


def _reglages(magasin):
    reglages = (magasin or {}).get("permissions_roles")
    return reglages if isinstance(reglages, dict) else {}


def roles_du_droit(magasin, cle):
    """Rôles qui ont le droit `cle` dans ce magasin (admin toujours inclus)."""
    droit = CATALOGUE[cle]
    choisis = _reglages(magasin).get(cle)
    if isinstance(choisis, list):
        # Seuls les rôles configurables suivent le choix de l'admin ; les autres
        # gardent la valeur par défaut.
        fixes = [r for r in droit["defaut"] if r not in droit["configurables"]]
        roles = fixes + [r for r in droit["configurables"] if r in choisis]
    else:
        roles = list(droit["defaut"])
        # Avant cette page, les remises du caissier se réglaient dans les
        # paramètres de caisse : ce réglage reste valable tant qu'on n'a rien changé.
        if cle == "ventes.remises_caisse" and ((magasin or {}).get("parametres_caisse") or {}).get("remises_autorisees"):
            roles.append("caissier")
    return set(roles) | {"admin"}


def a_le_droit(user, cle, magasin=None):
    role = _role((user or {}).get("role"))
    if role in ("admin", "staff"):
        return True
    if magasin is None:
        magasin = _magasin_de(user)
    return role in roles_du_droit(magasin, cle)


def droits_de(user, magasin=None):
    """Liste des droits du compte, pour adapter l'interface."""
    if magasin is None:
        magasin = _magasin_de(user)
    return sorted(cle for cle in CATALOGUE if a_le_droit(user, cle, magasin))


def _magasin_de(user):
    from models.magasin_model import MagasinModel

    magasin_id = (user or {}).get("magasin_id")
    if not magasin_id:
        return None
    return MagasinModel.get_collection().find_one({"_id": ObjectId(str(magasin_id))})


def utilisateur_courant():
    """Compte connecté, lu une fois par requête."""
    from models.user_model import UserModel

    identite = str(get_jwt_identity())
    cache = g.setdefault("utilisateurs_courants", {})
    if identite not in cache:
        cache[identite] = UserModel.get_user_by_id(identite)
    return cache[identite]


def droit_courant(cle):
    return a_le_droit(utilisateur_courant(), cle)


def configuration(magasin):
    """Catalogue avec, pour chaque droit, l'état de chaque rôle (page Accès)."""
    droits = []
    for droit in CATALOGUE.values():
        actifs = roles_du_droit(magasin, droit["cle"])
        droits.append({
            "cle": droit["cle"],
            "groupe": droit["groupe"],
            "libelle": droit["libelle"],
            "description": droit["description"],
            "roles": {
                role: {
                    "actif": role in actifs,
                    "modifiable": role in droit["configurables"],
                    "defaut": role in droit["defaut"],
                }
                for role in ROLES_CONFIGURABLES
            },
        })
    return {
        "groupes": [{"cle": cle, "libelle": libelle} for cle, libelle in GROUPES.items()],
        "roles": [{"cle": cle, "libelle": libelle} for cle, libelle in LIBELLES_ROLES.items()],
        "droits": droits,
        "personnalise": bool(_reglages(magasin)),
    }


def enregistrer(magasin, changements):
    """Applique {droit: {rôle: bool}} ; ignore ce qui n'est pas configurable.

    :raises ErreurPermissions: droit ou rôle inconnu, valeur non booléenne.
    """
    from models.magasin_model import MagasinModel

    if not isinstance(changements, dict) or not changements:
        raise ErreurPermissions("Aucun changement à enregistrer.")
    reglages = {cle: list(roles) for cle, roles in _reglages(magasin).items() if cle in CATALOGUE}
    for cle, roles in changements.items():
        if cle not in CATALOGUE:
            raise ErreurPermissions(f"Droit inconnu : {cle}.")
        if not isinstance(roles, dict):
            raise ErreurPermissions(f"Format invalide pour {cle}.")
        droit = CATALOGUE[cle]
        actifs = {r for r in roles_du_droit(magasin, cle) if r in droit["configurables"]}
        for role, valeur in roles.items():
            role = _role(role)
            if role not in ROLES_CONFIGURABLES:
                raise ErreurPermissions(f"Rôle inconnu : {role}.")
            if not isinstance(valeur, bool):
                raise ErreurPermissions("Chaque valeur doit être vrai ou faux.")
            if role not in droit["configurables"]:
                raise ErreurPermissions(f"« {droit['libelle']} » ne se règle pas pour le rôle {LIBELLES_ROLES[role]}.")
            (actifs.add if valeur else actifs.discard)(role)
        reglages[cle] = sorted(actifs)
    modifs = {"permissions_roles": reglages}
    if "ventes.remises_caisse" in changements:
        modifs["parametres_caisse.remises_autorisees"] = "caissier" in reglages["ventes.remises_caisse"]
    MagasinModel.get_collection().update_one({"_id": ObjectId(str(magasin["_id"]))}, {"$set": modifs})
    magasin = {**magasin, "permissions_roles": reglages}
    return configuration(magasin)


def reinitialiser(magasin):
    from models.magasin_model import MagasinModel

    MagasinModel.get_collection().update_one({"_id": ObjectId(str(magasin["_id"]))},
                                             {"$set": {"permissions_roles": {}}})
    return configuration({**magasin, "permissions_roles": {}})
