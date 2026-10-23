"""Rôles métier partagés par les services et les routes.

Le rôle historique ``livreur`` reste reconnu pour les comptes existants. Les
nouveaux comptes opérationnels sont enregistrés comme ``gerant``.

Le ``caissier`` vend au comptoir depuis le stock du MAGASIN (comme un admin
ou un manager) mais ne voit que ses propres ventes, sa caisse et ses
statistiques.
"""

LOGISTICS_ROLES = ("livreur", "gerant")
CAISSIER = "caissier"

# Rôles qui vendent depuis le stock du magasin.
ROLES_VENTE_MAGASIN = ("admin", "manager", CAISSIER)

LIBELLES_ROLES = {
    "admin": "Administrateur",
    "manager": "Manager",
    "gerant": "Gérant",
    "livreur": "Gérant",
    CAISSIER: "Caissier",
    "staff": "Staff",
}


def is_logistics_role(role):
    return role in LOGISTICS_ROLES


def is_caissier(role):
    return role == CAISSIER
