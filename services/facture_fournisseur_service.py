# services/facture_fournisseur_service.py
"""Factures fournisseurs : achats de marchandises à crédit.

Enregistrer une facture fait entrer les articles en stock (au prix d'achat HT
de la facture) ; la dette envers le fournisseur est ensuite soldée par un ou
plusieurs règlements. En comptabilité : 601 + 4452 au débit, 401 au crédit à
la date de la facture, puis 401 / trésorerie à chaque règlement.

Une facture ne se supprime pas (son stock est entré, elle est peut-être dans
une période close). On la corrige ou on l'annule par un AVOIR daté du jour :
les articles rendus au fournisseur sortent du stock et la dette baisse
(401 au débit, 601 + 4452 au crédit). Si la facture était déjà payée, le
fournisseur doit rembourser : ce remboursement s'enregistre comme un règlement
négatif.
"""
from datetime import datetime

from bson import ObjectId

from extensions import db
from models.fournisseur_model import FournisseurModel
from models.produit_model import ProduitModel
from services.cloture_service import verifier_dates
from services.rapport_service import _arrondi, _date, _num


def collection():
    return db.tables.factures_fournisseurs


def _statut(net, paye):
    """Statut d'après le montant dû net des avoirs et ce qui est déjà payé."""
    if net <= 0.004 and paye <= 0.004:
        return "annulee"
    if paye <= 0.004:
        return "a_payer"
    return "payee" if paye >= net - 0.004 else "partielle"


def _net(facture):
    """Montant dû au fournisseur : TTC moins les avoirs."""
    return round(_num(facture.get("montant_ttc")) - sum(_num(a.get("montant_ttc")) for a in facture.get("avoirs") or []), 2)


def get_for_magasin(facture_id, magasin_id):
    if not ObjectId.is_valid(str(facture_id)):
        return None
    return collection().find_one({"_id": ObjectId(str(facture_id)), "magasin_id": ObjectId(str(magasin_id))})


def lister(magasin_id):
    return sorted(collection().find({"magasin_id": ObjectId(str(magasin_id))}),
                  key=lambda f: _date(f.get("date_facture")) or datetime.min, reverse=True)


def serialiser(f):
    maintenant = datetime.now()
    echeance = _date(f.get("date_echeance"))
    ttc, paye, net = _num(f.get("montant_ttc")), _num(f.get("montant_paye")), _net(f)
    statut = _statut(net, paye)
    deja_rendu = {}
    for a in f.get("avoirs") or []:
        for l in a.get("lignes") or []:
            deja_rendu[l["produit_id"]] = deja_rendu.get(l["produit_id"], 0) + l["quantite"]
    iso = lambda v: _date(v).isoformat() if _date(v) else None
    return {
        "_id": str(f["_id"]),
        "numero": f.get("numero") or "",
        "fournisseur_id": str(f["fournisseur_id"]) if f.get("fournisseur_id") else None,
        "fournisseur_nom": f.get("fournisseur_nom") or "",
        "date_facture": iso(f.get("date_facture")),
        "date_echeance": iso(echeance),
        "lignes": f.get("lignes") or [],
        "montant_ht": _arrondi(f.get("montant_ht")),
        "montant_tva": _arrondi(f.get("montant_tva")),
        "montant_ttc": _arrondi(ttc),
        "montant_avoirs": _arrondi(ttc - net),
        "montant_net": _arrondi(net),
        "montant_paye": _arrondi(paye),
        "reste": _arrondi(max(net - paye, 0)),
        # Payé plus que le montant dû après avoir : le fournisseur doit rembourser.
        "a_recevoir": _arrondi(max(paye - net, 0)),
        "statut": statut,
        "en_retard": statut in ("a_payer", "partielle") and bool(echeance) and echeance.date() < maintenant.date(),
        "avoirs": [{**a, "date": iso(a.get("date"))} for a in f.get("avoirs") or []],
        "rendu": deja_rendu,
        "paiements": [{**p, "date": iso(p.get("date"))} for p in f.get("paiements") or []],
        "pieces_jointes": [{k: v for k, v in p.items() if k != "fichier"} for p in f.get("pieces_jointes") or []],
        "note": f.get("note") or "",
    }


