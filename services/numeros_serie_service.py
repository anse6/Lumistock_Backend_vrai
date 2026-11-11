# services/numeros_serie_service.py
"""Suivi des articles par numéro de série (machines, téléphones…).

Option du magasin, désactivée par défaut (Paramètres › Stock). Une fois
activée, l'administrateur coche « Suivi par numéro de série » sur les articles
concernés. Chaque unité physique est alors une ligne de ``numeros_serie`` :

- elle entre (création, approvisionnement, facture fournisseur) avec son numéro ;
- elle sort (vente, avarie) : on dit QUELLE unité sort, au scan ou dans une liste ;
- elle revient (annulation, retour client) ;
- chaque événement est ajouté à son ``historique`` (traçabilité et rapports).

Règle : le stock d'un article suivi est le nombre de ses numéros « en stock ».
Un numéro est unique dans le magasin (comparaison sans tenir compte de la
casse ni des espaces), pour qu'un scan désigne toujours une seule unité.
"""
import random
from datetime import datetime

from bson import ObjectId

from extensions import db
from models.magasin_model import MagasinModel

EN_STOCK, VENDU, AVARIE, RETIRE = "en_stock", "vendu", "avarie", "retire"
STATUTS = {EN_STOCK: "En stock", VENDU: "Vendu", AVARIE: "Avarié", RETIRE: "Suivi arrêté"}
LONGUEUR_MAX = 100

# Événements de l'historique, et ceux qui font sortir l'unité du magasin.
LIBELLES = {
    "entree": "Entrée en stock",
    "vente": "Vendu",
    "annulation": "Vente annulée : retour en stock",
    "restauration": "Vente restaurée : de nouveau vendu",
    "modification": "Retiré de la vente (modification) : retour en stock",
    "retour": "Retour client : retour en stock",
    "avarie": "Déclaré en avarie",
    "suivi_arrete": "Suivi par numéro de série arrêté sur l'article",
}
SORTIES = {"vente", "restauration", "avarie"}


class ErreurNumeroSerie(ValueError):
    """Numéros manquants, en double, inconnus ou déjà sortis."""

    def __init__(self, message, statut=400):
        super().__init__(message)
        self.statut = statut


def _table():
    return db.tables.numeros_serie


def _oid(valeur):
    return ObjectId(str(valeur)) if valeur is not None and ObjectId.is_valid(str(valeur)) else None


# --------------------------------------------------------------------------
# Réglages
# --------------------------------------------------------------------------
def actif(magasin_id):
    """L'option « Numéros de série » est-elle activée pour ce magasin ?"""
    oid = _oid(magasin_id)
    magasin = MagasinModel.get_collection().find_one({"_id": oid}) if oid else None
    return bool((magasin or {}).get("suivi_numeros_serie"))


def definir_actif(magasin_id, valeur):
    """Active ou désactive l'option. Elle ne se désactive pas tant que des
    articles suivis ont encore des numéros en stock (ils deviendraient
    incontrôlables)."""
    valeur = bool(valeur)
    if not valeur:
        en_stock = [n for n in _table().find({"magasin_id": _oid(magasin_id)}) if n.get("statut") == EN_STOCK]
        if en_stock:
            raise ErreurNumeroSerie(
                f"{len(en_stock)} unité(s) suivie(s) par numéro de série sont encore en stock : "
                "retirez d'abord le suivi des articles concernés.")
    MagasinModel.get_collection().update_one({"_id": _oid(magasin_id)}, {"$set": {"suivi_numeros_serie": valeur}})
    return valeur


def est_suivi(produit):
    return bool((produit or {}).get("suivi_serie"))


# --------------------------------------------------------------------------
# Numéros
# --------------------------------------------------------------------------
def normaliser(numero):
    return " ".join(str(numero or "").split())


def cle(numero):
    return normaliser(numero).upper()


def _du_magasin(magasin_id):
    return list(_table().find({"magasin_id": _oid(magasin_id)}))


def numeros_de(produit_id, statut=None):
    lignes = list(_table().find({"produit_id": _oid(produit_id)}))
    if statut:
        lignes = [n for n in lignes if n.get("statut") == statut]
    return sorted(lignes, key=lambda n: cle(n.get("numero_serie")))


