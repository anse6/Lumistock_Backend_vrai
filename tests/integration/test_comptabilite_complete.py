"""Comptabilité complète : TVA déclarée et reversée, impôt sur le résultat, immobilisations
(TVA, valeur résiduelle automatique, cession), comptes clients, capital, emprunts,
bilan SYSCOHADA équilibré et journal qui retrouve le résultat du compte de résultat."""
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from tests.integration.test_livraisons_magasin import _h, _produit, magasins  # noqa: F401

AUJOURD_HUI = datetime.now()
MOIS = f"{AUJOURD_HUI:%Y-%m}"
PERIODE = f"debut={MOIS}-01&fin={AUJOURD_HUI:%Y-%m-%d}"


@pytest.fixture(autouse=True)
def _tables_vides(app):
    with app.app_context():
        for table in ("charges", "assets", "financements", "operations_fiscales", "factures_fournisseurs"):
            db.tables[table].delete_many({})


def _cameroun(app, m):
    with app.app_context():
        db.tables.magasin.update_one({"_id": ObjectId(m["magasin"])}, {"$set": {"pays": "Cameroun", "country_code": "CM"}})


def _get(client, m, url):
    r = client.get(url, headers=_h(m, "admin"))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _post(client, m, url, donnees, attendu=(200, 201)):
    r = client.post(url, json=donnees, headers=_h(m, "admin"))
    assert r.status_code in attendu, r.get_json()
    return r.get_json()


def _vente_tva(client, m, produit_id):
    """10 × 300 HT avec TVA camerounaise (19,25 %) : 3 000 HT + 578 de TVA, payée en espèces."""
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": produit_id, "quantite": 10, "prix": 300}], "statut_paiement": "payé",
        "montant_paye": 3578, "client_id": m["client"], "appliquer_tva": True,
        "paiements": [{"methode": "cash", "montant": 3578}],
    }, headers=_h(m, "manager"))
    assert r.status_code == 201, r.get_json()


def _par_compte(lignes):
    return {l["compte"]: l for l in lignes}


def test_tva_declaree_puis_reversee(client, app, magasins):
    a = magasins["A"]
    _cameroun(app, a)
    _vente_tva(client, a, _produit(client, a))
    _post(client, a, "/charges/", {"nom": "Internet", "montant": 1193, "montant_tva": 193, "categorie": "telecom"})

    impots = _get(client, a, f"/compta/impots?annee={AUJOURD_HUI.year}")
    assert impots["parametres"]["taux_tva"] == 19.25 and impots["parametres"]["taux_is"] == 33.0
    mois = next(l for l in impots["mois"] if l["periode"] == MOIS)
    assert (mois["collectee"], mois["deductible"], mois["a_payer"]) == (578, 193, 385)
    assert mois["statut"] == "en_cours" and mois["acompte_is"] == 66  # 2,2 % de 3 000

    bilan = _par_compte(_get(client, a, f"/compta/synthese?{PERIODE}")["bilan"]["passif"])
    assert bilan["4441"]["montant"] == 385

    paiement = _post(client, a, "/compta/impots/paiements", {"type": "tva", "periode": MOIS, "montant": 385,
                                                             "mode_paiement": "banque"})
    mois = next(l for l in _get(client, a, f"/compta/impots?annee={AUJOURD_HUI.year}")["mois"] if l["periode"] == MOIS)
    assert mois["paye"] == 385 and mois["reste"] == 0
    d = _get(client, a, f"/compta/synthese?{PERIODE}")
    assert _par_compte(d["bilan"]["passif"])["4441"]["montant"] == 0
    assert {s["cle"]: s["montant"] for s in d["tresorerie"]["sorties"]}["impots_verses"] == 385
    j = _get(client, a, f"/compta/journal?{PERIODE}")
    assert _par_compte(j["balance"])["4441"]["debit"] == 385

    # Mauvaise période refusée, suppression possible, manager non autorisé.
    _post(client, a, "/compta/impots/paiements", {"type": "tva", "periode": "2026/09", "montant": 1}, attendu=(400,))
    assert client.post("/compta/impots/paiements", json={"type": "is", "periode": "2026", "montant": 1},
                       headers=_h(a, "manager")).status_code == 403
    assert client.delete(f"/compta/impots/paiements/{paiement['_id']}", headers=_h(a, "admin")).status_code == 200


