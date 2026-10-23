# services/compta_service.py
"""Comptabilité du magasin : compte de résultat, trésorerie, TVA, impôt sur le
résultat, bilan SYSCOHADA et journal comptable (plan SYSCOHADA révisé).

Les chiffres sont reconstitués à partir des opérations déjà saisies dans
l'application (ventes, paiements, retours, comptes clients, charges,
approvisionnements, factures fournisseurs, pertes, immobilisations et leurs
cessions, apports, emprunts, impôts versés) : aucune double saisie n'est demandée.

Conventions :
- Chiffre d'affaires = ventes HT nettes des réductions et des retours, sans la
  TVA (due à l'État) ni la dette ancienne reportée sur une facture.
- Coût des ventes = quantités vendues × prix d'achat, comme le rapport des ventes.
  Dans le journal, achats (601) + variation des stocks (6031) = coût des ventes
  + pertes : le journal et le compte de résultat donnent le même résultat.
- Charges : constatées à leur date (engagement) ; décaissées à leur date de paiement.
- Achats de marchandises sans facture fournisseur : réglés comptant (caisse).
- Impôt sur le résultat : le plus élevé du bénéfice × taux et du minimum fiscal
  (CA × taux minimum), selon les réglages fiscaux du magasin.
"""
import calendar
from collections import defaultdict
from datetime import datetime, timedelta

from bson import ObjectId

from extensions import db
from models.asset_model import AssetModel
from models.charge_model import ChargeModel
from models.magasin_model import MagasinModel
from models.produit_model import ProduitModel
from services import fiscal_service
from services.rapport_service import (
    MODE_CANONIQUE,
    MODES,
    _arrondi,
    UN_JOUR,
    _date,
    _norm,
    _num,
    _ventes_du_magasin,
    facture,
    heure_locale,
    famille_mouvement,
    rapport_encaissements,
)

# --------------------------------------------------------------------------- plan comptable
# clé : (libellé, compte SYSCOHADA, rubrique du compte de résultat)
CATEGORIES_CHARGES = {
    "achats_fournitures": ("Fournitures et petits achats", "605", "externes"),
    "electricite": ("Électricité et eau", "605", "externes"),
    "transport": ("Transport et carburant", "618", "externes"),
    "loyer": ("Loyer et charges locatives", "622", "externes"),
    "entretien": ("Entretien et réparations", "624", "externes"),
    "assurance": ("Assurances", "625", "externes"),
    "publicite": ("Publicité et marketing", "627", "externes"),
    "telecom": ("Téléphone et internet", "628", "externes"),
    "frais_bancaires": ("Frais bancaires et mobile money", "631", "externes"),
    "honoraires": ("Honoraires et prestataires", "632", "externes"),
    "impots": ("Impôts et taxes (patente…)", "641", "impots"),
    "salaire": ("Salaires", "661", "personnel"),
    "charges_sociales": ("Charges sociales (CNSS…)", "664", "personnel"),
    "interets": ("Intérêts d'emprunt", "671", "financieres"),
    "autre": ("Autres charges", "658", "externes"),
}
# Catégories dont la facture porte normalement une TVA récupérable. Les
# salaires, cotisations, impôts, intérêts et assurances (taxe spéciale) n'en ont pas.
CATEGORIES_AVEC_TVA = {"achats_fournitures", "electricite", "transport", "loyer", "entretien", "publicite",
                       "telecom", "frais_bancaires", "honoraires", "autre"}
# Dette d'une charge à payer, selon sa nature.
COMPTES_DETTES = {"salaire": "422", "charges_sociales": "431", "impots": "447"}
# Immobilisation : (compte d'actif, compte d'amortissement, libellé)
COMPTES_IMMOBILISATIONS = {
    "informatique": ("2442", "2844", "Matériel informatique"),
    "mobilier": ("2444", "2844", "Mobilier de bureau et de magasin"),
    "vehicule": ("245", "2845", "Matériel de transport"),
    "equipement": ("241", "2841", "Matériel et outillage"),
    "autre": ("244", "2844", "Autres matériels"),
}
LIBELLES_COMPTES = {
    "101": "Capital social", "108": "Compte de l'exploitant", "109": "Situation nette d'ouverture", "121": "Report à nouveau", "131": "Résultat net",
    "162": "Emprunts auprès des établissements de crédit", "31": "Marchandises",
    "401": "Fournisseurs", "409": "Fournisseurs débiteurs", "411": "Clients", "4191": "Clients, avances reçues",
    "422": "Personnel, rémunérations dues", "431": "Sécurité sociale", "441": "État, impôt sur les bénéfices",
    "4431": "TVA facturée sur ventes", "4441": "État, TVA due", "4449": "État, crédit de TVA à reporter",
    "4451": "TVA récupérable sur immobilisations", "4452": "TVA récupérable sur achats",
    "447": "État, impôts retenus et autres taxes", "521": "Banque", "552": "Monnaie électronique (mobile money)",
    "565": "Banques, découverts", "571": "Caisse", "601": "Achats de marchandises",
    "6031": "Variation des stocks de marchandises", "681": "Dotations aux amortissements",
    "701": "Ventes de marchandises", "812": "Valeur comptable des cessions d'immobilisations",
    "822": "Produits des cessions d'immobilisations", "891": "Impôts sur le résultat",
    "241": "Matériel et outillage", "2442": "Matériel informatique", "2444": "Mobilier", "244": "Matériel et mobilier",
    "245": "Matériel de transport", "2841": "Amortissements du matériel",
    "2844": "Amortissements du matériel et mobilier", "2845": "Amortissements du matériel de transport",
    **{compte: libelle for libelle, compte, _ in CATEGORIES_CHARGES.values()},
}
LIBELLES_COMPTES["605"] = "Autres achats (fournitures, énergie)"
LIBELLES_COMPTES["7078"] = "Autres produits accessoires (frais de retour)"
LIBELLES_TRESORERIE = {"521": "Banque", "552": "Mobile money", "571": "Caisse"}


def categorie_charge(cle):
    return CATEGORIES_CHARGES.get(cle) or CATEGORIES_CHARGES["autre"]


def compte_dette(categorie):
    return COMPTES_DETTES.get(categorie, "401")


def compte_tresorerie(mode):
    """(compte, code journal) du moyen de paiement."""
    m = MODE_CANONIQUE.get(_norm(mode), _norm(mode) or "cash")
    if m in ("om", "momo", "mobile money", "mobile_money", "wave", "flooz"):
        return "552", "MM"
    if m in ("bancaire", "banque", "cheque", "chèque", "virement", "carte"):
        return "521", "BQ"
    return "571", "CA"


def libelle_mode(mode):
    m = MODE_CANONIQUE.get(_norm(mode), _norm(mode) or "cash")
    return MODES.get(m, {"banque": "Banque", "cheque": "Chèque"}.get(m, (mode or "Espèces").capitalize()))


def comptes_immobilisation(categorie):
    return COMPTES_IMMOBILISATIONS.get(categorie) or COMPTES_IMMOBILISATIONS["autre"]


# --------------------------------------------------------------------------- données
def _charge_normalisee(c):
    statut = c.get("statut") or "payee"  # anciennes charges : payées le jour même
    date_charge = _date(c.get("date_charge")) or _date(c.get("created_at"))
    montant = _num(c.get("montant"))
    tva = min(_num(c.get("montant_tva")), montant)
    return {
        "id": str(c["_id"]),
        "nom": c.get("nom") or "Charge",
        "categorie": c.get("categorie") if c.get("categorie") in CATEGORIES_CHARGES else "autre",
        "montant": montant,
        "tva": tva,
        "ht": montant - tva,
        "date": date_charge,
        "statut": statut,
        "date_paiement": (_date(c.get("date_paiement")) or date_charge) if statut == "payee" else None,
        "date_echeance": _date(c.get("date_echeance")),
        "mode_paiement": c.get("mode_paiement") or "cash",
        "fournisseur": c.get("fournisseur") or "",
        "reference": c.get("reference") or "",
    }


def _vente_normalisee(l):
    lignes = l.get("produits", []) or []
    brut = _num(l.get("montant_brut")) or sum(_num(p.get("quantite")) * _num(p.get("prix")) for p in lignes)
    ht = _num(l.get("montant_produits")) or max(brut - _num(l.get("total_reductions")), 0)
    return {
        "id": str(l["_id"]),
        "date": heure_locale(l.get("date_livraison") or l.get("created_at")),
        "ht": ht,
        "tva": _num(l.get("montant_tva")),
        "cout": sum(_num(p.get("quantite")) * _num(p.get("prix_achat")) for p in lignes),
        "reste": max(_num(l.get("montant_total")) - _num(l.get("montant_paye")), 0),
        "deduit": _num(l.get("montant_deduit_caisse")),
    }


