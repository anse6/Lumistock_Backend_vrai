# services/rapprochement_service.py
"""Rapprochement bancaire : comparer le relevé de la banque (ou du compte
mobile money) aux opérations enregistrées dans Lumistock.

Chaque ligne du relevé est rapprochée d'une ou plusieurs opérations de
l'application sur le même compte (paiements clients, charges, règlements
fournisseurs, remboursements…). Les écarts restants montrent ce qui manque
d'un côté ou de l'autre : frais bancaires non saisis, paiement oublié…
"""
import csv
import io
from datetime import datetime, timedelta

from bson import ObjectId

from extensions import db
from services.rapport_service import _arrondi, _date, _norm, _num

COMPTES = {"521": "Banque", "552": "Mobile money"}
TOLERANCE_JOURS = 5


def collection():
    return db.tables.releves_bancaires


def _verifier_compte(compte):
    if compte not in COMPTES:
        raise ValueError("Compte inconnu : 521 (banque) ou 552 (mobile money).")


# --------------------------------------------------------------------------- import du relevé
def _montant(texte):
    t = str(texte or "").strip().replace(" ", "").replace("\xa0", "").replace(" ", "")
    if not t:
        return None
    negatif = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace("FCFA", "").replace("XAF", "").replace("CFA", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        valeur = float(t)
    except ValueError:
        return None
    return -valeur if negatif else valeur


def _date_releve(texte):
    t = str(texte or "").strip()[:10]
    for f in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(t, f)
        except ValueError:
            continue
    return None


def _colonne(entetes, *noms):
    for i, e in enumerate(entetes):
        if any(n in e for n in noms):
            return i
    return None


def lire_csv(contenu):
    """Lignes (date, libellé, montant signé, référence) d'un relevé CSV.

    Colonnes reconnues : date ; libellé / description ; montant, ou débit et
    crédit ; référence (facultative). Séparateur « ; » ou « , ».
    """
    texte = contenu.decode("utf-8-sig", errors="replace") if isinstance(contenu, bytes) else contenu
    premiere = texte.splitlines()[0] if texte.strip() else ""
    separateur = ";" if premiere.count(";") >= premiere.count(",") else ","
    lignes = list(csv.reader(io.StringIO(texte), delimiter=separateur))
    if len(lignes) < 2:
        raise ValueError("Le fichier ne contient aucune opération.")
    entetes = [_norm(e) for e in lignes[0]]
    i_date = _colonne(entetes, "date")
    i_libelle = _colonne(entetes, "libelle", "description", "operation", "motif", "details")
    i_montant = _colonne(entetes, "montant", "amount")
    i_debit = _colonne(entetes, "debit", "retrait", "sortie")
    i_credit = _colonne(entetes, "credit", "depot", "entree", "versement")
    i_ref = _colonne(entetes, "reference", "ref", "transaction", "id")
    if i_date is None or (i_montant is None and i_debit is None and i_credit is None):
        raise ValueError("Colonnes introuvables : le fichier doit avoir une colonne « Date » et une colonne "
                         "« Montant » (ou « Débit » et « Crédit »).")
    resultat, erreurs = [], []
    for n, ligne in enumerate(lignes[1:], start=2):
        if not any(c.strip() for c in ligne):
            continue
        cellule = lambda i: ligne[i] if i is not None and i < len(ligne) else ""
        quand = _date_releve(cellule(i_date))
        if i_montant is not None and _montant(cellule(i_montant)) is not None:
            montant = _montant(cellule(i_montant))
        else:
            montant = (_montant(cellule(i_credit)) or 0) - abs(_montant(cellule(i_debit)) or 0)
        if not quand or not montant:
            erreurs.append(f"Ligne {n} ignorée (date ou montant illisible)")
            continue
        resultat.append({"date": quand, "libelle": cellule(i_libelle).strip() or "Opération",
                         "montant": round(montant, 2), "reference": cellule(i_ref).strip()})
    return resultat, erreurs


def importer(magasin_id, compte, operations):
    """Ajoute les lignes du relevé ; une ligne déjà importée n'est pas dupliquée."""
    _verifier_compte(compte)
    existantes = {(_date(r["date_operation"]).date(), _arrondi(r["montant"]), _norm(r.get("libelle")), r.get("reference") or "")
                  for r in collection().find({"magasin_id": ObjectId(str(magasin_id)), "compte": compte})}
    import_id = ObjectId().__str__()
    ajoutees = 0
    for o in operations:
        cle = (o["date"].date(), _arrondi(o["montant"]), _norm(o["libelle"]), o.get("reference") or "")
        if cle in existantes:
            continue
        existantes.add(cle)
        collection().insert_one({
            "magasin_id": ObjectId(str(magasin_id)), "compte": compte, "date_operation": o["date"],
            "libelle": o["libelle"][:250], "montant": float(o["montant"]), "reference": o.get("reference") or "",
            "statut": "non_rapproche", "rapproche_avec": [], "import_id": import_id, "created_at": datetime.utcnow(),
        })
        ajoutees += 1
    return {"ajoutees": ajoutees, "doublons": len(operations) - ajoutees}


# --------------------------------------------------------------------------- opérations de l'application
def operations_application(magasin_id, compte, debut, fin):
    """Mouvements du compte enregistrés dans l'application (+ entrée, − sortie)."""
    from services.compta_service import journal

    ops = []
    for e in journal(magasin_id, debut, fin)["ecritures"]:
        for l in e["lignes"]:
            if l["compte"] == compte and e.get("ref"):
                ops.append({"ref": e["ref"], "date": e["date"], "libelle": e["libelle"], "piece": e["piece"],
                            "montant": _arrondi(l["debit"] - l["credit"])})
    return ops


def _releves(magasin_id, compte, debut, fin):
    return sorted(
        (r for r in collection().find({"magasin_id": ObjectId(str(magasin_id)), "compte": compte})
         if debut <= _date(r["date_operation"]) <= fin),
        key=lambda r: _date(r["date_operation"]),
    )


def _deja_rapprochees(magasin_id, compte):
    return {ref for r in collection().find({"magasin_id": ObjectId(str(magasin_id)), "compte": compte})
            for ref in r.get("rapproche_avec") or []}


def etat(magasin_id, compte, debut, fin):
    _verifier_compte(compte)
    # Les opérations sont lues avec une marge : un virement saisi le 30 peut
    # apparaître sur le relevé le 2 du mois suivant.
    marge = timedelta(days=TOLERANCE_JOURS)
    ops = operations_application(magasin_id, compte, debut - marge, fin + marge)
    rapprochees = _deja_rapprochees(magasin_id, compte)
    releves = _releves(magasin_id, compte, debut, fin)
    par_ref = {o["ref"]: o for o in ops}
    lignes = []
    for r in releves:
        lignes.append({
            "id": str(r["_id"]), "date": _date(r["date_operation"]).isoformat(), "libelle": r.get("libelle") or "",
            "reference": r.get("reference") or "", "montant": _arrondi(r.get("montant")),
            "statut": r.get("statut") or "non_rapproche",
            "operations": [par_ref.get(ref, {"ref": ref, "libelle": "Opération hors période", "montant": None})
                           for ref in r.get("rapproche_avec") or []],
        })
    en_attente = [o for o in ops if o["ref"] not in rapprochees and debut <= _date(o["date"]) <= fin]
    total_releve = sum(l["montant"] for l in lignes)
    total_rapproche = sum(l["montant"] for l in lignes if l["statut"] == "rapproche")
    return {
        "compte": compte, "libelle": COMPTES[compte],
        "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()},
        "lignes": lignes,
        "en_attente": en_attente,
        "totaux": {
            "releve": _arrondi(total_releve),
            "application": _arrondi(sum(o["montant"] for o in ops if debut <= _date(o["date"]) <= fin)),
            "rapproche": _arrondi(total_rapproche),
            "lignes_non_rapprochees": sum(1 for l in lignes if l["statut"] != "rapproche"),
            "ecart_releve": _arrondi(total_releve - total_rapproche),
            "ecart_application": _arrondi(sum(o["montant"] for o in en_attente)),
        },
    }


# --------------------------------------------------------------------------- rapprochement
def _releve(magasin_id, ligne_id):
    if not ObjectId.is_valid(str(ligne_id)):
        return None
    return collection().find_one({"_id": ObjectId(str(ligne_id)), "magasin_id": ObjectId(str(magasin_id))})


def rapprocher(magasin_id, ligne_id, refs):
    """Rapproche une ligne du relevé d'opérations dont la somme est égale à son montant."""
    ligne = _releve(magasin_id, ligne_id)
    if not ligne:
        return None
    refs = [str(r) for r in refs or []]
    if not refs:
        raise ValueError("Choisissez au moins une opération.")
    d = _date(ligne["date_operation"])
    ops = {o["ref"]: o for o in operations_application(magasin_id, ligne["compte"], d - timedelta(days=60), d + timedelta(days=60))}
    inconnues = [r for r in refs if r not in ops]
    if inconnues:
        raise ValueError("Opération introuvable sur ce compte (à plus de 60 jours de la ligne du relevé ?).")
    deja = _deja_rapprochees(magasin_id, ligne["compte"]) - set(ligne.get("rapproche_avec") or [])
    if any(r in deja for r in refs):
        raise ValueError("Une de ces opérations est déjà rapprochée d'une autre ligne du relevé.")
    total = round(sum(ops[r]["montant"] for r in refs), 2)
    if abs(total - _num(ligne["montant"])) > 0.01:
        raise ValueError(f"Les montants ne correspondent pas : relevé {_num(ligne['montant']):g}, opérations {total:g}.")
    collection().update_one({"_id": ligne["_id"]}, {"$set": {"rapproche_avec": refs, "statut": "rapproche",
                                                             "updated_at": datetime.utcnow()}})
    return True


def annuler(magasin_id, ligne_id):
    ligne = _releve(magasin_id, ligne_id)
    if not ligne:
        return None
    collection().update_one({"_id": ligne["_id"]}, {"$set": {"rapproche_avec": [], "statut": "non_rapproche",
                                                             "updated_at": datetime.utcnow()}})
    return True


def supprimer_ligne(magasin_id, ligne_id):
    ligne = _releve(magasin_id, ligne_id)
    if not ligne:
        return None
    collection().delete_one({"_id": ligne["_id"]})
    return True


def automatique(magasin_id, compte, debut, fin):
    """Rapproche chaque ligne avec l'opération de même montant à la date la plus proche (± 5 jours)."""
    _verifier_compte(compte)
    marge = timedelta(days=TOLERANCE_JOURS)
    ops = operations_application(magasin_id, compte, debut - marge, fin + marge)
    prises = _deja_rapprochees(magasin_id, compte)
    nombre = 0
    for r in _releves(magasin_id, compte, debut, fin):
        if r.get("statut") == "rapproche":
            continue
        d = _date(r["date_operation"])
        candidats = [o for o in ops if o["ref"] not in prises and abs(o["montant"] - _num(r["montant"])) <= 0.01
                     and abs((_date(o["date"]) - d).days) <= TOLERANCE_JOURS]
        if not candidats:
            continue
        choisi = min(candidats, key=lambda o: abs((_date(o["date"]) - d).total_seconds()))
        prises.add(choisi["ref"])
        collection().update_one({"_id": r["_id"]}, {"$set": {"rapproche_avec": [choisi["ref"]], "statut": "rapproche",
                                                             "updated_at": datetime.utcnow()}})
        nombre += 1
    return nombre


def creer_charge_depuis_ligne(magasin_id, ligne_id, categorie="frais_bancaires", nom=None):
    """Frais, agios… présents sur le relevé mais pas dans l'application : crée la
    charge payée correspondante et la rapproche aussitôt."""
    from models.charge_model import ChargeModel
    from services.cloture_service import verifier_dates

    ligne = _releve(magasin_id, ligne_id)
    if not ligne:
        return None
    montant = _num(ligne["montant"])
    if montant >= 0:
        raise ValueError("Seule une sortie d'argent peut devenir une charge.")
    d = _date(ligne["date_operation"])
    verifier_dates(magasin_id, d)
    charge_id = ChargeModel.create_charge({
        "nom": nom or ligne.get("libelle") or "Frais bancaires", "montant": -montant, "categorie": categorie,
        "magasin_id": str(magasin_id), "date_charge": d, "statut": "payee", "date_paiement": d,
        "mode_paiement": "bancaire" if ligne["compte"] == "521" else "om",
        "reference": ligne.get("reference") or "", "description": "Créée depuis le relevé bancaire",
    })
    collection().update_one({"_id": ligne["_id"]}, {"$set": {
        "rapproche_avec": [f"charge:{charge_id}:paiement"], "statut": "rapproche", "updated_at": datetime.utcnow()}})
    return charge_id