def compter_en_stock(produit_id):
    return len(numeros_de(produit_id, EN_STOCK))


def _liste(numeros):
    if numeros is None:
        return []
    if isinstance(numeros, str):
        numeros = numeros.replace(",", "\n").replace(";", "\n").splitlines()
    if not isinstance(numeros, (list, tuple)):
        raise ErreurNumeroSerie("Les numéros de série doivent être une liste.")
    return [normaliser(n) for n in numeros if normaliser(n)]


def _doublons(numeros):
    vus, doubles = set(), []
    for n in numeros:
        if cle(n) in vus and n not in doubles:
            doubles.append(n)
        vus.add(cle(n))
    return doubles


def verifier_nouveaux(produit, numeros, quantite):
    """Contrôle les numéros d'une entrée en stock et les renvoie nettoyés."""
    numeros = _liste(numeros)
    quantite = int(quantite or 0)
    nom = produit.get("nom", "")
    if len(numeros) != quantite:
        raise ErreurNumeroSerie(
            f"« {nom} » est suivi par numéro de série : saisissez {quantite} numéro(s) "
            f"({len(numeros)} saisi(s)).")
    trop_longs = [n for n in numeros if len(n) > LONGUEUR_MAX]
    if trop_longs:
        raise ErreurNumeroSerie(f"Numéro de série trop long (100 caractères au plus) : {trop_longs[0][:30]}…")
    doubles = _doublons(numeros)
    if doubles:
        raise ErreurNumeroSerie(f"Numéro(s) de série saisi(s) deux fois : {', '.join(doubles[:5])}.")
    existants = {cle(n.get("numero_serie")) for n in _du_magasin(produit["magasin_id"])
                 if not (n.get("statut") == RETIRE and str(n.get("produit_id")) == str(produit.get("_id")))}
    deja = [n for n in numeros if cle(n) in existants]
    if deja:
        raise ErreurNumeroSerie(
            f"Numéro(s) de série déjà enregistré(s) dans le magasin : {', '.join(deja[:5])}.", 409)
    return numeros


def _evenement(type_, acteur=None, **details):
    acteur = acteur or {}
    return {
        "type": type_,
        "libelle": LIBELLES.get(type_, type_),
        "date": datetime.utcnow(),
        "par_id": str(acteur.get("admin_id") or acteur.get("user_id") or "") or None,
        "par_nom": acteur.get("admin_nom") or acteur.get("nom"),
        **{k: (str(v) if isinstance(v, ObjectId) else v) for k, v in details.items() if v not in (None, "")},
    }


def entrer(produit, numeros, origine, cout=0.0, acteur=None, fournisseur_id=None, facture_id=None):
    """Enregistre de nouvelles unités en stock (numéros déjà vérifiés)."""
    maintenant = datetime.utcnow()
    repris = {cle(n.get("numero_serie")): n for n in numeros_de(produit["_id"], RETIRE)}
    for numero in numeros:
        ancien = repris.get(cle(numero))
        if ancien:  # suivi réactivé : l'unité garde son historique
            _table().update_one({"_id": ancien["_id"]}, {
                "$set": {"statut": EN_STOCK, "cout_achat": float(cout or 0), "date_entree": maintenant,
                         "origine": origine, "updated_at": maintenant},
                "$push": {"historique": _evenement("entree", acteur, origine=origine, cout=float(cout or 0))},
            })
            continue
        _table().insert_one({
            "magasin_id": _oid(produit["magasin_id"]),
            "produit_id": _oid(produit["_id"]),
            "numero_serie": numero,
            "statut": EN_STOCK,
            "cout_achat": float(cout or 0),
            "date_entree": maintenant,
            "origine": origine,
            "fournisseur_id": _oid(fournisseur_id),
            "facture_id": _oid(facture_id),
            "historique": [_evenement("entree", acteur, origine=origine, cout=float(cout or 0),
                                      facture_id=facture_id)],
            "created_at": maintenant,
        })


