"""Impôts et taxes du magasin : réglages fiscaux, TVA à déclarer et impôt sur le résultat.

- La TVA collectée (ventes) moins la TVA récupérable (charges, factures
  fournisseurs, immobilisations) donne, mois par mois, la TVA à reverser ou un
  crédit reporté sur le mois suivant. Les reversements enregistrés soldent la dette.
- L'impôt sur le résultat est le plus élevé de l'impôt théorique (bénéfice ×
  taux) et du minimum fiscal (chiffre d'affaires × taux minimum), sauf au
  régime forfaitaire (impôt libératoire payé comme une charge « Impôts et taxes »).
- Les taux par défaut viennent du pays du magasin ; ils restent modifiables
  car ils changent avec les lois de finances et la taille de l'entreprise.
"""
from datetime import datetime

from bson import ObjectId

from extensions import db
from services.tva_service import TAUX_TVA, code_pays

# Impôt sur les sociétés (en %, centimes additionnels compris) et minimum
# fiscal (en % du chiffre d'affaires) du régime réel, par code pays ISO.
TAUX_IS = {
    "CM": 33.0, "GA": 30.0, "CG": 28.0, "TD": 35.0, "CF": 30.0, "GQ": 35.0,
    "CI": 25.0, "SN": 30.0, "BJ": 30.0, "BF": 27.5, "ML": 30.0, "NE": 30.0, "TG": 27.0, "GW": 25.0,
    "GN": 25.0, "NG": 30.0, "GH": 25.0, "KE": 30.0, "MA": 20.0, "TN": 15.0, "DZ": 26.0, "EG": 22.5,
    "CD": 30.0, "RW": 28.0, "BI": 30.0, "MG": 20.0, "ZA": 27.0, "MU": 15.0, "MR": 25.0, "DJ": 25.0,
    "FR": 25.0, "BE": 25.0, "LU": 24.94, "CH": 14.0, "DE": 30.0, "ES": 25.0, "IT": 24.0, "PT": 20.0,
    "NL": 25.8, "GB": 25.0, "CA": 26.5, "CN": 25.0, "IN": 25.0, "AE": 9.0, "SA": 20.0, "TR": 25.0,
}
TAUX_MINIMUM = {
    "CM": 2.2, "GA": 1.0, "CG": 1.0, "TD": 1.5, "CF": 1.0, "GQ": 3.0,
    "CI": 0.5, "SN": 0.5, "BJ": 1.0, "BF": 0.5, "ML": 1.0, "NE": 1.0, "TG": 1.0, "GW": 1.0,
    "GN": 3.0, "MA": 0.25, "TN": 0.2, "CD": 1.0, "MG": 0.5, "MR": 2.5,
}
REGIMES = {
    "reel": "Régime réel (impôt sur le bénéfice)",
    "simplifie": "Régime simplifié (impôt sur le bénéfice)",
    "forfait": "Impôt forfaitaire / libératoire (payé comme une charge)",
}
TYPES_OPERATIONS = {
    "tva": "Reversement de TVA",
    "remboursement_tva": "Remboursement de crédit de TVA",
    "is": "Impôt sur le résultat (acompte ou solde)",
}


def _magasin(magasin_id):
    from models.magasin_model import MagasinModel

    return MagasinModel.get_collection().find_one({"_id": ObjectId(str(magasin_id))}) or {}


def _nombre(valeur, libelle, minimum=0.0, maximum=100.0):
    try:
        nombre = float(valeur)
    except (TypeError, ValueError):
        raise ValueError(f"{libelle} invalide.")
    if not minimum <= nombre <= maximum:
        raise ValueError(f"{libelle} : valeur entre {minimum:g} et {maximum:g} attendue.")
    return nombre


# --------------------------------------------------------------------------- réglages
def parametres_fiscaux(magasin_id):
    m = _magasin(magasin_id)
    code = code_pays(m)
    reglages = m.get("fiscalite") or {}
    taux_tva = TAUX_TVA.get(code)
    taux_is_defaut, minimum_defaut = TAUX_IS.get(code), TAUX_MINIMUM.get(code, 0.0)
    regime = reglages.get("regime") if reglages.get("regime") in REGIMES else "reel"
    return {
        "pays": m.get("pays") or code or None,
        "code_pays": code or None,
        "taux_tva": taux_tva,
        "assujetti_tva": bool(reglages.get("assujetti_tva", taux_tva is not None)),
        "regime": regime,
        "regimes": [{"value": k, "label": v} for k, v in REGIMES.items()],
        "taux_is": float(reglages["taux_is"]) if reglages.get("taux_is") is not None else (taux_is_defaut or 0.0),
        "taux_minimum": float(reglages["taux_minimum"]) if reglages.get("taux_minimum") is not None else minimum_defaut,
        "taux_is_defaut": taux_is_defaut,
        "taux_minimum_defaut": minimum_defaut,
        "capital_social": float(reglages.get("capital_social") or 0),
        "personnalise": bool(reglages),
    }