def test_impot_sur_le_resultat_et_regime_forfaitaire(client, app, magasins):
    a = magasins["A"]
    _cameroun(app, a)
    _vente_tva(client, a, _produit(client, a))  # marge 2 000, aucun frais : bénéfice 2 000
    d = _get(client, a, f"/compta/synthese?{PERIODE}")
    assert d["impot"]["theorique"] == 660 and d["impot"]["minimum"] == 66 and d["impot"]["du"] == 660
    lignes = {l["cle"]: l["montant"] for l in d["compte_resultat"]}
    assert lignes["avant_impot"] == 2000 and lignes["impot"] == 660 and lignes["net"] == 1340
    assert _par_compte(d["bilan"]["passif"])["441"]["montant"] == 660

    # Une perte paie quand même le minimum fiscal (CA × 2,2 %).
    _post(client, a, "/charges/", {"nom": "Loyer", "montant": 10000, "categorie": "loyer"})
    d = _get(client, a, f"/compta/synthese?{PERIODE}")
    assert d["impot"]["du"] == 66 and d["indicateurs"]["resultat_net"] == 2000 - 10000 - 66

    r = client.put("/compta/fiscalite", json={"regime": "forfait"}, headers=_h(a, "admin"))
    assert r.status_code == 200 and r.get_json()["regime"] == "forfait"
    assert _get(client, a, f"/compta/synthese?{PERIODE}")["impot"]["du"] == 0
    assert client.put("/compta/fiscalite", json={"taux_is": 150}, headers=_h(a, "admin")).status_code == 400
    assert client.put("/compta/fiscalite", json={"regime": "reel"}, headers=_h(a, "manager")).status_code == 403


def test_immobilisation_tva_valeur_residuelle_et_cession(client, app, magasins):
    a = magasins["A"]
    _cameroun(app, a)
    asset_id = _post(client, a, "/assets/", {
        "nom": "Ordinateur", "valeur_achat": 100000, "montant_tva": 19250, "duree_vie_annees": 3,
        "categorie": "informatique", "residuelle_auto": True, "mode_paiement": "banque",
        "date_achat": f"{MOIS}-01T08:00:00",
    })["asset_id"]
    actif = next(x for x in _get(client, a, "/assets/") if x["_id"] == asset_id)
    assert actif["valeur_residuelle"] == 5000 and actif["residuelle_auto"] is True  # 5 % en informatique

    r = client.put(f"/assets/{asset_id}", json={"valeur_achat": 200000}, headers=_h(a, "admin"))
    assert r.status_code == 200 and r.get_json()["asset"]["valeur_residuelle"] == 10000
    r = client.put(f"/assets/{asset_id}", json={"valeur_residuelle": 0}, headers=_h(a, "admin"))
    assert r.get_json()["asset"]["valeur_residuelle"] == 0 and r.get_json()["asset"]["residuelle_auto"] is False

    d = _get(client, a, f"/compta/synthese?{PERIODE}")
    assert d["tva"]["deductible_immobilisations"] == 19250
    assert {s["cle"]: s["montant"] for s in d["tresorerie"]["sorties"]}["investissements"] == 219250
    immo = _par_compte(d["bilan"]["actif"])["2442"]
    assert immo["brut"] == 200000 and immo["montant"] == immo["brut"] - immo["amortissements"]

    r = _post(client, a, f"/assets/{asset_id}/cession", {"date_cession": AUJOURD_HUI.isoformat(), "prix_cession": 150000,
                                                        "mode_paiement": "cash"})
    cession = r["asset"]["cession"]
    assert r["asset"]["valeur_nette"] == 0 and round(cession["plus_value"], 2) == round(150000 - cession["valeur_nette"], 2)
    d = _get(client, a, f"/compta/synthese?{PERIODE}")
    lignes = {l["cle"]: l["montant"] for l in d["compte_resultat"]}
    assert lignes["produits_hao"] == 150000 and lignes["charges_hao"] == round(cession["valeur_nette"], 2)
    assert "2442" not in _par_compte(d["bilan"]["actif"])
    _post(client, a, f"/assets/{asset_id}/cession", {"date_cession": AUJOURD_HUI.isoformat(), "prix_cession": 1},
          attendu=(400,))
    assert client.delete(f"/assets/{asset_id}/cession", headers=_h(a, "admin")).status_code == 200