class _Contexte:
    """Données du magasin lues une seule fois pour tous les calculs."""

    def __init__(self, magasin_id, debut, fin):
        self.magasin_id = magasin_id
        oid = ObjectId(magasin_id)
        self.charges = [_charge_normalisee(c) for c in ChargeModel.get_all_by_magasin(magasin_id)]
        self.immobilisations = AssetModel.get_all_by_magasin(magasin_id)
        # Fenêtre élargie d'un jour : les dates enregistrées sont en UTC.
        self.ventes = [_vente_normalisee(l) for l in _ventes_du_magasin(magasin_id, debut - UN_JOUR, fin + UN_JOUR)]
        self._charger_retours(magasin_id)
        # Mouvements de stock valorisés au prix d'achat. Un réapprovisionnement
        # rattaché à une facture fournisseur est un achat à crédit : il est
        # comptabilisé et payé par la facture, pas au comptant.
        self.mouvements = []
        self.produits = list(ProduitModel.get_collection().find({"magasin_id": oid}))
        for p in self.produits:
            prix_defaut = _num(p.get("prix_achat"))
            for n, m in enumerate(p.get("mouvements_stock") or []):
                quand = heure_locale(m.get("date"))
                if not quand:
                    continue
                q = _num(m.get("quantite"))
                famille, _ = famille_mouvement(m.get("type"), q)
                prix = _num(m.get("prix_unitaire")) or prix_defaut
                self.mouvements.append({"date": quand, "famille": famille, "valeur": q * prix,
                                        "produit": p.get("nom") or "Produit", "ref": f"achat:{p['_id']}:{n}",
                                        "facture_id": m.get("facture_id")})
        self.factures = []
        for f in db.tables.factures_fournisseurs.find({"magasin_id": oid}):
            self.factures.append({
                "id": str(f["_id"]), "date": _date(f.get("date_facture")), "numero": f.get("numero") or "",
                "fournisseur": f.get("fournisseur_nom") or "", "ht": _num(f.get("montant_ht")),
                "tva": _num(f.get("montant_tva")), "ttc": _num(f.get("montant_ttc")),
                "paiements": [{**r, "date": _date(r.get("date")), "montant": _num(r.get("montant"))}
                              for r in f.get("paiements") or []],
                "avoirs": [{**a, "date": _date(a.get("date")), "ht": _num(a.get("montant_ht")),
                            "tva": _num(a.get("montant_tva")), "ttc": _num(a.get("montant_ttc"))}
                           for a in f.get("avoirs") or []],
            })
        self.fiscal = fiscal_service.parametres_fiscaux(magasin_id)
        self.financements = [{
            "id": str(o["_id"]), "type": o.get("type"), "montant": _num(o.get("montant")),
            "interets": _num(o.get("interets")), "date": _date(o.get("date_operation")),
            "mode": o.get("mode_paiement") or "banque", "libelle": o.get("libelle") or "", "tiers": o.get("tiers") or "",
            "emprunt_id": str(o["emprunt_id"]) if o.get("emprunt_id") else None,
        } for o in db.tables.financements.find({"magasin_id": oid})]
        self.operations_fiscales = [{
            "id": str(o["_id"]), "type": o.get("type"), "periode": o.get("periode") or "", "montant": _num(o.get("montant")),
            "date": _date(o.get("date_operation")), "mode": o.get("mode_paiement") or "banque",
            "reference": o.get("reference") or "",
        } for o in db.tables.operations_fiscales.find({"magasin_id": oid})]
        self._charger_comptes_clients(oid)

    def _charger_retours(self, magasin_id):
        """Retours clients, comptés à LEUR date.

        Un retour modifie la vente d'origine (quantités et montants restants).
        Pour qu'une période close ne bouge plus, la vente est ramenée à ses
        valeurs d'origine (actuelles + retours) et chaque retour vient en
        déduction des ventes de la période où il a eu lieu.
        """
        from models.livraison_model import LivraisonModel

        collection = LivraisonModel.get_collection()
        supprimees = {str(l["_id"]) for l in collection.find({"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": True}})}
        ventes = {v["id"]: v for v in self.ventes}
        self.retours = []
        for r in db.tables.retours_livraison.find({"magasin_id": ObjectId(magasin_id)}):
            vente_id = str(r.get("livraison_id"))
            quand = heure_locale(r.get("created_at"))
            if vente_id in supprimees or not quand:
                continue
            if r.get("montant_ht") is not None:
                ht, tva, cout = _num(r.get("montant_ht")), _num(r.get("montant_tva")), _num(r.get("cout_retour"))
            else:  # retour enregistré avant le détail comptable : TVA comprise, coût d'après la vente
                vente = collection.find_one({"_id": r.get("livraison_id")}) or {}
                prix = {str(p.get("produit_id")): _num(p.get("prix_achat")) for p in vente.get("produits") or []}
                ht, tva = _num(r.get("montant_retour")), 0.0
                cout = sum(_num(p.get("quantite")) * prix.get(str(p.get("produit_id")), 0) for p in r.get("produits") or [])
            retour = {"id": str(r["_id"]), "vente_id": vente_id, "date": quand, "ht": ht, "tva": tva, "cout": cout,
                      "frais": _num(r.get("frais_retour"))}
            self.retours.append(retour)
            if vente_id in ventes:
                ventes[vente_id]["ht"] += ht
                ventes[vente_id]["tva"] += tva
                ventes[vente_id]["cout"] += cout

    def _charger_comptes_clients(self, oid):
        """Dépôts et retraits d'argent sur les comptes clients, et avances actuelles."""
        self.comptes_clients, self.avances_clients = [], 0.0
        for c in db.tables.clients.find({"magasin_id": oid}):
            self.avances_clients += max(_num(c.get("montant_en_caisse")), 0)
            for n, m in enumerate(c.get("mouvements_compte") or []):
                quand = _date(m.get("date"))
                if quand and _num(m.get("montant")):
                    self.comptes_clients.append({"date": quand, "montant": _num(m.get("montant")), "mode": m.get("mode") or "cash",
                                                 "client": c.get("nom") or "Client", "ref": f"compte:{c['_id']}:{n}"})

    # ------------------------------------------------------------ sélections par période
    def retours_entre(self, debut, fin):
        return [r for r in self.retours if debut <= r["date"] <= fin]

    def ventes_entre(self, debut, fin):
        return [v for v in self.ventes if v["date"] and debut <= v["date"] <= fin]

    def charges_entre(self, debut, fin):
        return [c for c in self.charges if c["date"] and debut <= c["date"] <= fin]

    def achats_comptant(self, debut, fin):
        return [m for m in self.mouvements if m["famille"] == "approvisionnements" and not m["facture_id"]
                and debut <= m["date"] <= fin]

    def achats_entre(self, debut, fin):
        return sum(m["valeur"] for m in self.achats_comptant(debut, fin))

    def pertes_entre(self, debut, fin):
        return sum(-m["valeur"] for m in self.mouvements if m["famille"] == "pertes" and debut <= m["date"] <= fin)

    def factures_entre(self, debut, fin):
        return [f for f in self.factures if f["date"] and debut <= f["date"] <= fin]

    def reglements_fournisseurs(self, debut, fin):
        return [(f, r) for f in self.factures for r in f["paiements"] if r["date"] and debut <= r["date"] <= fin]

    def avoirs_entre(self, debut, fin):
        return [(f, a) for f in self.factures for a in f["avoirs"] if a["date"] and debut <= a["date"] <= fin]

    def financements_entre(self, debut, fin, *types):
        return [o for o in self.financements if o["date"] and debut <= o["date"] <= fin and (not types or o["type"] in types)]

    def fiscales_entre(self, debut, fin, *types):
        return [o for o in self.operations_fiscales if o["date"] and debut <= o["date"] <= fin and (not types or o["type"] in types)]

    def comptes_clients_entre(self, debut, fin):
        return [m for m in self.comptes_clients if debut <= m["date"] <= fin]

    def acquisitions_entre(self, debut, fin):
        return [a for a in self.immobilisations if (d := _date(a.get("date_achat"))) and debut <= d <= fin]

    def cessions_entre(self, debut, fin):
        """[(immobilisation, prix de cession, valeur nette au jour de la cession)]"""
        cessions = []
        for a in self.immobilisations:
            quand = AssetModel.date_cession(a)
            if quand and debut <= quand <= fin:
                vnc = AssetModel.valeur_nette({**a, "date_cession": None}, quand)
                cessions.append((a, _num(a.get("prix_cession")), vnc))
        return cessions

    def _soldes_factures(self, date):
        """Par facture : dû net des avoirs moins payé, à la date (+ dette, − créance)."""
        for f in self.factures:
            if f["date"] and f["date"] <= date:
                yield (f["ttc"] - sum(a["ttc"] for a in f["avoirs"] if a["date"] and a["date"] <= date)
                       - sum(r["montant"] for r in f["paiements"] if r["date"] and r["date"] <= date))

    def dettes_factures(self, date):
        """Reste dû aux fournisseurs sur les factures reçues jusqu'à la date."""
        return sum(max(s, 0) for s in self._soldes_factures(date))

    def creances_fournisseurs(self, date):
        """Avoirs déjà payés que les fournisseurs doivent rembourser."""
        return sum(max(-s, 0) for s in self._soldes_factures(date))

    def charges_dues(self, date):
        """Charges engagées jusqu'à la date et pas encore payées à cette date."""
        return [c for c in self.charges if c["date"] and c["date"] <= date
                and (c["statut"] != "payee" or (c["date_paiement"] and c["date_paiement"] > date))]

    def dotations_entre(self, debut, fin):
        return sum(AssetModel.amortissement_entre(a, debut, fin) for a in self.immobilisations)

    def tva_entre(self, debut, fin):
        """TVA collectée et récupérable de la période, par origine."""
        collectee = sum(v["tva"] for v in self.ventes_entre(debut, fin)) - sum(r["tva"] for r in self.retours_entre(debut, fin))
        charges = sum(c["tva"] for c in self.charges_entre(debut, fin))
        achats = sum(f["tva"] for f in self.factures_entre(debut, fin)) - sum(a["tva"] for _, a in self.avoirs_entre(debut, fin))
        immobilisations = sum(_num(a.get("montant_tva")) for a in self.acquisitions_entre(debut, fin))
        deductible = charges + achats + immobilisations
        return {"collectee": collectee, "deductible_charges": charges, "deductible_achats": achats,
                "deductible_immobilisations": immobilisations, "deductible": deductible, "net": collectee - deductible}

    def premiere_date(self):
        """Date de la plus ancienne opération enregistrée."""
        dates = [v["date"] for v in self.ventes if v["date"]] + [c["date"] for c in self.charges if c["date"]]
        dates += [m["date"] for m in self.mouvements] + [f["date"] for f in self.factures if f["date"]]
        dates += [d for a in self.immobilisations if (d := _date(a.get("date_achat")))]
        dates += [o["date"] for o in self.financements if o["date"]]
        return min(dates) if dates else datetime.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    def stock_a(self, date):
        """Valeur d'achat du stock (magasin + livreurs) à la date.

        Stock d'aujourd'hui moins les mouvements postérieurs à la date. Les
        remises de stock aux livreurs ne changent pas le stock de l'entreprise :
        seules les ventes, achats, retours, pertes et corrections comptent.
        """
        from models.stock_livreur_model import StockLivreurModel

        ids = {str(p["_id"]) for p in self.produits}
        chez_livreurs = defaultdict(float)
        for s in StockLivreurModel.get_collection().find({}):
            if str(s.get("produit_id")) in ids:
                chez_livreurs[str(s.get("produit_id"))] += max(_num(s.get("quantite")), 0)
        passe = date < datetime.now()
        total = 0.0
        for p in self.produits:
            supprime = heure_locale(p.get("deleted_at"))
            if supprime and (not passe or supprime <= date):
                continue
            quantite = max(_num(p.get("quantite")), 0) + chez_livreurs[str(p["_id"])]
            if passe:
                cree = heure_locale(p.get("created_at"))
                if cree and cree > date:
                    continue
                for m in p.get("mouvements_stock") or []:
                    quand = heure_locale(m.get("date"))
                    if quand and quand > date and famille_mouvement(m.get("type"), _num(m.get("quantite")))[0] != "livreurs":
                        quantite -= _num(m.get("quantite"))
            total += max(quantite, 0) * _num(p.get("prix_achat"))
        return total


