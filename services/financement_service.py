"""Financement du magasin : apports, retraits de l'exploitant, emprunts et remboursements.

Chaque opération est datée et passe par un moyen de paiement : elle entre dans
la trésorerie, le journal (comptes 101, 108, 162, 671) et le bilan. Les
intérêts payés avec un remboursement sont des charges financières : il ne faut
pas les saisir une seconde fois dans les charges.
"""
from datetime import datetime

from bson import ObjectId

from extensions import db

TYPES = {
    "apport_capital": "Apport en capital",
    "apport": "Apport de l'exploitant",
    "retrait": "Retrait de l'exploitant",
    "emprunt": "Emprunt reçu",
    "remboursement": "Remboursement d'emprunt",
}


def _collection():
    return db.tables.financements


def _num(valeur):
    try:
        return float(valeur or 0)
    except (TypeError, ValueError):
        return 0.0


def _date(valeur):
    from services.rapport_service import _date as lire

    return lire(valeur)


def operations(magasin_id):
    return list(_collection().find({"magasin_id": ObjectId(str(magasin_id))}))


def _json(o):
    quand = o.get("date_operation")
    return {
        "_id": str(o["_id"]), "type": o.get("type"), "libelle_type": TYPES.get(o.get("type"), o.get("type")),
        "libelle": o.get("libelle") or TYPES.get(o.get("type"), ""), "tiers": o.get("tiers") or "",
        "montant": _num(o.get("montant")), "interets": _num(o.get("interets")),
        "date": quand.isoformat() if isinstance(quand, datetime) else quand,
        "mode_paiement": o.get("mode_paiement") or "banque", "emprunt_id": str(o["emprunt_id"]) if o.get("emprunt_id") else None,
        "duree_mois": o.get("duree_mois"), "taux": o.get("taux"), "note": o.get("note") or "",
    }


def restant_du(emprunt, toutes, date=None):
    """Capital restant dû d'un emprunt (à la date, sinon aujourd'hui)."""
    rembourse = sum(_num(o.get("montant")) for o in toutes
                    if o.get("type") == "remboursement" and str(o.get("emprunt_id")) == str(emprunt["_id"])
                    and (date is None or (_date(o.get("date_operation")) or datetime.min) <= date))
    return max(_num(emprunt.get("montant")) - rembourse, 0.0)


def liste(magasin_id):
    toutes = operations(magasin_id)
    toutes.sort(key=lambda o: _date(o.get("date_operation")) or datetime.min, reverse=True)
    emprunts = []
    for e in (o for o in toutes if o.get("type") == "emprunt"):
        remboursements = [o for o in toutes if o.get("type") == "remboursement" and str(o.get("emprunt_id")) == str(e["_id"])]
        emprunts.append({
            **_json(e), "restant_du": round(restant_du(e, toutes), 2),
            "rembourse": round(sum(_num(o.get("montant")) for o in remboursements), 2),
            "interets_payes": round(sum(_num(o.get("interets")) for o in remboursements), 2),
            "remboursements": [_json(o) for o in remboursements],
        })
    somme = lambda t: round(sum(_num(o.get("montant")) for o in toutes if o.get("type") == t), 2)
    return {
        "operations": [_json(o) for o in toutes],
        "emprunts": emprunts,
        "totaux": {
            "capital": somme("apport_capital"), "apports": somme("apport"), "retraits": somme("retrait"),
            "emprunte": somme("emprunt"), "restant_du": round(sum(e["restant_du"] for e in emprunts), 2),
        },
    }


def creer(magasin_id, data, par=None):
    from services.cloture_service import verifier_dates

    type_ = data.get("type")
    if type_ not in TYPES:
        raise ValueError("Type d'opération inconnu.")
    montant, interets = round(_num(data.get("montant")), 2), round(_num(data.get("interets")), 2)
    if montant < 0 or interets < 0 or montant + interets <= 0:
        raise ValueError("Indiquez un montant positif.")
    quand = _date(data.get("date")) or datetime.now()
    verifier_dates(magasin_id, quand)
    document = {
        "magasin_id": ObjectId(str(magasin_id)), "type": type_, "montant": montant,
        "libelle": str(data.get("libelle") or "").strip()[:120] or TYPES[type_],
        "tiers": str(data.get("tiers") or "").strip()[:120], "date_operation": quand,
        "mode_paiement": data.get("mode_paiement") or "banque", "note": str(data.get("note") or "").strip()[:300],
        "cree_par": str(par) if par else None, "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
    }
    if type_ == "emprunt":
        if not montant:
            raise ValueError("Indiquez le montant emprunté.")
        document["duree_mois"] = int(_num(data.get("duree_mois"))) or None
        document["taux"] = round(_num(data.get("taux")), 3) or None
    if type_ == "remboursement":
        emprunt_id = str(data.get("emprunt_id") or "")
        toutes = operations(magasin_id)
        emprunt = next((o for o in toutes if str(o["_id"]) == emprunt_id and o.get("type") == "emprunt"), None)
        if not emprunt:
            raise ValueError("Choisissez l'emprunt remboursé.")
        if montant > restant_du(emprunt, toutes) + 0.004:
            raise ValueError("Le capital remboursé dépasse le capital restant dû.")
        document.update({"emprunt_id": emprunt["_id"], "interets": interets, "tiers": emprunt.get("tiers") or ""})
    elif interets:
        raise ValueError("Les intérêts ne concernent qu'un remboursement d'emprunt.")
    document["_id"] = _collection().insert_one(document).inserted_id
    return _json(document)


def supprimer(magasin_id, operation_id):
    from services.cloture_service import verifier_dates

    if not ObjectId.is_valid(str(operation_id)):
        raise LookupError("Opération introuvable.")
    o = _collection().find_one({"_id": ObjectId(str(operation_id)), "magasin_id": ObjectId(str(magasin_id))})
    if not o:
        raise LookupError("Opération introuvable.")
    if o.get("type") == "emprunt" and _collection().find_one({"emprunt_id": o["_id"]}):
        raise ValueError("Supprimez d'abord les remboursements de cet emprunt.")
    verifier_dates(magasin_id, o.get("date_operation"))
    _collection().delete_one({"_id": o["_id"]})
    return {"message": "Opération supprimée."}