def verifier_disponibles(produit, numeros, quantite):
    """Contrôle les numéros d'une sortie : autant que d'unités, tous en stock."""
    numeros = _liste(numeros)
    quantite = int(quantite or 0)
    nom = produit.get("nom", "")
    if len(numeros) != quantite:
        raise ErreurNumeroSerie(
            f"« {nom} » est suivi par numéro de série : choisissez {quantite} numéro(s) "
            f"({len(numeros)} choisi(s)).")
    doubles = _doublons(numeros)
    if doubles:
        raise ErreurNumeroSerie(f"Numéro(s) de série choisi(s) deux fois : {', '.join(doubles[:5])}.")
    par_cle = {cle(n.get("numero_serie")): n for n in numeros_de(produit["_id"])}
    inconnus = [n for n in numeros if cle(n) not in par_cle]
    if inconnus:
        raise ErreurNumeroSerie(f"Numéro(s) de série inconnu(s) pour « {nom} » : {', '.join(inconnus[:5])}.")
    sortis = [n for n in numeros if par_cle[cle(n)].get("statut") != EN_STOCK]
    if sortis:
        raise ErreurNumeroSerie(
            f"Numéro(s) de série qui ne sont plus en stock pour « {nom} » : {', '.join(sortis[:5])}.", 409)
    return [par_cle[cle(n)]["numero_serie"] for n in numeros]


def _changer(produit_id, numeros, de, vers, evenement, champs=None):
    """Passe chaque numéro de l'état ``de`` à l'état ``vers``. La condition
    sur l'état rend l'opération sûre si deux caisses vendent la même unité."""
    par_cle = {cle(n.get("numero_serie")): n for n in numeros_de(produit_id)}
    for numero in numeros:
        ligne = par_cle.get(cle(numero))
        if not ligne:
            raise ErreurNumeroSerie(f"Numéro de série inconnu : {numero}.")
        resultat = _table().update_one(
            {"_id": ligne["_id"], "statut": de},
            {"$set": {"statut": vers, **(champs or {}), "updated_at": datetime.utcnow()},
             "$push": {"historique": evenement}},
        )
        if not resultat.matched_count:
            raise ErreurNumeroSerie(
                f"Le numéro de série {numero} vient d'être utilisé ailleurs (par exemple à une autre caisse).", 409)


def sortir(produit_id, numeros, type_, acteur=None, vente_id=None, client_id=None, client_nom=None, prix=None,
           statut=VENDU, **details):
    evenement = _evenement(type_, acteur, vente_id=vente_id, client_id=client_id, client_nom=client_nom,
                           prix=prix, **details)
    _changer(produit_id, numeros, EN_STOCK, statut, evenement, {
        "vente_id": _oid(vente_id), "client_id": _oid(client_id),
        "prix_vente": float(prix) if prix is not None else None, "date_sortie": datetime.utcnow(),
    })


def rentrer(produit_id, numeros, type_, acteur=None, depuis=VENDU, **details):
    evenement = _evenement(type_, acteur, **details)
    _changer(produit_id, numeros, depuis, EN_STOCK, evenement,
             {"vente_id": None, "client_id": None, "prix_vente": None, "date_sortie": None})


def arreter_suivi(produit, acteur=None):
    """L'article n'est plus suivi : ses unités en stock sortent du suivi
    (elles gardent leur historique et pourront être reprises)."""
    en_stock = [n["numero_serie"] for n in numeros_de(produit["_id"], EN_STOCK)]
    if en_stock:
        _changer(produit["_id"], en_stock, EN_STOCK, RETIRE, _evenement("suivi_arrete", acteur))
    return len(en_stock)


def generer(magasin_id, nombre):
    """Numéros internes pour des unités sans numéro du fabricant : « LS » +
    10 chiffres, uniques dans le magasin."""
    nombre = max(0, min(int(nombre or 0), 500))
    existants = {cle(n.get("numero_serie")) for n in _du_magasin(magasin_id)}
    resultat = []
    while len(resultat) < nombre:
        numero = "LS" + "".join(random.choices("0123456789", k=10))
        if numero not in existants:
            existants.add(numero)
            resultat.append(numero)
    return resultat


def trouver_en_stock(magasin_id, code):
    """Unité en stock dont le numéro correspond à un code scanné."""
    c = cle(code)
    for n in _du_magasin(magasin_id):
        if cle(n.get("numero_serie")) == c and n.get("statut") == EN_STOCK:
            return n
    return None