# --------------------------------------------------------------------------- compte de résultat
def _resultat(ctx, debut, fin):
    ventes = ctx.ventes_entre(debut, fin)
    retours = ctx.retours_entre(debut, fin)
    charges = ctx.charges_entre(debut, fin)
    par_categorie = defaultdict(float)
    for c in charges:
        par_categorie[c["categorie"]] += c["ht"]
    rubrique = lambda r: sum(v for k, v in par_categorie.items() if categorie_charge(k)[2] == r)
    ventes_brutes = sum(v["ht"] for v in ventes)
    retours_ht = sum(r["ht"] for r in retours)
    ca = ventes_brutes - retours_ht
    cout = sum(v["cout"] for v in ventes) - sum(r["cout"] for r in retours)
    frais_retour = sum(r["frais"] for r in retours)
    pertes = ctx.pertes_entre(debut, fin)
    externes, impots, personnel = (rubrique(r) for r in ("externes", "impots", "personnel"))
    interets_emprunts = sum(o["interets"] for o in ctx.financements_entre(debut, fin, "remboursement"))
    financieres = rubrique("financieres") + interets_emprunts
    dotations = ctx.dotations_entre(debut, fin)
    marge = ca - cout
    ebe = marge + frais_retour - pertes - externes - impots - personnel
    exploitation = ebe - dotations
    rao = exploitation - financieres
    cessions = ctx.cessions_entre(debut, fin)
    produits_hao = sum(prix for _, prix, _ in cessions)
    charges_hao = sum(vnc for _, _, vnc in cessions)
    avant_impot = rao + produits_hao - charges_hao
    impot = fiscal_service.impot_sur_resultat(avant_impot, ca, ctx.fiscal)
    tva = ctx.tva_entre(debut, fin)
    return {
        "ventes_brutes": ventes_brutes, "retours": retours_ht, "frais_retour": frais_retour,
        "ca": ca, "cout": cout, "marge": marge, "pertes": pertes, "externes": externes, "impots": impots,
        "personnel": personnel, "ebe": ebe, "dotations": dotations, "exploitation": exploitation,
        "financieres": financieres, "interets_emprunts": interets_emprunts, "rao": rao,
        "produits_hao": produits_hao, "charges_hao": charges_hao, "hao": produits_hao - charges_hao,
        "avant_impot": avant_impot, "impot": impot["du"], "impot_detail": impot, "net": avant_impot - impot["du"],
        "par_categorie": dict(par_categorie), "ventes": len(ventes),
        "tva_collectee": tva["collectee"], "tva_deductible": tva["deductible"], "tva": tva,
    }


def _charges_totales(r):
    return r["pertes"] + r["externes"] + r["impots"] + r["personnel"] + r["dotations"] + r["financieres"]


def _lignes_resultat(r, p):
    """Lignes affichables du compte de résultat (période et période précédente)."""
    lignes = []

    def ajouter(cle, libelle, compte, type_, montant, precedent, niveau=0):
        lignes.append({"cle": cle, "libelle": libelle, "compte": compte, "type": type_, "niveau": niveau,
                       "montant": _arrondi(montant), "precedent": _arrondi(precedent)})

    ajouter("ventes_brutes", "Ventes de marchandises (HT)", "701", "produit", r["ventes_brutes"], p["ventes_brutes"])
    ajouter("retours", "Retours clients de la période", "701", "charge", r["retours"], p["retours"])
    ajouter("ca", "Chiffre d'affaires net", "", "total", r["ca"], p["ca"])
    ajouter("cout", "Coût d'achat des marchandises vendues", "601/6031", "charge", r["cout"], p["cout"])
    ajouter("marge", "Marge commerciale", "", "total", r["marge"], p["marge"])
    if r["frais_retour"] or p["frais_retour"]:
        ajouter("frais_retour", "Frais de retour retenus aux clients", "7078", "produit", r["frais_retour"], p["frais_retour"])
    ajouter("pertes", "Pertes et avaries de stock", "6031", "charge", r["pertes"], p["pertes"])
    for rubrique, titre in (("externes", "Charges externes"), ("impots", "Impôts et taxes"),
                            ("personnel", "Charges de personnel")):
        ajouter(rubrique, titre, "", "charge", r[rubrique], p[rubrique])
        for cle, (libelle, compte, rub) in CATEGORIES_CHARGES.items():
            if rub == rubrique and rubrique != "impots" and (r["par_categorie"].get(cle) or p["par_categorie"].get(cle)):
                ajouter(f"cat_{cle}", libelle, compte, "detail", r["par_categorie"].get(cle, 0),
                        p["par_categorie"].get(cle, 0), 1)
    ajouter("ebe", "Excédent brut d'exploitation (EBE)", "", "total", r["ebe"], p["ebe"])
    ajouter("dotations", "Dotations aux amortissements", "681", "charge", r["dotations"], p["dotations"])
    ajouter("exploitation", "Résultat d'exploitation", "", "total", r["exploitation"], p["exploitation"])
    ajouter("financieres", "Charges financières (intérêts)", "671", "charge", r["financieres"], p["financieres"])
    ajouter("rao", "Résultat des activités ordinaires", "", "total", r["rao"], p["rao"])
    if r["produits_hao"] or p["produits_hao"] or r["charges_hao"] or p["charges_hao"]:
        ajouter("produits_hao", "Produits des cessions d'immobilisations", "822", "produit", r["produits_hao"], p["produits_hao"])
        ajouter("charges_hao", "Valeur nette des immobilisations cédées", "812", "charge", r["charges_hao"], p["charges_hao"])
    ajouter("avant_impot", "Résultat avant impôt", "", "total", r["avant_impot"], p["avant_impot"])
    ajouter("impot", "Impôt sur le résultat (estimation)", "891", "charge", r["impot"], p["impot"])
    ajouter("net", "Résultat net", "", "resultat", r["net"], p["net"])
    return lignes