def enregistrer_parametres_fiscaux(magasin_id, data):
    from models.magasin_model import MagasinModel

    actuels = _magasin(magasin_id).get("fiscalite") or {}
    reglages = dict(actuels)
    if "assujetti_tva" in data:
        reglages["assujetti_tva"] = bool(data["assujetti_tva"])
    if "regime" in data:
        if data["regime"] not in REGIMES:
            raise ValueError("Régime fiscal inconnu.")
        reglages["regime"] = data["regime"]
    for cle, libelle in (("taux_is", "Taux de l'impôt sur le résultat"), ("taux_minimum", "Taux du minimum fiscal")):
        if cle in data:
            reglages[cle] = None if data[cle] in (None, "") else _nombre(data[cle], libelle)
    if "capital_social" in data:
        reglages["capital_social"] = _nombre(data["capital_social"] or 0, "Capital social", 0, 1e15)
    MagasinModel.get_collection().update_one({"_id": ObjectId(str(magasin_id))}, {"$set": {"fiscalite": reglages}})
    return parametres_fiscaux(magasin_id)


def impot_sur_resultat(resultat, chiffre_affaires, params):
    """{theorique, minimum, du} pour un résultat avant impôt et un CA donnés."""
    if params["regime"] == "forfait":
        return {"theorique": 0.0, "minimum": 0.0, "du": 0.0}
    theorique = max(resultat, 0.0) * params["taux_is"] / 100
    minimum = max(chiffre_affaires, 0.0) * params["taux_minimum"] / 100
    return {"theorique": theorique, "minimum": minimum, "du": max(theorique, minimum)}


# --------------------------------------------------------------------------- paiements d'impôts
def _collection():
    return db.tables.operations_fiscales


def _date(valeur):
    from services.rapport_service import _date as lire

    return lire(valeur)


def operation_json(o):
    return {
        "_id": str(o["_id"]), "type": o.get("type"), "libelle_type": TYPES_OPERATIONS.get(o.get("type"), o.get("type")),
        "periode": o.get("periode"), "montant": float(o.get("montant") or 0),
        "date": o["date_operation"].isoformat() if isinstance(o.get("date_operation"), datetime) else o.get("date_operation"),
        "mode_paiement": o.get("mode_paiement") or "banque", "reference": o.get("reference") or "", "note": o.get("note") or "",
    }


def operations(magasin_id):
    return list(_collection().find({"magasin_id": ObjectId(str(magasin_id))}))


def _periode_valide(type_, periode):
    texte = str(periode or "").strip()
    if type_ == "is":
        if not texte.isdigit() or len(texte) != 4:
            raise ValueError("Exercice invalide (année attendue, ex. 2026).")
    else:
        try:
            datetime.strptime(texte, "%Y-%m")
        except ValueError:
            raise ValueError("Mois déclaré invalide (format AAAA-MM).")
    return texte


def creer_operation(magasin_id, data, par=None):
    from services.cloture_service import verifier_dates

    type_ = data.get("type")
    if type_ not in TYPES_OPERATIONS:
        raise ValueError("Type de paiement d'impôt inconnu.")
    montant = _nombre(data.get("montant"), "Montant", 0.01, 1e15)
    quand = _date(data.get("date")) or datetime.now()
    verifier_dates(magasin_id, quand)
    document = {
        "magasin_id": ObjectId(str(magasin_id)), "type": type_, "periode": _periode_valide(type_, data.get("periode")),
        "montant": round(montant, 2), "date_operation": quand,
        "mode_paiement": data.get("mode_paiement") or "banque",
        "reference": str(data.get("reference") or "").strip()[:80], "note": str(data.get("note") or "").strip()[:300],
        "cree_par": str(par) if par else None, "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
    }
    document["_id"] = _collection().insert_one(document).inserted_id
    return operation_json(document)


def supprimer_operation(magasin_id, operation_id):
    from services.cloture_service import verifier_dates

    if not ObjectId.is_valid(str(operation_id)):
        raise LookupError("Paiement introuvable.")
    o = _collection().find_one({"_id": ObjectId(str(operation_id)), "magasin_id": ObjectId(str(magasin_id))})
    if not o:
        raise LookupError("Paiement introuvable.")
    verifier_dates(magasin_id, o.get("date_operation"))
    _collection().delete_one({"_id": o["_id"]})
    return {"message": "Paiement supprimé."}


# --------------------------------------------------------------------------- déclarations
def situation(magasin_id, annee):
    """TVA mois par mois et impôt sur le résultat de l'année."""
    from services import compta_service

    params = parametres_fiscaux(magasin_id)
    return compta_service.situation_fiscale(magasin_id, int(annee), params)
