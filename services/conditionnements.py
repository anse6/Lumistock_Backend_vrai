# services/conditionnements.py
"""Règles des conditionnements d'un article (carton, pack, sac…).

Un conditionnement = un nom + un facteur (nombre d'unités de base) + un prix
de vente du conditionnement entier. Le stock reste toujours compté en unités.

Un conditionnement peut en contenir un autre (palette = 84 packs) :
`contenu_nom` + `contenu_nombre`. Son facteur est alors calculé
(84 × 6 = 504 unités) et suit le conditionnement contenu.
"""
from bson import ObjectId

LONGUEUR_NOM = 50


class ErreurConditionnement(ValueError):
    pass


def _nombre(valeur):
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return None


def _nom(valeur):
    return " ".join(str(valeur or "").split())


def _fmt(n):
    return f"{n:,.2f}".rstrip("0").rstrip(".").replace(",", " ")


def normaliser(conditionnements, prix_unitaire, cout_unitaire=0.0, anciens=None):
    """Vérifie et nettoie la liste des conditionnements d'un article.

    - nom obligatoire et unique sur l'article ;
    - facteur entier d'au moins 2 (1 unité = l'article lui-même), unique ;
    - prix > 0 (absent : facteur × prix unitaire) et jamais sous le coût
      d'achat des unités qu'il contient (même règle que la vente à l'unité) ;
    - un conditionnement existant garde son identifiant et son code-barre.

    :raises ErreurConditionnement: message prêt à afficher.
    """
    if conditionnements in (None, ""):
        return []
    if not isinstance(conditionnements, list):
        raise ErreurConditionnement("Liste de conditionnements invalide.")
    anciens = {str(c.get("_id")): c for c in (anciens or []) if c.get("_id") is not None}
    prix_unitaire = float(prix_unitaire or 0)
    cout_unitaire = float(cout_unitaire or 0)

    # 1. Noms (uniques) et saisie brute.
    saisis = {}
    for c in conditionnements:
        if not isinstance(c, dict):
            raise ErreurConditionnement("Conditionnement invalide.")
        nom = _nom(c.get("nom"))
        if not nom:
            raise ErreurConditionnement("Chaque conditionnement doit avoir un nom (ex. : Carton).")
        if len(nom) > LONGUEUR_NOM:
            raise ErreurConditionnement(f"Nom de conditionnement trop long (50 caractères au plus) : {nom[:20]}…")
        if nom.lower() in saisis:
            raise ErreurConditionnement(f"Deux conditionnements s'appellent « {nom} ».")
        saisis[nom.lower()] = (nom, c)

    # 2. Facteur et prix, en suivant ce que chaque conditionnement contient.
    calcules, en_cours = {}, set()

    def resoudre(cle_nom):
        if cle_nom in calcules:
            return calcules[cle_nom]
        nom, c = saisis[cle_nom]
        if cle_nom in en_cours:
            raise ErreurConditionnement(f"« {nom} » se contient lui-même (directement ou par un autre conditionnement).")
        en_cours.add(cle_nom)
        contenu_nom = _nom(c.get("contenu_nom"))
        contenu = None
        if contenu_nom:
            if contenu_nom.lower() not in saisis:
                raise ErreurConditionnement(f"« {nom} » contient « {contenu_nom} », qui n'existe pas sur cet article.")
            nombre = _nombre(c.get("contenu_nombre"))
            if nombre is None or not nombre.is_integer() or nombre < 2:
                raise ErreurConditionnement(
                    f"« {nom} » : indiquez combien de « {contenu_nom} » il contient (un nombre entier d'au moins 2).")
            contenu = resoudre(contenu_nom.lower())
            facteur = int(nombre) * contenu["facteur"]
        else:
            facteur = _nombre(c.get("facteur"))
            if facteur is None or not facteur.is_integer() or facteur < 2:
                raise ErreurConditionnement(
                    f"« {nom} » : la quantité d'unités doit être un nombre entier d'au moins 2 "
                    "(1 unité, c'est l'article lui-même).")
            facteur = int(facteur)
        prix = _nombre(c.get("prix_conditionnement"))
        if c.get("prix_conditionnement") in (None, ""):
            # Vide : le prix des conditionnements contenus (ou des unités).
            prix = round(int(nombre) * contenu["prix_conditionnement"], 2) if contenu \
                else round(facteur * prix_unitaire, 2)
        if prix is None or prix <= 0:
            raise ErreurConditionnement(f"« {nom} » : le prix du conditionnement doit être supérieur à 0.")
        plancher = round(facteur * cout_unitaire, 2)
        if cout_unitaire > 0 and round(prix, 2) < plancher:
            raise ErreurConditionnement(
                f"« {nom} » : {_fmt(prix)} pour {facteur} unités, soit {_fmt(prix / facteur)} l'unité, "
                f"est sous le coût d'achat ({_fmt(cout_unitaire)} l'unité). Prix minimum : {_fmt(plancher)}.")
        ancien = anciens.get(str(c.get("_id"))) if c.get("_id") is not None else None
        reference = round(facteur * prix_unitaire, 2)
        cond = {
            "_id": ancien["_id"] if ancien else ObjectId(),
            "nom": nom,
            "facteur": facteur,
            "prix_conditionnement": round(prix, 2),
            "prix_reference": reference,
            "economie": round(prix - reference, 2),
        }
        if contenu:
            cond["contenu_nom"] = contenu["nom"]
            cond["contenu_nombre"] = int(nombre)
        if ancien and ancien.get("code_barre"):
            cond["code_barre"] = ancien["code_barre"]
        en_cours.discard(cle_nom)
        calcules[cle_nom] = cond
        return cond

    resultat, facteurs = [], {}
    for cle_nom in saisis:
        cond = resoudre(cle_nom)
        if cond["facteur"] in facteurs:
            raise ErreurConditionnement(
                f"« {facteurs[cond['facteur']]} » et « {cond['nom']} » contiennent tous les deux "
                f"{cond['facteur']} unités : gardez-en un seul.")
        facteurs[cond["facteur"]] = cond["nom"]
        resultat.append(cond)
    return sorted(resultat, key=lambda c: c["facteur"])


def recalculer_references(conditionnements, prix_unitaire):
    """Après un changement du prix unitaire : prix de référence et économie à jour."""
    for c in conditionnements or []:
        c["prix_reference"] = round(int(c.get("facteur") or 1) * float(prix_unitaire or 0), 2)
        c["economie"] = round(float(c.get("prix_conditionnement") or 0) - c["prix_reference"], 2)
    return conditionnements