def creer(magasin_id, data, admin_id=None):
    """Crée la facture et fait entrer les articles en stock."""
    from services.produit_service import approvisionner_produit_service

    date_facture = _date(data.get("date_facture")) or datetime.now()
    verifier_dates(magasin_id, date_facture)
    lignes_demandees = data.get("lignes") or []
    if not lignes_demandees:
        raise ValueError("Ajoutez au moins un article à la facture.")

    # Toutes les lignes sont vérifiées avant toute entrée en stock.
    lignes = []
    for i, l in enumerate(lignes_demandees, start=1):
        produit = ProduitModel.get_for_magasin(l.get("produit_id"), magasin_id)
        if not produit:
            raise ValueError(f"Ligne {i} : produit introuvable dans ce magasin.")
        try:
            quantite = int(l.get("quantite"))
            prix = float(l.get("prix_achat"))
        except (TypeError, ValueError):
            raise ValueError(f"Ligne {i} : quantité (nombre entier) et prix d'achat obligatoires.")
        if quantite <= 0 or prix < 0:
            raise ValueError(f"Ligne {i} : quantité positive et prix d'achat positif ou nul attendus.")
        lignes.append({"produit_id": str(produit["_id"]), "nom": produit.get("nom") or "Produit",
                       "quantite": quantite, "prix_achat": prix, "total": round(quantite * prix, 2)})

    montant_ht = round(sum(l["total"] for l in lignes), 2)
    montant_tva = round(_num(data.get("montant_tva")), 2)
    if montant_tva < 0:
        raise ValueError("La TVA ne peut pas être négative.")
    montant_ttc = round(montant_ht + montant_tva, 2)

    fournisseur_id, fournisseur_nom = None, (data.get("fournisseur_nom") or "").strip()
    if data.get("fournisseur_id"):
        fournisseur = FournisseurModel.get_by_id(data["fournisseur_id"]) if ObjectId.is_valid(str(data["fournisseur_id"])) else None
        if not fournisseur or str(fournisseur.get("magasin_id")) != str(magasin_id):
            raise ValueError("Fournisseur introuvable dans ce magasin.")
        fournisseur_id, fournisseur_nom = fournisseur["_id"], fournisseur.get("nom") or fournisseur_nom
    if not fournisseur_nom:
        raise ValueError("Indiquez le fournisseur.")

    numero = (data.get("numero") or "").strip()
    if numero and any((f.get("numero") or "").strip().lower() == numero.lower() and
                      str(f.get("fournisseur_id") or f.get("fournisseur_nom")) == str(fournisseur_id or fournisseur_nom)
                      for f in lister(magasin_id)):
        raise ValueError(f"La facture n° {numero} de ce fournisseur est déjà enregistrée.")

    facture_id = ObjectId()
    collection().insert_one({
        "_id": facture_id, "magasin_id": ObjectId(str(magasin_id)), "fournisseur_id": fournisseur_id,
        "fournisseur_nom": fournisseur_nom, "numero": numero, "date_facture": date_facture,
        "date_echeance": _date(data.get("date_echeance")), "lignes": lignes, "montant_ht": montant_ht,
        "montant_tva": montant_tva, "montant_ttc": montant_ttc, "montant_paye": 0.0, "statut": _statut(montant_ttc, 0),
        "paiements": [], "pieces_jointes": [], "note": data.get("note") or "", "cree_par": admin_id,
        "created_at": datetime.utcnow(),
    })
    for l in lignes:
        resultat, statut = approvisionner_produit_service(l["produit_id"], {
            "quantite": l["quantite"], "prix_achat": l["prix_achat"], "admin_id": admin_id, "facture_id": str(facture_id),
        })
        if statut != 200:  # ne devrait pas arriver : lignes vérifiées plus haut
            raise ValueError(resultat.get("error") or "Entrée en stock impossible.")

    paiement = data.get("paiement") or {}
    if _num(paiement.get("montant")) > 0:
        payer(facture_id, magasin_id, paiement, admin_id)
    return get_for_magasin(facture_id, magasin_id)