# --------------------------------------------------------------------------- trésorerie
def _flux(ctx, debut, fin):
    """Entrées et sorties d'argent de la période, par nature et par moyen de paiement."""
    operations = encaissements(ctx.magasin_id, debut, fin)
    par_mode = defaultdict(lambda: {"entrees": 0.0, "sorties": 0.0})
    v = defaultdict(float)

    def mouvement(cle, mode, montant, sens):
        v[cle] += montant
        par_mode[compte_tresorerie(mode)[0]][sens] += montant

    for o in operations:
        if o["mode"] == "credit_client":
            continue
        mouvement("entrees" if o["sens"] == "entree" else "remboursements", o["mode"], o["montant"],
                  "entrees" if o["sens"] == "entree" else "sorties")
    for m in ctx.comptes_clients_entre(debut, fin):
        if m["montant"] > 0:
            mouvement("depots_clients", m["mode"], m["montant"], "entrees")
        else:
            mouvement("retraits_clients", m["mode"], -m["montant"], "sorties")
    for c in ctx.charges:
        if c["statut"] == "payee" and c["date_paiement"] and debut <= c["date_paiement"] <= fin:
            mouvement("charges", c["mode_paiement"], c["montant"], "sorties")
    mouvement("achats", "cash", ctx.achats_entre(debut, fin), "sorties")
    for _, r in ctx.reglements_fournisseurs(debut, fin):
        mouvement("fournisseurs", r.get("mode"), r["montant"], "sorties")
    for a in ctx.acquisitions_entre(debut, fin):
        mouvement("investissements", a.get("mode_paiement"), _num(a.get("valeur_achat")) + _num(a.get("montant_tva")), "sorties")
    for a, prix, _ in ctx.cessions_entre(debut, fin):
        if prix:
            mouvement("cessions", a.get("mode_paiement_cession"), prix, "entrees")
    for o in ctx.financements_entre(debut, fin):
        if o["type"] in ("apport_capital", "apport"):
            mouvement("apports", o["mode"], o["montant"], "entrees")
        elif o["type"] == "emprunt":
            mouvement("emprunts", o["mode"], o["montant"], "entrees")
        elif o["type"] == "retrait":
            mouvement("retraits_exploitant", o["mode"], o["montant"], "sorties")
        elif o["type"] == "remboursement":
            mouvement("remboursements_emprunts", o["mode"], o["montant"] + o["interets"], "sorties")
    for o in ctx.fiscales_entre(debut, fin):
        if o["type"] == "remboursement_tva":
            mouvement("remboursements_tva", o["mode"], o["montant"], "entrees")
        else:
            mouvement("impots_verses", o["mode"], o["montant"], "sorties")

    cles_entrees = ("entrees", "depots_clients", "cessions", "apports", "emprunts", "remboursements_tva")
    cles_sorties = ("remboursements", "retraits_clients", "charges", "achats", "fournisseurs", "investissements",
                    "retraits_exploitant", "remboursements_emprunts", "impots_verses")
    entrees_total = sum(v[k] for k in cles_entrees)
    sorties = sum(v[k] for k in cles_sorties)
    return {
        **{k: v[k] for k in cles_entrees + cles_sorties},
        "entrees_total": entrees_total, "sorties": sorties, "net": entrees_total - sorties, "par_compte": par_mode,
    }


LIBELLES_FLUX = {
    "entrees": "Encaissements clients", "depots_clients": "Dépôts sur les comptes clients",
    "cessions": "Ventes d'immobilisations", "apports": "Apports de l'exploitant ou des associés",
    "emprunts": "Emprunts reçus", "remboursements_tva": "Remboursements de crédit de TVA",
    "charges": "Charges payées", "achats": "Achats de marchandises au comptant",
    "fournisseurs": "Règlements des factures fournisseurs", "remboursements": "Remboursements clients",
    "investissements": "Investissements (immobilisations)", "retraits_clients": "Retraits sur les comptes clients",
    "retraits_exploitant": "Retraits de l'exploitant", "remboursements_emprunts": "Remboursements d'emprunts (capital et intérêts)",
    "impots_verses": "Impôts versés (TVA, impôt sur le résultat)",
}
FLUX_TOUJOURS_AFFICHES = {"entrees", "charges", "achats", "fournisseurs", "remboursements", "investissements"}


def encaissements(magasin_id, debut, fin):
    """Paiements et remboursements clients de la période (dates déjà locales)."""
    return rapport_encaissements(magasin_id, debut, fin)["operations"]


COMPTES_OUVERTURE = ("571", "521", "552")


def parametres(magasin_id):
    m = MagasinModel.get_collection().find_one({"_id": ObjectId(magasin_id)}) or {}
    total = _num(m.get("compta_solde_ouverture"))
    detail = m.get("compta_soldes_ouverture") or {}
    soldes = {c: _num(detail.get(c)) for c in COMPTES_OUVERTURE} if detail else {"571": total, "521": 0.0, "552": 0.0}
    return {
        "solde_ouverture": total,
        "soldes_ouverture": soldes,
        "date_ouverture": _date(m.get("compta_date_ouverture")),
        "configure": m.get("compta_date_ouverture") is not None,
    }


def enregistrer_parametres(magasin_id, solde_ouverture, date_ouverture, soldes=None):
    champs = {
        "compta_solde_ouverture": float(solde_ouverture or 0),
        "compta_date_ouverture": _date(date_ouverture) or datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
    }
    if soldes:
        detail = {c: float(soldes.get(c) or 0) for c in COMPTES_OUVERTURE}
        champs["compta_soldes_ouverture"] = detail
        champs["compta_solde_ouverture"] = sum(detail.values())
    MagasinModel.get_collection().update_one({"_id": ObjectId(magasin_id)}, {"$set": champs})
    return parametres(magasin_id)


DEBUT_HISTOIRE = datetime(2000, 1, 1)


def _solde_au(ctx_global, magasin_id, avant, reglages):
    """Trésorerie juste avant la date : solde d'ouverture + flux depuis l'ouverture."""
    depart = reglages["date_ouverture"] or DEBUT_HISTOIRE
    if avant <= depart:
        return reglages["solde_ouverture"]
    return reglages["solde_ouverture"] + _flux(ctx_global, depart, avant - timedelta(microseconds=1))["net"]


def _soldes_comptes_au(ctx, date, reglages):
    """Solde de chaque compte de trésorerie (caisse, banque, mobile money) à la date incluse."""
    soldes = dict(reglages["soldes_ouverture"])
    depart = reglages["date_ouverture"] or DEBUT_HISTOIRE
    if date >= depart:
        for compte, m in _flux(ctx, depart, date)["par_compte"].items():
            soldes[compte] = soldes.get(compte, 0.0) + m["entrees"] - m["sorties"]
    return soldes


# --------------------------------------------------------------------------- synthèse
def _periode_precedente(debut, fin):
    jours = (fin.date() - debut.date()).days + 1
    return debut - timedelta(days=jours), debut - timedelta(seconds=1)


def _fin_de_mois(annee, mois):
    return datetime(annee, mois, calendar.monthrange(annee, mois)[1], 23, 59, 59)


def _debut_comptable(ctx, reglages):
    return reglages["date_ouverture"] or ctx.premiere_date()


def _exercices(ctx, reglages, fin):
    """Résultat de chaque exercice (année civile) depuis le début, arrêté à la date."""
    depart = _debut_comptable(ctx, reglages)
    resultats = {}
    for annee in range(depart.year, fin.year + 1):
        d = max(datetime(annee, 1, 1), depart)
        f = min(datetime(annee, 12, 31, 23, 59, 59), fin)
        if f >= d:
            resultats[annee] = _resultat(ctx, d, f)
    return resultats


