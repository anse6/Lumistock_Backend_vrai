"""Compte d'un client : dette d'ouverture, règlements et situation.

- Dette d'ouverture : ce que le client devait AVANT l'installation de
  Lumistock (cahier de crédit). Elle se saisit une seule fois, sur la fiche du
  client, et n'est jamais ajoutée au total d'une vente : sinon un impayé
  serait compté deux fois (dans la vente d'origine et dans la nouvelle).
- Règlement : l'argent versé par le client est réparti automatiquement, comme
  le lettrage d'Odoo : d'abord la dette d'ouverture (la plus ancienne), puis
  les ventes impayées de la plus ancienne à la plus récente. Un surplus va
  sur le compte du client (avance utilisable sur ses prochains achats).
"""
from datetime import datetime

from bson import ObjectId

from extensions import db
from models.client_model import ClientModel
from models.livraison_model import LivraisonModel

MODES_REGLEMENT = ("cash", "om", "momo", "bancaire")


class ErreurCompte(Exception):
    def __init__(self, message, statut=400):
        super().__init__(message)
        self.statut = statut


def _num(valeur):
    try:
        return float(valeur or 0)
    except (TypeError, ValueError):
        return 0.0


def _collection():
    return db.tables.dettes_ouverture


def _date(valeur):
    if isinstance(valeur, datetime):
        return valeur
    if isinstance(valeur, str) and valeur:
        try:
            return datetime.fromisoformat(valeur.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- dette d'ouverture
def dette_ouverture(client_id):
    return _collection().find_one({"client_id": ObjectId(str(client_id)), "deleted_at": {"$exists": False}})


def reste_ouverture(dette):
    if not dette:
        return 0.0
    return max(round(_num(dette.get("montant")) - _num(dette.get("montant_paye")), 2), 0.0)


def restes_ouverture(magasin_id, cree_par=None):
    """{client_id: reste dû} des dettes d'ouverture non soldées du magasin.

    :param cree_par: ne garde que les dettes saisies par cet utilisateur (gérant).
    """
    query = {"magasin_id": ObjectId(str(magasin_id)), "deleted_at": {"$exists": False}}
    restes = {}
    for d in _collection().find(query):
        if cree_par and str(d.get("cree_par")) != str(cree_par):
            continue
        reste = reste_ouverture(d)
        if reste > 0:
            restes[str(d["client_id"])] = reste
    return restes


def serialiser_dette_ouverture(dette):
    if not dette:
        return None
    return {
        "id": str(dette["_id"]),
        "montant": round(_num(dette.get("montant")), 2),
        "montant_paye": round(_num(dette.get("montant_paye")), 2),
        "reste": reste_ouverture(dette),
        "date_origine": dette["date_origine"].isoformat() if isinstance(dette.get("date_origine"), datetime) else None,
        "note": dette.get("note") or "",
        "cree_par": str(dette.get("cree_par") or ""),
        "created_at": dette["created_at"].isoformat() if isinstance(dette.get("created_at"), datetime) else None,
        "paiements": [{**p, "date": p["date"].isoformat() if isinstance(p.get("date"), datetime) else p.get("date")}
                      for p in dette.get("paiements") or []],
    }


def definir_dette_ouverture(client, montant, date_origine=None, note=None, par=None, peut_modifier=True):
    """Crée, modifie ou supprime (montant 0) la dette d'ouverture du client.

    :param peut_modifier: False pour un gérant : il peut saisir la dette d'un
        client qui n'en a pas encore, mais pas changer une dette existante.
    """
    if isinstance(montant, bool):
        raise ErreurCompte("Montant invalide.")
    try:
        montant = round(float(montant), 2)
    except (TypeError, ValueError):
        raise ErreurCompte("Montant invalide.")
    if montant < 0:
        raise ErreurCompte("La dette ne peut pas être négative.")
    origine = _date(date_origine) if date_origine else None
    if date_origine and not origine:
        raise ErreurCompte("Date d'origine invalide.")
    if origine and origine > datetime.now():
        raise ErreurCompte("La date d'origine ne peut pas être dans le futur.")

    existante = dette_ouverture(client["_id"])
    if existante and not peut_modifier:
        raise ErreurCompte("Ce client a déjà une dette d'ouverture : seul un admin ou un manager peut la modifier.", 403)
    deja_paye = _num((existante or {}).get("montant_paye"))
    if montant < deja_paye - 0.005:
        raise ErreurCompte(f"Le client a déjà réglé {deja_paye:g} sur cette dette : le montant ne peut pas être inférieur.")

    maintenant = datetime.utcnow()
    if existante:
        if montant == 0:
            _collection().update_one({"_id": existante["_id"]}, {"$set": {"deleted_at": maintenant, "updated_at": maintenant}})
            return None
        champs = {"montant": montant, "note": (note if note is not None else existante.get("note") or "")[:300],
                  "updated_at": maintenant}
        if date_origine is not None:
            champs["date_origine"] = origine
        _collection().update_one({"_id": existante["_id"]}, {"$set": champs})
        return dette_ouverture(client["_id"])
    if montant == 0:
        return None
    _collection().insert_one({
        "magasin_id": ObjectId(str(client["magasin_id"])),
        "client_id": ObjectId(str(client["_id"])),
        "montant": montant,
        "montant_paye": 0.0,
        "date_origine": origine,
        "note": (note or "")[:300],
        "cree_par": str(par) if par else None,
        "paiements": [],
        "created_at": maintenant,
        "updated_at": maintenant,
    })
    return dette_ouverture(client["_id"])


# --------------------------------------------------------------------------- situation
def ventes_impayees(client_id, magasin_id, livreur_id=None):
    """Ventes du client avec un reste à payer, de la plus ancienne à la plus récente."""
    query = {"client_id": ObjectId(str(client_id)), "magasin_id": ObjectId(str(magasin_id)),
             "deleted_at": {"$exists": False}}
    if livreur_id:
        query["livreur_id"] = ObjectId(str(livreur_id))
    ventes = []
    for l in LivraisonModel.get_collection().find(query):
        reste = round(_num(l.get("montant_total")) - _num(l.get("montant_paye")), 2)
        if reste > 0.004:
            ventes.append(l)
    ventes.sort(key=lambda l: l.get("date_livraison") or l.get("created_at") or datetime.min)
    return ventes


def situation_client(client, livreur_id=None):
    """Ce que le client doit (dette d'ouverture + ventes impayées) et son avance."""
    from services.rapport_service import facture as numero_facture

    dette = dette_ouverture(client["_id"])
    ventes = []
    for l in ventes_impayees(client["_id"], client["magasin_id"], livreur_id):
        quand = l.get("date_livraison") or l.get("created_at")
        ventes.append({
            "livraison_id": str(l["_id"]),
            "facture": numero_facture(l["_id"]),
            "date": quand.isoformat() if isinstance(quand, datetime) else None,
            "montant_total": round(_num(l.get("montant_total")), 2),
            "montant_paye": round(_num(l.get("montant_paye")), 2),
            "reste": round(_num(l.get("montant_total")) - _num(l.get("montant_paye")), 2),
        })
    total_ventes = round(sum(v["reste"] for v in ventes), 2)
    reste_dette = reste_ouverture(dette)
    return {
        "client_id": str(client["_id"]),
        "nom": client.get("nom") or "",
        "dette_ouverture": serialiser_dette_ouverture(dette),
        "ventes_impayees": ventes,
        "total_ventes_impayees": total_ventes,
        "total_du": round(reste_dette + total_ventes, 2),
        "avance": round(_num(client.get("montant_en_caisse")), 2),
    }


# --------------------------------------------------------------------------- règlement
def regler(client, montant, methode="cash", par=None, livreur_id=None):
    """Encaisse un règlement du client et le répartit sur ce qu'il doit.

    Ordre : dette d'ouverture, puis ventes impayées de la plus ancienne à la
    plus récente ; le surplus est crédité sur le compte du client.
    :param livreur_id: pour un gérant, seules SES ventes sont réglées (la
        dette d'ouverture du client reste réglable par tous).
    :return: (montant, affectations, avance créditée)
    """
    if isinstance(montant, bool):
        raise ErreurCompte("Montant invalide.")
    try:
        montant = round(float(montant), 2)
    except (TypeError, ValueError):
        raise ErreurCompte("Montant invalide.")
    if montant <= 0:
        raise ErreurCompte("Le montant doit être positif.")
    methode = str(methode or "cash").strip().lower()
    if methode not in MODES_REGLEMENT:
        raise ErreurCompte("Mode de paiement invalide (cash, om, momo ou bancaire).")

    from services.rapport_service import facture as numero_facture

    reste = montant
    affectations = []
    maintenant = datetime.utcnow()

    dette = dette_ouverture(client["_id"])
    du = reste_ouverture(dette)
    if du > 0:
        part = round(min(reste, du), 2)
        paiement = {"methode": methode, "montant": part, "date": maintenant, "par": str(par) if par else None}
        _collection().update_one({"_id": dette["_id"]}, {"$set": {
            "montant_paye": round(_num(dette.get("montant_paye")) + part, 2),
            "paiements": list(dette.get("paiements") or []) + [paiement],
            "updated_at": maintenant,
        }})
        affectations.append({"type": "dette_ouverture", "libelle": "Dette d'avant Lumistock", "montant": part,
                             "reste": round(du - part, 2)})
        reste = round(reste - part, 2)

    for vente in ventes_impayees(client["_id"], client["magasin_id"], livreur_id):
        if reste <= 0:
            break
        du = round(_num(vente.get("montant_total")) - _num(vente.get("montant_paye")), 2)
        part = round(min(reste, du), 2)
        LivraisonModel.ajouter_paiement(vente["_id"], part, methode=methode, par=par)
        affectations.append({"type": "vente", "livraison_id": str(vente["_id"]), "libelle": numero_facture(vente["_id"]),
                             "montant": part, "reste": round(du - part, 2)})
        reste = round(reste - part, 2)

    avance = 0.0
    if reste > 0:
        from services.client_service import tracer_compte_client
        ClientModel.incrementer_montant_caisse(client["_id"], reste)
        tracer_compte_client(client["_id"], reste, methode, par, "depot")
        avance = reste
        affectations.append({"type": "avance", "libelle": "Avance sur le compte du client", "montant": reste})
    return montant, affectations, avance


# --------------------------------------------------------------------------- comptabilité
def reglements_ouverture(magasin_id, par=None):
    """Règlements des dettes d'ouverture (argent reçu), pour les encaissements."""
    query = {"magasin_id": ObjectId(str(magasin_id))}
    resultat = []
    for d in _collection().find(query):
        for p in d.get("paiements") or []:
            if par and str(p.get("par")) != str(par):
                continue
            resultat.append({"dette_id": str(d["_id"]), "client_id": d.get("client_id"), "date": p.get("date"),
                             "methode": p.get("methode") or "cash", "montant": _num(p.get("montant")),
                             "par": p.get("par")})
    return resultat


def creances_ouverture_au(magasin_id, date):
    """Reste dû des dettes d'ouverture à la date (comptées depuis leur saisie)."""
    total = 0.0
    for d in _collection().find({"magasin_id": ObjectId(str(magasin_id))}):
        saisie = d.get("created_at")
        supprimee = d.get("deleted_at")
        if not saisie or saisie > date or (supprimee and supprimee <= date):
            continue
        regle = sum(_num(p.get("montant")) for p in d.get("paiements") or []
                    if isinstance(p.get("date"), datetime) and p["date"] <= date)
        total += max(_num(d.get("montant")) - regle, 0.0)
    return round(total, 2)
