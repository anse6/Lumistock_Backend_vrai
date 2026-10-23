# services/cloture_service.py
"""Clôture d'exercice et verrouillage des périodes comptables.

Une fois une période verrouillée (jusqu'à une date incluse), plus aucune
opération datée de cette période ne peut être créée, modifiée ou supprimée :
charges, immobilisations, factures fournisseurs et leurs paiements, ventes
(modification, annulation, retour, restauration). Les chiffres déjà
communiqués (déclarations, comptes annuels) ne bougent donc plus.

La clôture d'un exercice fige en plus son compte de résultat et son bilan.
Seule la dernière clôture peut être annulée (réouverture), par un admin.
"""
from datetime import datetime

from bson import ObjectId

from extensions import db
from models.magasin_model import MagasinModel
from services.rapport_service import _date


class PeriodeVerrouillee(ValueError):
    """Opération datée d'une période clôturée."""


def date_verrouillage(magasin_id):
    if not magasin_id or not ObjectId.is_valid(str(magasin_id)):
        return None
    magasin = MagasinModel.get_collection().find_one({"_id": ObjectId(str(magasin_id))}) or {}
    return _date(magasin.get("compta_date_verrouillage"))


def message_verrouillage(limite):
    return (f"La période jusqu'au {limite:%d/%m/%Y} est clôturée : aucune opération de cette période ne peut "
            "être créée, modifiée ou supprimée.")


def verifier_dates(magasin_id, *dates):
    """Lève PeriodeVerrouillee si une des dates tombe dans la période verrouillée."""
    limite = date_verrouillage(magasin_id)
    if not limite:
        return
    for valeur in dates:
        if isinstance(valeur, datetime) and valeur.tzinfo:
            valeur = valeur.astimezone().replace(tzinfo=None)
        d = _date(valeur)
        if d and d <= limite:
            raise PeriodeVerrouillee(message_verrouillage(limite))


def erreur_vente_verrouillee(livraison):
    """(réponse, statut) si la vente appartient à une période clôturée, sinon None."""
    from services.compta_service import heure_locale
    try:
        verifier_dates(livraison.get("magasin_id"), heure_locale(livraison.get("date_livraison") or livraison.get("created_at")))
    except PeriodeVerrouillee as erreur:
        return {"error": f"Vente d'une période clôturée. {erreur}"}, 409
    return None


# --------------------------------------------------------------------------- verrouillage
def _clotures(magasin_id):
    return sorted(db.tables.clotures.find({"magasin_id": ObjectId(str(magasin_id))}), key=lambda c: c.get("exercice") or 0)


def _fin_derniere_cloture(magasin_id):
    clotures = _clotures(magasin_id)
    return _date(clotures[-1]["date_fin"]) if clotures else None


def verrouiller(magasin_id, date):
    """Verrouille jusqu'à la date incluse (None : seulement les exercices clôturés)."""
    d = _date(date)
    plancher = _fin_derniere_cloture(magasin_id)
    if d:
        d = d.replace(hour=23, minute=59, second=59, microsecond=0)
        if d.date() >= datetime.now().date():
            raise ValueError("On ne peut verrouiller qu'une période passée (jusqu'à hier au plus tard).")
    if plancher and (not d or d < plancher):
        raise ValueError(f"L'exercice clôturé jusqu'au {plancher:%d/%m/%Y} reste verrouillé : "
                         "annulez d'abord sa clôture.")
    MagasinModel.get_collection().update_one({"_id": ObjectId(str(magasin_id))},
                                             {"$set": {"compta_date_verrouillage": d}})
    return d


def etat(magasin_id):
    limite = date_verrouillage(magasin_id)
    return {
        "date_verrouillage": limite.isoformat() if limite else None,
        "clotures": [
            {
                "id": str(c["_id"]), "exercice": c.get("exercice"),
                "date_debut": _date(c.get("date_debut")).isoformat(), "date_fin": _date(c.get("date_fin")).isoformat(),
                "resultat": float(c.get("resultat") or 0), "cloture_par": c.get("cloture_par"),
                "cloture_le": _date(c.get("created_at")).isoformat() if c.get("created_at") else None,
                "compte_resultat": c.get("compte_resultat") or [], "bilan": c.get("bilan") or {},
            }
            for c in reversed(_clotures(magasin_id))
        ],
    }


# --------------------------------------------------------------------------- clôture
def cloturer(magasin_id, exercice, par=None):
    """Fige le compte de résultat et le bilan de l'année et verrouille l'exercice."""
    from services import compta_service

    exercice = int(exercice)
    debut, fin = datetime(exercice, 1, 1), datetime(exercice, 12, 31, 23, 59, 59)
    if fin.date() >= datetime.now().date():
        raise ValueError(f"L'exercice {exercice} n'est pas terminé.")
    clotures = _clotures(magasin_id)
    if any(c.get("exercice") == exercice for c in clotures):
        raise ValueError(f"L'exercice {exercice} est déjà clôturé.")
    if clotures and clotures[-1]["exercice"] > exercice:
        raise ValueError("Un exercice plus récent est déjà clôturé.")

    synthese = compta_service.synthese(magasin_id, debut, fin)
    db.tables.clotures.insert_one({
        "magasin_id": ObjectId(str(magasin_id)), "exercice": exercice, "date_debut": debut, "date_fin": fin,
        "resultat": synthese["indicateurs"]["resultat_net"],
        "compte_resultat": synthese["compte_resultat"], "bilan": synthese["bilan"],
        "cloture_par": par, "created_at": datetime.utcnow(),
    })
    limite = date_verrouillage(magasin_id)
    if not limite or limite < fin:
        MagasinModel.get_collection().update_one({"_id": ObjectId(str(magasin_id))},
                                                 {"$set": {"compta_date_verrouillage": fin}})
    return etat(magasin_id)


def rouvrir(magasin_id, exercice):
    """Annule la dernière clôture ; le verrou revient à la fin de l'exercice précédent clôturé."""
    clotures = _clotures(magasin_id)
    if not clotures or clotures[-1].get("exercice") != int(exercice):
        raise ValueError("Seul le dernier exercice clôturé peut être rouvert.")
    db.tables.clotures.delete_one({"_id": clotures[-1]["_id"]})
    precedent = _date(clotures[-2]["date_fin"]) if len(clotures) > 1 else None
    MagasinModel.get_collection().update_one({"_id": ObjectId(str(magasin_id))},
                                             {"$set": {"compta_date_verrouillage": precedent}})
    return etat(magasin_id)


# --------------------------------------------------------------------------- politique de retour
def politique_retour(magasin_id):
    """Pourcentage de la valeur retournée gardé par défaut (frais de retour)."""
    if not magasin_id or not ObjectId.is_valid(str(magasin_id)):
        return 0.0
    magasin = MagasinModel.get_collection().find_one({"_id": ObjectId(str(magasin_id))}) or {}
    try:
        return float(magasin.get("retour_retenue_pourcentage") or 0)
    except (TypeError, ValueError):
        return 0.0


def enregistrer_politique_retour(magasin_id, pourcentage):
    try:
        pourcentage = round(float(pourcentage or 0), 2)
    except (TypeError, ValueError):
        raise ValueError("Pourcentage invalide.")
    if not 0 <= pourcentage <= 100:
        raise ValueError("Le pourcentage doit être compris entre 0 et 100.")
    MagasinModel.get_collection().update_one({"_id": ObjectId(str(magasin_id))},
                                             {"$set": {"retour_retenue_pourcentage": pourcentage}})
    return {"retenue_pourcentage": pourcentage}