def _bilan(ctx, fin, reglages, soldes_tresorerie):
    """Bilan SYSCOHADA à la date (postes détaillés, rubriques et totaux)."""
    from services.tva_service import decimales_magasin

    actif, passif = [], []
    decimales = decimales_magasin(ctx.magasin_id)  # 0 pour le franc CFA : pas de centimes au bilan
    arrondi = lambda v: _arrondi(v, decimales)

    def poste(cote, rubrique, compte, libelle, montant, **extra):
        extra = {k: arrondi(v) if isinstance(v, float) else v for k, v in extra.items()}
        cote.append({"rubrique": rubrique, "compte": compte, "libelle": libelle, "montant": arrondi(montant), **extra})

    # Actif immobilisé : valeur brute, amortissements, valeur nette par nature.
    par_nature = defaultdict(lambda: [0.0, 0.0])
    for a in ctx.immobilisations:
        achat = _date(a.get("date_achat"))
        if not achat or achat > fin or AssetModel.cede_avant(a, fin):
            continue
        brut = _num(a.get("valeur_achat"))
        cle = a.get("categorie") if a.get("categorie") in COMPTES_IMMOBILISATIONS else "autre"
        par_nature[cle][0] += brut
        par_nature[cle][1] += AssetModel.amortissements_cumules(a, fin)
    if not par_nature:
        poste(actif, "immobilise", "24", "Immobilisations corporelles", 0, brut=0, amortissements=0)
    for cle, (compte, _, libelle) in COMPTES_IMMOBILISATIONS.items():
        if cle in par_nature:
            brut, amort = par_nature[cle]
            poste(actif, "immobilise", compte, libelle, arrondi(brut) - arrondi(amort), brut=brut, amortissements=amort)

    # Actif et passif circulants
    stock = ctx.stock_a(fin)
    creances = sum(v["reste"] for v in ctx.ventes if v["date"] and v["date"] <= fin)
    # Dettes d'avant Lumistock : créance dès leur saisie (la contrepartie est
    # dans la situation nette d'ouverture, calculée par différence).
    from services.compte_client_service import creances_ouverture_au
    creances += creances_ouverture_au(ctx.magasin_id, fin)
    debut = _debut_comptable(ctx, reglages)
    tva = ctx.tva_entre(debut, fin)
    tva_versee = sum(o["montant"] for o in ctx.fiscales_entre(debut, fin, "tva"))
    tva_remboursee = sum(o["montant"] for o in ctx.fiscales_entre(debut, fin, "remboursement_tva"))
    position_tva = tva["net"] - tva_versee + tva_remboursee  # + dette, − crédit

    exercices = _exercices(ctx, reglages, fin)
    position_is = 0.0
    for annee, r in exercices.items():
        verse = sum(o["montant"] for o in ctx.fiscales_entre(debut, fin, "is") if o["periode"] == str(annee))
        position_is += r["impot"] - verse
    creances_fiscales = max(-position_tva, 0) + max(-position_is, 0)

    poste(actif, "circulant", "31", "Stock de marchandises (valeur d'achat)", stock)
    poste(actif, "circulant", "409", "Fournisseurs débiteurs (avoirs à rembourser)", ctx.creances_fournisseurs(fin))
    poste(actif, "circulant", "411", "Clients (reste à encaisser)", creances)
    poste(actif, "circulant", "4449", "État, crédit de TVA et impôt versé en trop", creances_fiscales)
    for compte in ("521", "552", "571"):
        poste(actif, "tresorerie", compte, LIBELLES_TRESORERIE[compte], max(soldes_tresorerie.get(compte, 0.0), 0))

    # Passif
    passe = fin < datetime.now()
    avances = ctx.avances_clients - (sum(m["montant"] for m in ctx.comptes_clients if m["date"] > fin) if passe else 0)
    dues = ctx.charges_dues(fin)
    dette = lambda compte: sum(c["montant"] for c in dues if compte_dette(c["categorie"]) == compte)
    emprunts = 0.0
    for e in (o for o in ctx.financements if o["type"] == "emprunt" and o["date"] and o["date"] <= fin):
        rembourse = sum(o["montant"] for o in ctx.financements_entre(e["date"], fin, "remboursement") if o["emprunt_id"] == e["id"])
        emprunts += max(e["montant"] - rembourse, 0)
    origine = DEBUT_HISTOIRE
    capital = ctx.fiscal["capital_social"] + sum(o["montant"] for o in ctx.financements_entre(origine, fin, "apport_capital"))
    exploitant = (sum(o["montant"] for o in ctx.financements_entre(origine, fin, "apport"))
                  - sum(o["montant"] for o in ctx.financements_entre(origine, fin, "retrait")))
    resultat = exercices.get(fin.year, {}).get("net", 0.0)
    report = sum(r["net"] for annee, r in exercices.items() if annee < fin.year)
    decouverts = sum(-s for s in soldes_tresorerie.values() if s < 0)

    total_actif = sum(p["montant"] for p in actif)
    poste(passif, "capitaux", "101", "Capital social / apport initial", capital)
    poste(passif, "capitaux", "108", "Compte de l'exploitant (apports − retraits)", exploitant)
    poste(passif, "capitaux", "121", "Report à nouveau (résultats des années précédentes)", report)
    poste(passif, "capitaux", "131", f"Résultat net de l'exercice {fin.year}", resultat)
    poste(passif, "financieres", "162", "Emprunts et dettes financières", emprunts)
    poste(passif, "circulant", "4191", "Clients, avances reçues (comptes clients)", max(avances, 0))
    poste(passif, "circulant", "401", "Fournisseurs (factures et charges à payer)", ctx.dettes_factures(fin) + dette("401"))
    poste(passif, "circulant", "422", "Personnel, salaires à payer", dette("422"))
    poste(passif, "circulant", "431", "Organismes sociaux (CNSS…) à payer", dette("431"))
    poste(passif, "circulant", "4441", "État, TVA due", max(position_tva, 0))
    poste(passif, "circulant", "441", "État, impôt sur le résultat à payer", max(position_is, 0))
    poste(passif, "circulant", "447", "État, autres impôts et taxes à payer", dette("447"))
    poste(passif, "tresorerie", "565", "Banques, découverts et soldes négatifs", decouverts)
    # Ce qui n'est pas saisi dans l'application (argent et stock de départ,
    # apports anciens) : situation nette d'ouverture, calculée par différence.
    ouverture = total_actif - sum(p["montant"] for p in passif)
    passif.insert(1, {"rubrique": "capitaux", "compte": "109", "libelle": "Situation nette d'ouverture (calculée par différence)",
                      "montant": arrondi(ouverture), "calcule": True})

    def rubriques(postes, libelles):
        return [{"cle": cle, "libelle": libelle, "total": _arrondi(sum(p["montant"] for p in postes if p["rubrique"] == cle))}
                for cle, libelle in libelles]

    capitaux = sum(p["montant"] for p in passif if p["rubrique"] == "capitaux")
    dettes = sum(p["montant"] for p in passif if p["rubrique"] in ("financieres", "circulant"))
    return {
        "actif": actif,
        "passif": passif,
        "rubriques": {
            "actif": rubriques(actif, (("immobilise", "Actif immobilisé"), ("circulant", "Actif circulant"),
                                       ("tresorerie", "Trésorerie – actif"))),
            "passif": rubriques(passif, (("capitaux", "Capitaux propres"), ("financieres", "Dettes financières"),
                                         ("circulant", "Passif circulant"), ("tresorerie", "Trésorerie – passif"))),
        },
        "total": _arrondi(total_actif),
        "total_actif": _arrondi(total_actif),
        "total_passif": _arrondi(sum(p["montant"] for p in passif)),
        "ouverture": _arrondi(ouverture),
        "ratios": {
            "fonds_roulement": _arrondi(capitaux + emprunts - sum(p["montant"] for p in actif if p["rubrique"] == "immobilise")),
            "liquidite": _arrondi((sum(p["montant"] for p in actif if p["rubrique"] != "immobilise")) / dettes, 2) if dettes else None,
            "endettement": _arrondi(emprunts / capitaux * 100, 1) if capitaux > 0 else None,
        },
        "position_tva": _arrondi(position_tva),
        "position_is": _arrondi(position_is),
    }