def payer(facture_id, magasin_id, data, par=None):
    facture = get_for_magasin(facture_id, magasin_id)
    if not facture:
        return None
    montant = round(_num(data.get("montant")), 2)
    net, paye_avant = _net(facture), _num(facture.get("montant_paye"))
    if montant <= 0:
        raise ValueError("Le montant doit être positif.")
    if data.get("type") == "remboursement":
        # Argent rendu par le fournisseur après un avoir sur une facture payée.
        a_recevoir = round(paye_avant - net, 2)
        if montant > a_recevoir + 0.004:
            raise ValueError(f"Le fournisseur ne doit que {max(a_recevoir, 0):g}.")
        montant = -montant
    else:
        reste = round(net - paye_avant, 2)
        if montant > reste + 0.004:
            raise ValueError(f"Le règlement dépasse le reste à payer ({max(reste, 0):g}).")
    date = _date(data.get("date")) or datetime.now()
    verifier_dates(magasin_id, date)
    paiement = {"id": ObjectId().__str__(), "date": date, "montant": montant,
                "mode": data.get("mode") or "cash", "reference": data.get("reference") or "", "par": par}
    paye = round(paye_avant + montant, 2)
    collection().update_one({"_id": facture["_id"]}, {
        "$set": {"montant_paye": paye, "statut": _statut(net, paye),
                 "updated_at": datetime.utcnow()},
        "$push": {"paiements": paiement},
    })
    return get_for_magasin(facture_id, magasin_id)


def annuler_paiement(facture_id, magasin_id, paiement_id):
    facture = get_for_magasin(facture_id, magasin_id)
    if not facture:
        return None
    paiements = facture.get("paiements") or []
    cible = next((p for p in paiements if p.get("id") == paiement_id), None)
    if not cible:
        raise LookupError("Règlement introuvable.")
    verifier_dates(magasin_id, cible.get("date"))
    restants = [p for p in paiements if p.get("id") != paiement_id]
    paye = round(sum(_num(p.get("montant")) for p in restants), 2)
    collection().update_one({"_id": facture["_id"]}, {"$set": {
        "paiements": restants, "montant_paye": paye, "statut": _statut(_net(facture), paye),
        "updated_at": datetime.utcnow()}})
    return get_for_magasin(facture_id, magasin_id)


def modifier(facture_id, magasin_id, data):
    """Seuls l'en-tête et la note se modifient : les lignes ont déjà mouvementé le stock."""
    facture = get_for_magasin(facture_id, magasin_id)
    if not facture:
        return None
    champs = {}
    if "numero" in data:
        champs["numero"] = (data.get("numero") or "").strip()
    if "date_echeance" in data:
        champs["date_echeance"] = _date(data.get("date_echeance"))
    if "note" in data:
        champs["note"] = data.get("note") or ""
    if "montant_tva" in data:
        verifier_dates(magasin_id, facture.get("date_facture"))
        tva = round(_num(data.get("montant_tva")), 2)
        ttc = round(_num(facture.get("montant_ht")) + tva, 2)
        if facture.get("avoirs"):
            raise ValueError("La TVA d'une facture ayant un avoir ne se modifie plus.")
        if tva < 0 or ttc < _num(facture.get("montant_paye")) - 0.004:
            raise ValueError("TVA invalide : le total deviendrait inférieur aux règlements déjà faits.")
        champs.update({"montant_tva": tva, "montant_ttc": ttc, "statut": _statut(ttc, _num(facture.get("montant_paye")))})
    champs["updated_at"] = datetime.utcnow()
    collection().update_one({"_id": facture["_id"]}, {"$set": champs})
    return get_for_magasin(facture_id, magasin_id)