# --------------------------------------------------------------------------
# Lignes de vente
# --------------------------------------------------------------------------
def lignes_suivies(lignes, produits_par_id):
    """Lignes de vente dont l'article est suivi par numéro de série."""
    return [l for l in lignes if est_suivi(produits_par_id.get(str(l["produit_id"])))]


def preparer_lignes_vente(lignes, produits_par_id):
    """Vérifie les numéros de chaque ligne suivie (avant toute écriture) et
    les inscrit sur la ligne. Un article suivi se vend à l'unité."""
    total = {}
    for ligne in lignes:
        produit = produits_par_id.get(str(ligne["produit_id"]))
        if not est_suivi(produit):
            ligne.pop("numeros_serie", None)
            continue
        if ligne.get("is_conditionne"):
            raise ErreurNumeroSerie(f"« {produit['nom']} » est suivi par numéro de série : il se vend à l'unité.")
        ligne["numeros_serie"] = verifier_disponibles(produit, ligne.get("numeros_serie"), ligne.get("quantite"))
        total.setdefault(str(ligne["produit_id"]), []).extend(ligne["numeros_serie"])
    for produit_id, numeros in total.items():
        doubles = _doublons(numeros)
        if doubles:
            raise ErreurNumeroSerie(f"Numéro(s) de série présent(s) sur deux lignes : {', '.join(doubles[:5])}.")
    return lignes


def vendre_lignes(lignes, vente_id, acteur, client_id=None, client_nom=None, type_="vente"):
    for ligne in lignes:
        if ligne.get("numeros_serie"):
            sortir(ligne["produit_id"], ligne["numeros_serie"], type_, acteur, vente_id=vente_id,
                   client_id=client_id, client_nom=client_nom, prix=ligne.get("prix"))


def modifier_lignes(anciennes, nouvelles, produits_par_id, vente_id, acteur, client_id=None, client_nom=None):
    """Modification d'une vente : les unités retirées reviennent en stock,
    les unités ajoutées sortent ; celles qui restent ne bougent pas."""
    avant = {}
    for ligne in anciennes:
        for n in ligne.get("numeros_serie") or []:
            avant.setdefault(str(ligne["produit_id"]), {})[cle(n)] = n
    apres = {}
    for ligne in nouvelles:
        produit = produits_par_id.get(str(ligne["produit_id"]))
        if not est_suivi(produit):
            ligne.pop("numeros_serie", None)
            continue
        if ligne.get("is_conditionne"):
            raise ErreurNumeroSerie(f"« {produit['nom']} » est suivi par numéro de série : il se vend à l'unité.")
        numeros = _liste(ligne.get("numeros_serie"))
        if len(numeros) != int(ligne.get("quantite") or 0):
            raise ErreurNumeroSerie(
                f"« {produit['nom']} » est suivi par numéro de série : choisissez {ligne.get('quantite')} "
                f"numéro(s) ({len(numeros)} choisi(s)).")
        pid = str(ligne["produit_id"])
        gardes = avant.get(pid, {})
        nouveaux = [n for n in numeros if cle(n) not in gardes]
        if nouveaux:
            nouveaux = verifier_disponibles(produit, nouveaux, len(nouveaux))
        par_cle = {**{cle(n): n for n in nouveaux}, **gardes}
        ligne["numeros_serie"] = [par_cle[cle(n)] for n in numeros]
        apres.setdefault(pid, []).extend(ligne["numeros_serie"])
        ligne["_numeros_nouveaux"] = nouveaux
    for pid, numeros in apres.items():
        doubles = _doublons(numeros)
        if doubles:
            raise ErreurNumeroSerie(f"Numéro(s) de série présent(s) sur deux lignes : {', '.join(doubles[:5])}.")
    for pid, gardes in avant.items():
        restants = {cle(n) for n in apres.get(pid, [])}
        retires = [n for c, n in gardes.items() if c not in restants]
        if retires:
            rentrer(pid, retires, "modification", acteur, vente_id=vente_id)
    for ligne in nouvelles:
        nouveaux = ligne.pop("_numeros_nouveaux", None)
        if nouveaux:
            sortir(ligne["produit_id"], nouveaux, "vente", acteur, vente_id=vente_id, client_id=client_id,
                   client_nom=client_nom, prix=ligne.get("prix"))