def synthese(magasin_id, debut, fin):
    """Toutes les données de la page Finance pour la période."""
    reglages = parametres(magasin_id)
    p_debut, p_fin = _periode_precedente(debut, fin)
    ctx = _Contexte(magasin_id, DEBUT_HISTOIRE, max(fin, _fin_de_mois(fin.year, 12)))

    r, p = _resultat(ctx, debut, fin), _resultat(ctx, p_debut, p_fin)
    jours = (fin.date() - debut.date()).days + 1
    charges_fixes = r["externes"] + r["impots"] + r["personnel"] + r["dotations"] + r["financieres"]
    taux_marge = r["marge"] / r["ca"] if r["ca"] else 0
    seuil = charges_fixes / taux_marge if taux_marge > 0 else None

    evolution = []
    for mois in range(1, 13):
        d, f = datetime(fin.year, mois, 1), _fin_de_mois(fin.year, mois)
        if d > datetime.now():
            break
        m = _resultat(ctx, d, f)
        evolution.append({"mois": mois, "ca": _arrondi(m["ca"]), "marge": _arrondi(m["marge"]),
                          "charges": _arrondi(_charges_totales(m) + m["impot"]), "resultat": _arrondi(m["net"])})

    flux = _flux(ctx, debut, fin)
    solde_debut = _solde_au(ctx, magasin_id, debut, reglages)
    solde_fin = solde_debut + flux["net"]
    soldes = _soldes_comptes_au(ctx, fin, reglages)
    bilan = _bilan(ctx, fin, reglages, soldes)

    a_payer = ctx.charges_dues(fin)
    dettes_charges = sum(c["montant"] for c in a_payer)
    dettes_factures = ctx.dettes_factures(fin)
    maintenant = datetime.now()
    en_retard = [c for c in a_payer if c["date_echeance"] and c["date_echeance"] < maintenant]
    factures_ouvertes = [f for f in ctx.factures
                         if f["ttc"] - sum(a["ttc"] for a in f["avoirs"]) - sum(r_["montant"] for r_ in f["paiements"]) > 0.004]
    a = lambda v: _arrondi(v)
    from services.tva_service import decimales_magasin
    _dec = decimales_magasin(magasin_id)
    ad = lambda v: _arrondi(v, _dec)
    ligne_flux = lambda cle: {"cle": cle, "libelle": LIBELLES_FLUX[cle], "montant": a(flux[cle])}
    fiscal = ctx.fiscal
    return {
        "periode": {"debut": debut.isoformat(), "fin": fin.isoformat(), "jours": jours},
        "precedente": {"debut": p_debut.isoformat(), "fin": p_fin.isoformat()},
        "compte_resultat": _lignes_resultat(r, p),
        "indicateurs": {
            "chiffre_affaires": a(r["ca"]), "chiffre_affaires_precedent": a(p["ca"]),
            "marge": a(r["marge"]), "marge_precedente": a(p["marge"]),
            "taux_marge": _arrondi(taux_marge * 100, 1),
            "ebe": a(r["ebe"]), "resultat_net": a(r["net"]), "resultat_net_precedent": a(p["net"]),
            "resultat_avant_impot": a(r["avant_impot"]), "impot_resultat": a(r["impot"]),
            "taux_resultat": _arrondi(r["net"] / r["ca"] * 100, 1) if r["ca"] else 0,
            "charges": a(r["pertes"] + charges_fixes), "charges_precedentes": a(_charges_totales(p)),
            "charges_fixes": a(charges_fixes),
            "seuil_rentabilite": a(seuil) if seuil is not None else None,
            "point_mort_jours": _arrondi(seuil / r["ca"] * jours, 0) if seuil is not None and r["ca"] else None,
            "ventes": r["ventes"],
        },
        "evolution": {"annee": fin.year, "mois": evolution},
        "tresorerie": {
            "solde_debut": a(solde_debut), "solde_fin": a(solde_fin), "configure": reglages["configure"],
            "solde_ouverture": a(reglages["solde_ouverture"]),
            "soldes_ouverture": {c: a(v) for c, v in reglages["soldes_ouverture"].items()},
            "date_ouverture": reglages["date_ouverture"].isoformat() if reglages["date_ouverture"] else None,
            "entrees": [ligne_flux(k) for k in ("entrees", "depots_clients", "cessions", "apports", "emprunts", "remboursements_tva")
                        if k in FLUX_TOUJOURS_AFFICHES or flux[k]],
            "sorties": [ligne_flux(k) for k in ("charges", "achats", "fournisseurs", "remboursements", "investissements",
                                                "retraits_clients", "retraits_exploitant", "remboursements_emprunts", "impots_verses")
                        if k in FLUX_TOUJOURS_AFFICHES or flux[k]],
            "total_entrees": a(flux["entrees_total"]), "total_sorties": a(flux["sorties"]), "net": a(flux["net"]),
            "par_compte": [
                {"compte": c, "libelle": LIBELLES_COMPTES[c], "entrees": a(v["entrees"]), "sorties": a(v["sorties"]),
                 "net": a(v["entrees"] - v["sorties"])}
                for c, v in sorted(flux["par_compte"].items()) if v["entrees"] or v["sorties"]
            ],
            "soldes_fin": [{"compte": c, "libelle": LIBELLES_TRESORERIE[c], "solde": a(soldes.get(c, 0))} for c in ("571", "521", "552")],
        },
        "tva": {
            "collectee": ad(r["tva"]["collectee"]), "deductible": ad(r["tva"]["deductible"]),
            "deductible_charges": ad(r["tva"]["deductible_charges"]), "deductible_achats": ad(r["tva"]["deductible_achats"]),
            "deductible_immobilisations": ad(r["tva"]["deductible_immobilisations"]),
            "a_reverser": ad(r["tva"]["net"]),
            "taux": fiscal["taux_tva"], "assujetti": fiscal["assujetti_tva"],
        },
        "impot": {
            "regime": fiscal["regime"], "taux_is": fiscal["taux_is"], "taux_minimum": fiscal["taux_minimum"],
            "resultat_avant_impot": ad(r["avant_impot"]), "theorique": ad(r["impot_detail"]["theorique"]),
            "minimum": ad(r["impot_detail"]["minimum"]), "du": ad(r["impot"]),
        },
        "bilan": bilan,
        "a_payer": {
            "nombre": len(a_payer), "montant": a(dettes_charges),
            "factures": len(factures_ouvertes), "montant_factures": a(dettes_factures),
            "en_retard": len(en_retard), "montant_en_retard": a(sum(c["montant"] for c in en_retard)),
        },
        "creances": a(sum(v["reste"] for v in ctx.ventes if v["date"] and v["date"] <= fin)),
    }


# --------------------------------------------------------------------------- impôts
ECHEANCE_JOUR = 15  # dépôt de la déclaration le 15 du mois suivant (indicatif)


def situation_fiscale(magasin_id, annee, params):
    """TVA mois par mois (avec report de crédit), acomptes et impôt sur le résultat de l'année."""
    from services.tva_service import decimales_magasin

    decimales = decimales_magasin(magasin_id)
    ar = lambda v: _arrondi(v, decimales)  # montants à l'unité de la monnaie (FCFA sans centimes)
    reglages = parametres(magasin_id)
    fin_annee = _fin_de_mois(annee, 12)
    ctx = _Contexte(magasin_id, DEBUT_HISTOIRE, fin_annee)
    depart = _debut_comptable(ctx, reglages)
    maintenant = datetime.now()
    ops = ctx.operations_fiscales
    somme = lambda type_, periode: sum(o["montant"] for o in ops if o["type"] == type_ and o["periode"] == periode)

    credit, lignes = 0.0, []
    curseur = min(datetime(depart.year, depart.month, 1), datetime(annee, 1, 1))
    while curseur <= fin_annee:
        fin_mois = _fin_de_mois(curseur.year, curseur.month)
        t = ctx.tva_entre(max(curseur, depart), fin_mois)  # vide avant le début de la comptabilité
        periode = f"{curseur:%Y-%m}"
        report = credit
        net = t["net"] - report
        a_payer, credit_suivant = max(net, 0.0), max(-net, 0.0)
        rembourse = somme("remboursement_tva", periode)
        credit = max(credit_suivant - rembourse, 0.0)
        if curseur.year == annee:
            paye = somme("tva", periode)
            echeance = (fin_mois + timedelta(days=1)).replace(day=ECHEANCE_JOUR, hour=23, minute=59, second=59)
            reste = max(a_payer - paye, 0.0)
            if curseur > maintenant:
                statut = "a_venir"
            elif fin_mois > maintenant:
                statut = "en_cours"
            elif reste > 0.5:
                statut = "en_retard" if maintenant > echeance else "a_payer"
            elif a_payer > 0:
                statut = "paye"
            else:
                statut = "credit" if credit_suivant > 0 else "neant"
            ca = _resultat(ctx, curseur, fin_mois)["ca"] if curseur <= maintenant else 0.0
            lignes.append({
                "mois": curseur.month, "periode": periode, "echeance": echeance.isoformat(), "statut": statut,
                "collectee": ar(t["collectee"]), "deductible": ar(t["deductible"]),
                "deductible_charges": ar(t["deductible_charges"]), "deductible_achats": ar(t["deductible_achats"]),
                "deductible_immobilisations": ar(t["deductible_immobilisations"]),
                "credit_reporte": ar(report), "a_payer": ar(a_payer), "paye": ar(paye),
                "reste": ar(reste), "credit_suivant": ar(credit_suivant), "rembourse": ar(rembourse),
                "chiffre_affaires": ar(ca),
                "acompte_is": ar(ca * params["taux_minimum"] / 100) if params["regime"] != "forfait" else 0,
            })
        curseur = fin_mois + timedelta(seconds=1)

    d = max(datetime(annee, 1, 1), depart)
    r = _resultat(ctx, d, min(fin_annee, max(maintenant, d)))
    verse_is = somme("is", str(annee))
    autres = [c for c in ctx.charges if c["categorie"] == "impots" and c["date"] and c["date"].year == annee]
    total = lambda cle: ar(sum(l[cle] for l in lignes))
    return {
        "annee": annee,
        "parametres": params,
        "mois": lignes,
        "totaux": {"collectee": total("collectee"), "deductible": total("deductible"), "a_payer": total("a_payer"),
                   "paye": total("paye"), "reste": total("reste"), "credit_fin": ar(credit),
                   "acomptes_is": total("acompte_is")},
        "impot_resultat": {
            "exercice": annee, "chiffre_affaires": ar(r["ca"]), "resultat_avant_impot": ar(r["avant_impot"]),
            "theorique": ar(r["impot_detail"]["theorique"]), "minimum": ar(r["impot_detail"]["minimum"]),
            "du": ar(r["impot"]), "verse": ar(verse_is), "reste": ar(r["impot"] - verse_is),
            "base": "minimum" if r["impot_detail"]["minimum"] > r["impot_detail"]["theorique"] else "benefice",
            "provisoire": annee >= maintenant.year,
        },
        "autres_taxes": {
            "total": ar(sum(c["montant"] for c in autres)),
            "a_payer": ar(sum(c["montant"] for c in autres if c["statut"] != "payee")),
            "lignes": [{"id": c["id"], "nom": c["nom"], "montant": ar(c["montant"]), "date": c["date"].isoformat(),
                        "statut": c["statut"]} for c in sorted(autres, key=lambda c: c["date"])],
        },
        "operations": [fiscal_service.operation_json({"_id": o["id"], "type": o["type"], "periode": o["periode"],
                                                      "montant": o["montant"], "date_operation": o["date"],
                                                      "mode_paiement": o["mode"], "reference": o["reference"]})
                       for o in sorted(ops, key=lambda o: o["date"] or datetime.min, reverse=True)
                       if o["periode"][:4] == str(annee)],
        "annees": sorted({depart.year, maintenant.year, annee}),
    }