def avoir(facture_id, magasin_id, data, par=None):
    """Avoir du fournisseur : articles rendus (ou facture annulée), daté du jour.

    Les articles sortent du stock au prix de la facture et la dette baisse
    d'autant, TVA comprise au prorata. ``lignes`` absent : tout ce qui n'a pas
    encore été rendu (annulation complète de la facture).
    """
    from services import valorisation_service

    facture = get_for_magasin(facture_id, magasin_id)
    if not facture:
        return None
    date = datetime.now()
    verifier_dates(magasin_id, date)
    factures_lignes = {l["produit_id"]: l for l in facture.get("lignes") or []}
    deja = {}
    for a in facture.get("avoirs") or []:
        for l in a.get("lignes") or []:
            deja[l["produit_id"]] = deja.get(l["produit_id"], 0) + l["quantite"]
    demandees = data.get("lignes")
    if demandees is None:
        demandees = [{"produit_id": pid, "quantite": l["quantite"] - deja.get(pid, 0)}
                     for pid, l in factures_lignes.items() if l["quantite"] - deja.get(pid, 0) > 0]

    lignes = []
    for d in demandees:
        pid = str(d.get("produit_id"))
        origine = factures_lignes.get(pid)
        try:
            quantite = int(d.get("quantite") or 0)
        except (TypeError, ValueError):
            quantite = 0
        if quantite <= 0:
            continue
        if not origine:
            raise ValueError("Ce produit ne figure pas sur la facture.")
        if quantite > origine["quantite"] - deja.get(pid, 0):
            raise ValueError(f"{origine['nom']} : {origine['quantite'] - deja.get(pid, 0)} unité(s) au plus peuvent être rendues.")
        produit = ProduitModel.get_for_magasin(pid, magasin_id)
        if not produit or _num(produit.get("quantite")) < quantite:
            raise ValueError(f"{origine['nom']} : stock insuffisant ({_num((produit or {}).get('quantite')):g} en stock) "
                             "pour rendre ces articles.")
        lignes.append({"produit_id": pid, "nom": origine["nom"], "quantite": quantite, "prix_achat": origine["prix_achat"],
                       "total": round(quantite * origine["prix_achat"], 2)})
    if not lignes:
        raise ValueError("Indiquez les articles rendus au fournisseur.")

    montant_ht = round(sum(l["total"] for l in lignes), 2)
    ht_facture = _num(facture.get("montant_ht"))
    montant_tva = round(_num(facture.get("montant_tva")) * montant_ht / ht_facture, 2) if ht_facture else 0.0
    methode = valorisation_service.get_methode(magasin_id)
    for l in lignes:
        valorisation_service.sortie(l["produit_id"], l["quantite"], methode)
        ProduitModel.decrement_stock(l["produit_id"], l["quantite"], {"admin_id": par},
                                     type_mouvement="annulation approvisionnement (avoir fournisseur)",
                                     details={"prix_unitaire": l["prix_achat"], "facture_id": str(facture["_id"])})
    nouvel_avoir = {"id": str(ObjectId()), "date": date, "lignes": lignes, "montant_ht": montant_ht,
                    "montant_tva": montant_tva, "montant_ttc": round(montant_ht + montant_tva, 2),
                    "motif": data.get("motif") or "", "par": par}
    avoirs = (facture.get("avoirs") or []) + [nouvel_avoir]
    net = round(_num(facture.get("montant_ttc")) - sum(_num(a["montant_ttc"]) for a in avoirs), 2)
    collection().update_one({"_id": facture["_id"]}, {"$set": {
        "avoirs": avoirs, "statut": _statut(net, _num(facture.get("montant_paye"))), "updated_at": datetime.utcnow()}})
    return get_for_magasin(facture_id, magasin_id)