def rendre_lignes(lignes, type_, acteur=None, vente_id=None):
    for ligne in lignes:
        if ligne.get("numeros_serie"):
            rentrer(ligne["produit_id"], ligne["numeros_serie"], type_, acteur, vente_id=vente_id)


# --------------------------------------------------------------------------
# Lecture (fiche article, rapports)
# --------------------------------------------------------------------------
def _date_iso(valeur):
    return valeur.isoformat() if isinstance(valeur, datetime) else valeur


def serialiser(n, noms_produits=None):
    historique = [{**e, "date": _date_iso(e.get("date"))} for e in (n.get("historique") or [])]
    return {
        "_id": str(n["_id"]),
        "numero_serie": n.get("numero_serie"),
        "produit_id": str(n.get("produit_id")),
        "produit": (noms_produits or {}).get(str(n.get("produit_id"))),
        "statut": n.get("statut"),
        "statut_libelle": STATUTS.get(n.get("statut"), n.get("statut")),
        "cout_achat": float(n.get("cout_achat") or 0),
        "prix_vente": n.get("prix_vente"),
        "date_entree": _date_iso(n.get("date_entree")),
        "date_sortie": _date_iso(n.get("date_sortie")),
        "origine": n.get("origine"),
        "vente_id": str(n["vente_id"]) if n.get("vente_id") else None,
        "client_id": str(n["client_id"]) if n.get("client_id") else None,
        "historique": historique,
    }


def _noms_produits(magasin_id):
    return {str(p["_id"]): p.get("nom") for p in db.tables.produits.find({"magasin_id": _oid(magasin_id)})}


def lister(magasin_id, produit_id=None, statut=None, recherche=None):
    lignes = numeros_de(produit_id) if produit_id else _du_magasin(magasin_id)
    lignes = [n for n in lignes if str(n.get("magasin_id")) == str(magasin_id)]
    if statut:
        lignes = [n for n in lignes if n.get("statut") == statut]
    if recherche:
        r = cle(recherche)
        lignes = [n for n in lignes if r in cle(n.get("numero_serie"))]
    noms = _noms_produits(magasin_id)
    return [serialiser(n, noms) for n in sorted(lignes, key=lambda n: cle(n.get("numero_serie")))]


def sorties(magasin_id, debut, fin, produit_id=None):
    """Toutes les sorties (ventes, avaries) de la période, même si l'unité est
    revenue ensuite : une ligne par événement, la plus récente d'abord."""
    noms = _noms_produits(magasin_id)
    resultat = []
    for n in _du_magasin(magasin_id):
        if produit_id and str(n.get("produit_id")) != str(produit_id):
            continue
        historique = n.get("historique") or []
        for i, e in enumerate(historique):
            if e.get("type") not in SORTIES:
                continue
            quand = e.get("date")
            if isinstance(quand, str):
                try:
                    quand = datetime.fromisoformat(quand)
                except ValueError:
                    continue
            if not quand or not (debut <= quand.replace(tzinfo=None) < fin):
                continue
            revenu = next((s for s in historique[i + 1:] if s.get("type") in ("annulation", "retour", "modification")), None)
            resultat.append({
                "numero_serie": n.get("numero_serie"),
                "produit_id": str(n.get("produit_id")),
                "produit": noms.get(str(n.get("produit_id"))),
                "type": e.get("type"),
                "libelle": e.get("libelle"),
                "date": _date_iso(quand),
                "vente_id": e.get("vente_id"),
                "client_id": e.get("client_id"),
                "client_nom": e.get("client_nom"),
                "par_nom": e.get("par_nom"),
                "prix": e.get("prix"),
                "revenu": bool(revenu),
                "revenu_libelle": revenu.get("libelle") if revenu else None,
                "statut_actuel": STATUTS.get(n.get("statut"), n.get("statut")),
            })
    return sorted(resultat, key=lambda x: x["date"] or "", reverse=True)