# --------------------------------------------------------------------------- journal
def journal(magasin_id, debut, fin):
    """Écritures en partie double de la période, prêtes pour l'expert-comptable."""
    ctx = _Contexte(magasin_id, debut, fin)
    ecritures = []

    def ecrire(date, code, piece, libelle, lignes, ref=None):
        lignes = [l for l in lignes if round(l[1], 2) or round(l[2], 2)]
        if lignes:
            ecritures.append({
                "date": date.isoformat(), "journal": code, "piece": piece, "libelle": libelle, "ref": ref,
                "lignes": [{"compte": c, "intitule": LIBELLES_COMPTES.get(c, c), "debit": _arrondi(d), "credit": _arrondi(cr)}
                           for c, d, cr in lignes],
            })

    for v in ctx.ventes_entre(debut, fin):
        piece = facture(v["id"])
        ecrire(v["date"], "VT", piece, f"Vente {piece}",
               [("411", v["ht"] + v["tva"], 0), ("701", 0, v["ht"]), ("4431", 0, v["tva"])], f"vente:{v['id']}")
        if v["deduit"]:
            ecrire(v["date"], "OD", piece, f"Règlement {piece} par le compte client",
                   [("4191", v["deduit"], 0), ("411", 0, v["deduit"])], f"vente:{v['id']}:compte")

    for r in ctx.retours_entre(debut, fin):
        piece = facture(r["vente_id"])
        ecrire(r["date"], "VT", piece, f"Retour client {piece}",
               [("701", r["ht"], 0), ("4431", r["tva"], 0), ("411", 0, r["ht"] + r["tva"])], f"retour:{r['id']}")
        if r["frais"]:
            ecrire(r["date"], "VT", piece, f"Frais de retour retenus {piece}",
                   [("411", r["frais"], 0), ("7078", 0, r["frais"])], f"retour:{r['id']}:frais")

    for o in encaissements(magasin_id, debut, fin):
        quand = _date(o["date"])
        ref = f"{'enc' if o['sens'] == 'entree' else 'remb'}:{o['vente_id']}:{o['date']}:{o['montant']}"
        if o["mode"] == "credit_client":
            if o["sens"] == "entree":
                ecrire(quand, "OD", o["facture"], f"Règlement {o['facture']} par le compte client – {o['client']}",
                       [("4191", o["montant"], 0), ("411", 0, o["montant"])], ref)
            else:
                ecrire(quand, "OD", o["facture"], f"Avoir crédité au compte client {o['facture']} – {o['client']}",
                       [("411", o["montant"], 0), ("4191", 0, o["montant"])], ref)
            continue
        compte, code = compte_tresorerie(o["mode"])
        if o["sens"] == "entree":
            surplus = min(_num(o.get("surplus")), o["montant"])
            ecrire(quand, code, o["facture"], f"Règlement {o['facture']} – {o['client']}",
                   [(compte, o["montant"], 0), ("411", 0, o["montant"] - surplus), ("4191", 0, surplus)], ref)
        else:
            ecrire(quand, code, o["facture"], f"Remboursement {o['facture']} – {o['client']}",
                   [("411", o["montant"], 0), (compte, 0, o["montant"])], ref)

    # Dettes d'avant Lumistock : créance client ouverte contre la situation
    # nette d'ouverture, à la date de leur saisie.
    for d in db.tables.dettes_ouverture.find({"magasin_id": ObjectId(magasin_id), "deleted_at": {"$exists": False}}):
        quand = heure_locale(d.get("created_at"))
        if quand and debut <= quand <= fin and _num(d.get("montant")):
            from models.client_model import ClientModel
            client = ClientModel.get_client_by_id(d.get("client_id")) or {}
            ecrire(quand, "OD", "Dette d'ouverture", f"Dette d'avant Lumistock – {client.get('nom') or 'Client'}",
                   [("411", _num(d.get("montant")), 0), ("109", 0, _num(d.get("montant")))], f"ouverture:{d['_id']}")

    for m in ctx.comptes_clients_entre(debut, fin):
        compte, code = compte_tresorerie(m["mode"])
        if m["montant"] > 0:
            ecrire(m["date"], code, "", f"Dépôt sur le compte client – {m['client']}",
                   [(compte, m["montant"], 0), ("4191", 0, m["montant"])], m["ref"])
        else:
            ecrire(m["date"], code, "", f"Retrait du compte client – {m['client']}",
                   [("4191", -m["montant"], 0), (compte, 0, -m["montant"])], m["ref"])

    for c in ctx.charges:
        piece = c["reference"] or f"CH-{c['id'][-6:].upper()}"
        compte_charge = categorie_charge(c["categorie"])[1]
        dette = compte_dette(c["categorie"])
        libelle = c["nom"] + (f" – {c['fournisseur']}" if c["fournisseur"] else "")
        if c["date"] and debut <= c["date"] <= fin:
            ecrire(c["date"], "AC", piece, libelle,
                   [(compte_charge, c["ht"], 0), ("4452", c["tva"], 0), (dette, 0, c["montant"])], f"charge:{c['id']}")
        if c["date_paiement"] and debut <= c["date_paiement"] <= fin:
            compte, code = compte_tresorerie(c["mode_paiement"])
            ecrire(c["date_paiement"], code, piece, f"Paiement {libelle}",
                   [(dette, c["montant"], 0), (compte, 0, c["montant"])], f"charge:{c['id']}:paiement")

    for m in ctx.achats_comptant(debut, fin):
        valeur, produit = m["valeur"], m["produit"]
        if not round(valeur, 2):
            continue
        if valeur > 0:
            ecrire(m["date"], "CA", "", f"Achat marchandises – {produit}", [("601", valeur, 0), ("571", 0, valeur)], m["ref"])
        else:
            ecrire(m["date"], "CA", "", f"Annulation achat – {produit}", [("571", -valeur, 0), ("601", 0, -valeur)], m["ref"])

    for f in ctx.factures_entre(debut, fin):
        piece = f["numero"] or f"FF-{f['id'][-6:].upper()}"
        ecrire(f["date"], "AC", piece, f"Facture {piece} – {f['fournisseur']}",
               [("601", f["ht"], 0), ("4452", f["tva"], 0), ("401", 0, f["ttc"])], f"facture:{f['id']}")
    for f, r in ctx.reglements_fournisseurs(debut, fin):
        piece = f["numero"] or f"FF-{f['id'][-6:].upper()}"
        compte, code = compte_tresorerie(r.get("mode"))
        if r["montant"] >= 0:
            ecrire(r["date"], code, piece, f"Règlement facture {piece} – {f['fournisseur']}",
                   [("401", r["montant"], 0), (compte, 0, r["montant"])], f"facture:{f['id']}:{r.get('id')}")
        else:  # remboursement reçu du fournisseur après un avoir
            ecrire(r["date"], code, piece, f"Remboursement fournisseur {piece} – {f['fournisseur']}",
                   [(compte, -r["montant"], 0), ("401", 0, -r["montant"])], f"facture:{f['id']}:{r.get('id')}")
    for f, a in ctx.avoirs_entre(debut, fin):
        piece = f["numero"] or f"FF-{f['id'][-6:].upper()}"
        ecrire(a["date"], "AC", piece, f"Avoir sur facture {piece} – {f['fournisseur']}",
               [("401", a["ttc"], 0), ("601", 0, a["ht"]), ("4452", 0, a["tva"])], f"facture:{f['id']}:avoir:{a.get('id')}")

    for a in ctx.immobilisations:
        actif, amort, _ = comptes_immobilisation(a.get("categorie"))
        date_achat = _date(a.get("date_achat"))
        valeur, tva = _num(a.get("valeur_achat")), _num(a.get("montant_tva"))
        if date_achat and debut <= date_achat <= fin:
            compte, code = compte_tresorerie(a.get("mode_paiement"))
            ecrire(date_achat, code, a.get("reference") or "", f"Acquisition {a.get('nom')}",
                   [(actif, valeur, 0), ("4451", tva, 0), (compte, 0, valeur + tva)], f"immo:{a['_id']}")
        # Une dotation par mois, passée en fin de mois (ou à la fin de la période).
        curseur = datetime(debut.year, debut.month, 1)
        while curseur <= fin:
            fin_mois = _fin_de_mois(curseur.year, curseur.month)
            d, f = max(curseur, debut), min(fin_mois, fin)
            dotation = AssetModel.amortissement_entre(a, d, f)
            if dotation > 0.005:
                ecrire(f, "OD", "", f"Dotation amortissement {a.get('nom')} {curseur.strftime('%m/%Y')}",
                       [("681", dotation, 0), (amort, 0, dotation)])
            curseur = fin_mois + timedelta(seconds=1)
    for a, prix, vnc in ctx.cessions_entre(debut, fin):
        actif, amort, _ = comptes_immobilisation(a.get("categorie"))
        quand, brut = AssetModel.date_cession(a), _num(a.get("valeur_achat"))
        if prix:
            compte, code = compte_tresorerie(a.get("mode_paiement_cession"))
            ecrire(quand, code, "", f"Cession {a.get('nom')}", [(compte, prix, 0), ("822", 0, prix)], f"cession:{a['_id']}")
        ecrire(quand, "OD", "", f"Sortie de l'actif {a.get('nom')}",
               [(amort, brut - vnc, 0), ("812", vnc, 0), (actif, 0, brut)], f"cession:{a['_id']}:sortie")

    for o in ctx.financements_entre(debut, fin):
        compte, code = compte_tresorerie(o["mode"])
        libelle = o["libelle"] + (f" – {o['tiers']}" if o["tiers"] else "")
        if o["type"] == "apport_capital":
            lignes = [(compte, o["montant"], 0), ("101", 0, o["montant"])]
        elif o["type"] == "apport":
            lignes = [(compte, o["montant"], 0), ("108", 0, o["montant"])]
        elif o["type"] == "retrait":
            lignes = [("108", o["montant"], 0), (compte, 0, o["montant"])]
        elif o["type"] == "emprunt":
            lignes = [(compte, o["montant"], 0), ("162", 0, o["montant"])]
        else:
            lignes = [("162", o["montant"], 0), ("671", o["interets"], 0), (compte, 0, o["montant"] + o["interets"])]
        ecrire(o["date"], code, "", libelle, lignes, f"financement:{o['id']}")

    for o in ctx.fiscales_entre(debut, fin):
        compte, code = compte_tresorerie(o["mode"])
        if o["type"] == "tva":
            ecrire(o["date"], code, o["reference"], f"Reversement TVA {o['periode']}",
                   [("4441", o["montant"], 0), (compte, 0, o["montant"])], f"fiscal:{o['id']}")
        elif o["type"] == "remboursement_tva":
            ecrire(o["date"], code, o["reference"], f"Remboursement crédit de TVA {o['periode']}",
                   [(compte, o["montant"], 0), ("4449", 0, o["montant"])], f"fiscal:{o['id']}")
        else:
            ecrire(o["date"], code, o["reference"], f"Impôt sur le résultat {o['periode']}",
                   [("441", o["montant"], 0), (compte, 0, o["montant"])], f"fiscal:{o['id']}")

    # Fin de période : variation des stocks (achats non vendus) et impôt estimé,
    # pour que le journal donne le même résultat que le compte de résultat.
    r = _resultat(ctx, debut, fin)
    achats = (ctx.achats_entre(debut, fin) + sum(f["ht"] for f in ctx.factures_entre(debut, fin))
              - sum(a["ht"] for _, a in ctx.avoirs_entre(debut, fin)))
    variation = achats - r["cout"] - r["pertes"]
    if variation >= 0:
        ecrire(fin, "OD", "", "Variation des stocks de marchandises", [("31", variation, 0), ("6031", 0, variation)], "stock")
    else:
        ecrire(fin, "OD", "", "Variation des stocks de marchandises", [("6031", -variation, 0), ("31", 0, -variation)], "stock")
    if r["impot"]:
        ecrire(fin, "OD", "", "Impôt sur le résultat de la période (estimation)",
               [("891", r["impot"], 0), ("441", 0, r["impot"])], "impot")

    ecritures.sort(key=lambda e: (e["date"], e["journal"]))
    total_debit = sum(l["debit"] for e in ecritures for l in e["lignes"])
    total_credit = sum(l["credit"] for e in ecritures for l in e["lignes"])
    balance = defaultdict(lambda: [0.0, 0.0])
    for e in ecritures:
        for l in e["lignes"]:
            balance[l["compte"]][0] += l["debit"]
            balance[l["compte"]][1] += l["credit"]
    # Classe 6 et 81/83/85/87/89 : charges ; classe 7 et 82/84/86/88 : produits.
    est_charge = lambda c: c[0] == "6" or (c[0] == "8" and c[1:2] in "13579")
    est_produit = lambda c: c[0] == "7" or (c[0] == "8" and c[1:2] in "2468")
    produits = sum(cr - d for c, (d, cr) in balance.items() if est_produit(c))
    charges = sum(d - cr for c, (d, cr) in balance.items() if est_charge(c))
    return {
        "periode": {"debut": debut.isoformat(), "fin": fin.isoformat()},
        "ecritures": ecritures,
        "totaux": {"debit": _arrondi(total_debit), "credit": _arrondi(total_credit), "ecritures": len(ecritures),
                   "resultat": _arrondi(produits - charges)},
        "balance": [
            {"compte": c, "intitule": LIBELLES_COMPTES.get(c, c), "debit": _arrondi(d), "credit": _arrondi(cr),
             "solde": _arrondi(d - cr)}
            for c, (d, cr) in sorted(balance.items())
        ],
    }