def test_bilan_complet_equilibre_et_journal_fidele(client, app, magasins):
    a = magasins["A"]
    _cameroun(app, a)
    _vente_tva(client, a, _produit(client, a))
    _post(client, a, "/charges/", {"nom": "Salaire d'octobre", "montant": 40000, "categorie": "salaire", "statut": "a_payer"})
    _post(client, a, "/charges/", {"nom": "CNSS", "montant": 5000, "categorie": "charges_sociales", "statut": "a_payer"})
    emprunt = _post(client, a, "/compta/financements", {"type": "emprunt", "montant": 500000, "tiers": "Banque X",
                                                        "mode_paiement": "banque"})
    _post(client, a, "/compta/financements", {"type": "remboursement", "emprunt_id": emprunt["_id"], "montant": 100000,
                                              "interets": 5000, "mode_paiement": "banque"})
    _post(client, a, "/compta/financements", {"type": "remboursement", "emprunt_id": emprunt["_id"], "montant": 900000},
          attendu=(400,))
    _post(client, a, "/compta/financements", {"type": "apport", "montant": 50000, "mode_paiement": "cash"})
    _post(client, a, "/compta/financements", {"type": "retrait", "montant": 20000, "mode_paiement": "cash"})
    _post(client, a, f"/clients/{a['client']}/ajouter-caisse", {"montant": 10000, "mode_paiement": "om"})
    r = client.put("/compta/fiscalite", json={"capital_social": 100000}, headers=_h(a, "admin"))
    assert r.status_code == 200

    d = _get(client, a, f"/compta/synthese?{PERIODE}")
    bilan = d["bilan"]
    assert bilan["total_actif"] == bilan["total_passif"]
    passif, actif = _par_compte(bilan["passif"]), _par_compte(bilan["actif"])
    assert passif["162"]["montant"] == 400000 and passif["4191"]["montant"] == 10000
    assert passif["108"]["montant"] == 30000 and passif["101"]["montant"] == 100000
    assert passif["422"]["montant"] == 40000 and passif["431"]["montant"] == 5000
    assert passif["4441"]["montant"] == 578
    assert actif["521"]["montant"] == 395000 and actif["552"]["montant"] == 10000
    assert actif["571"]["montant"] == 3578 + 30000
    assert actif["31"]["montant"] == 1000  # 10 kg restants × 100
    lignes = {l["cle"]: l["montant"] for l in d["compte_resultat"]}
    assert lignes["financieres"] == 5000 and lignes["personnel"] == 45000

    entrees = {e["cle"]: e["montant"] for e in d["tresorerie"]["entrees"]}
    assert entrees["depots_clients"] == 10000 and entrees["emprunts"] == 500000 and entrees["apports"] == 50000
    assert d["tresorerie"]["solde_fin"] == sum(s["solde"] for s in d["tresorerie"]["soldes_fin"])

    j = _get(client, a, f"/compta/journal?{PERIODE}")
    assert j["totaux"]["debit"] == j["totaux"]["credit"]
    assert j["totaux"]["resultat"] == d["indicateurs"]["resultat_net"]
    balance = _par_compte(j["balance"])
    assert balance["422"]["solde"] == -40000 and balance["162"]["solde"] == -400000
    assert balance["4191"]["solde"] == -10000 and balance["671"]["debit"] == 5000

    # Un emprunt avec des remboursements ne se supprime pas ; l'autre magasin ne voit rien.
    assert client.delete(f"/compta/financements/{emprunt['_id']}", headers=_h(a, "admin")).status_code == 400
    assert _get(client, magasins["B"], "/compta/financements")["emprunts"] == []