# --------------------------------------------------------------------------- charges récurrentes
def _ajouter_mois(date, mois):
    total = date.month - 1 + mois
    annee, mois = date.year + total // 12, total % 12 + 1
    return date.replace(year=annee, month=mois, day=min(date.day, calendar.monthrange(annee, mois)[1]))


PAS_RECURRENCE = {"mensuelle": 1, "trimestrielle": 3, "annuelle": 12}


def generer_charges_recurrentes(magasin_id, annee, mois):
    """Crée, à payer, les charges récurrentes dues pour le mois indiqué.

    Une série est identifiée par la charge d'origine ; une charge déjà
    présente dans le mois pour la série n'est jamais recréée.
    """
    series = defaultdict(list)
    for c in ChargeModel.get_all_by_magasin(magasin_id):
        if (c.get("recurrence") or "aucune") in PAS_RECURRENCE:
            series[str(c.get("recurrence_source") or c["_id"])].append(c)
    creees = []
    for source, charges in series.items():
        origine = next((c for c in charges if str(c["_id"]) == source), None) or charges[0]
        date_origine = _date(origine.get("date_charge"))
        if not date_origine:
            continue
        pas = PAS_RECURRENCE[origine.get("recurrence")]
        ecart = (annee - date_origine.year) * 12 + (mois - date_origine.month)
        if ecart <= 0 or ecart % pas:
            continue
        if any((_date(c.get("date_charge")) or datetime.min).strftime("%Y-%m") == f"{annee:04d}-{mois:02d}" for c in charges):
            continue
        derniere = max(charges, key=lambda c: _date(c.get("date_charge")) or datetime.min)
        date_charge = _ajouter_mois(date_origine, ecart)
        charge_id = ChargeModel.create_charge({
            "nom": derniere.get("nom"), "montant": derniere.get("montant"), "montant_tva": derniere.get("montant_tva") or 0,
            "categorie": derniere.get("categorie"), "magasin_id": str(magasin_id), "date_charge": date_charge,
            "description": derniere.get("description") or "", "fournisseur": derniere.get("fournisseur") or "",
            "statut": "a_payer", "date_echeance": date_charge, "mode_paiement": derniere.get("mode_paiement"),
            "recurrence": origine.get("recurrence"), "recurrence_source": source,
        })
        creees.append(charge_id)
    return creees


# --------------------------------------------------------------------------- compatibilité
def resume_global(magasin_id):
    """Résumé depuis le début (ancienne route /managers/stats/profit-perte)."""
    fin = datetime.now()
    ctx = _Contexte(magasin_id, DEBUT_HISTOIRE, fin)
    r = _resultat(ctx, DEBUT_HISTOIRE, fin)
    charges = _charges_totales(r)
    return {
        "chiffre_affaires": _arrondi(r["ca"]),
        "cout_achat_total": _arrondi(r["cout"]),
        "benefice_brut": _arrondi(r["marge"]),
        "total_charges": _arrondi(charges),
        "benefice_net": _arrondi(r["net"]),
        "marge_brute_percent": _arrondi(r["marge"] / r["ca"] * 100, 2) if r["ca"] else 0,
    }
