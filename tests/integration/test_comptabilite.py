"""Page Finance et Charges : compte de résultat, trésorerie, TVA, journal, charges."""
from datetime import datetime

import pytest

from extensions import db
from models.asset_model import AssetModel
from tests.integration.test_livraisons_magasin import _h, _produit, magasins  # noqa: F401
from tests.integration.test_retour_remboursement import _vente

AUJOURD_HUI = datetime.now()
PERIODE = f"debut={AUJOURD_HUI:%Y-%m}-01&fin={AUJOURD_HUI:%Y-%m-%d}"


@pytest.fixture(autouse=True)
def _tables_vides(app):
    with app.app_context():
        for table in ("charges", "assets"):
            db.tables[table].delete_many({})


def _get(client, m, url, role="admin"):
    r = client.get(url, headers=_h(m, role))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _charge(client, m, **donnees):
    r = client.post("/charges/", json={"categorie": "autre", **donnees}, headers=_h(m, "admin"))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["charge_id"]


def _scenario(client, m):
    """Vente de 10 × 300 (achat 100) payée ; réappro de 5 à 120 ; 2 avariés ;
    loyer de 50 000 payé ; électricité de 11 800 dont 1 800 de TVA, à payer."""
    produit_id = _produit(client, m, quantite=20)
    _vente(client, m, produit_id, 10, 3000)
    r = client.post(f"/produits/{produit_id}/approvisionner", json={"quantite": 5, "prix_achat": 120},
                    headers=_h(m, "admin"))
    assert r.status_code == 200, r.get_json()
    r = client.post(f"/produits/{produit_id}/perime", json={"quantite": 2}, headers=_h(m, "admin"))
    assert r.status_code == 200, r.get_json()
    loyer = _charge(client, m, nom="Loyer", montant=50000, categorie="loyer")
    courant = _charge(client, m, nom="Électricité", montant=11800, montant_tva=1800, categorie="electricite",
                      statut="a_payer", fournisseur="ENEO", reference="F-42")
    return produit_id, loyer, courant


def test_compte_de_resultat_tresorerie_et_tva(client, app, magasins):
    a = magasins["A"]
    _scenario(client, a)
    d = _get(client, a, f"/compta/synthese?{PERIODE}")
    lignes = {l["cle"]: l["montant"] for l in d["compte_resultat"]}
    assert lignes["ca"] == 3000 and lignes["cout"] == 1000 and lignes["marge"] == 2000
    # CMUP : 10 restants à 100 + 5 à 120 → coût moyen 106,67 ; 2 avariés = 213,33
    # (et non 2 × 120 : le dernier prix d'achat n'est pas le coût du stock).
    assert lignes["pertes"] == pytest.approx(213.33, abs=0.01)
    assert lignes["externes"] == 60000  # 50 000 + 10 000 HT
    assert lignes["cat_loyer"] == 50000 and lignes["cat_electricite"] == 10000
    assert lignes["net"] == pytest.approx(2000 - 213.33 - 60000, abs=0.01)
    assert d["indicateurs"]["taux_marge"] == 66.7

    t = d["tresorerie"]
    assert t["total_entrees"] == 3000
    sorties = {s["libelle"]: s["montant"] for s in t["sorties"]}
    assert sorties["Charges payées"] == 50000  # l'électricité n'est pas encore payée
    assert sorties["Achats de marchandises au comptant"] == 600
    assert t["solde_debut"] == 0 and t["solde_fin"] == 3000 - 50600

    assert (d["tva"]["collectee"], d["tva"]["deductible"], d["tva"]["a_reverser"]) == (0, 1800, -1800)
    assert d["tva"]["deductible_charges"] == 1800
    assert d["a_payer"]["montant"] == 11800 and d["a_payer"]["nombre"] == 1
    bilan = {b["compte"]: b["montant"] for b in d["bilan"]["passif"]}
    assert bilan["401"] == 11800
    assert sum(b["montant"] for b in d["bilan"]["actif"]) == sum(b["montant"] for b in d["bilan"]["passif"])

    # Le magasin B ne voit rien du magasin A.
    assert _get(client, magasins["B"], f"/compta/synthese?{PERIODE}")["indicateurs"]["chiffre_affaires"] == 0
    # Ancienne route : limitée au magasin, CA hors TVA.
    assert _get(client, a, "/managers/stats/profit-perte")["chiffre_affaires"] == 3000
    assert _get(client, magasins["B"], "/managers/stats/profit-perte")["chiffre_affaires"] == 0


def test_solde_ouverture_et_paiement_de_charge(client, magasins):
    a = magasins["A"]
    _, _, courant = _scenario(client, a)
    r = client.put("/compta/parametres", json={"solde_ouverture": 100000, "date_ouverture": "2000-01-01"},
                   headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    assert client.put("/compta/parametres", json={"solde_ouverture": 1}, headers=_h(a, "manager")).status_code == 403

    r = client.post(f"/charges/{courant}/payer", json={"mode_paiement": "om"}, headers=_h(a, "admin"))
    assert r.status_code == 200 and r.get_json()["charge"]["statut"] == "payee"
    d = _get(client, a, f"/compta/synthese?{PERIODE}")
    assert d["tresorerie"]["solde_fin"] == 100000 + 3000 - 50000 - 11800 - 600
    comptes = {c["compte"]: c for c in d["tresorerie"]["par_compte"]}
    assert comptes["552"]["sorties"] == 11800  # payé par Orange Money
    assert d["a_payer"]["montant"] == 0


def test_journal_equilibre_au_plan_syscohada(client, magasins):
    a = magasins["A"]
    _scenario(client, a)
    j = _get(client, a, f"/compta/journal?{PERIODE}")
    assert j["totaux"]["debit"] == j["totaux"]["credit"] > 0
    for e in j["ecritures"]:
        assert round(sum(l["debit"] for l in e["lignes"]), 2) == round(sum(l["credit"] for l in e["lignes"]), 2)
    balance = {b["compte"]: b for b in j["balance"]}
    assert balance["701"]["credit"] == 3000 and balance["601"]["debit"] == 600
    assert balance["622"]["debit"] == 50000 and balance["4452"]["debit"] == 1800
    assert balance["401"]["solde"] == -11800  # électricité restant due
    assert balance["411"]["solde"] == 0  # vente encaissée


def test_charges_isolees_par_magasin(client, magasins):
    a, b = magasins["A"], magasins["B"]
    charge = _charge(client, a, nom="Loyer", montant=1000)
    assert client.put(f"/charges/{charge}", json={"montant": 1}, headers=_h(b, "admin")).status_code == 404
    assert client.delete(f"/charges/{charge}", headers=_h(b, "admin")).status_code == 404
    assert client.post(f"/charges/{charge}/payer", json={}, headers=_h(b, "admin")).status_code == 404
    assert _get(client, b, "/charges/") == []
    r = client.post("/charges/", json={"nom": "X", "montant": 100, "montant_tva": 200, "categorie": "autre"},
                    headers=_h(a, "admin"))
    assert r.status_code == 400


def test_charges_recurrentes_creees_une_seule_fois(client, app, magasins):
    a = magasins["A"]
    mois_dernier = (AUJOURD_HUI.replace(day=1) - __import__("datetime").timedelta(days=1)).replace(day=5)
    _charge(client, a, nom="Loyer", montant=50000, categorie="loyer", recurrence="mensuelle",
            date_charge=mois_dernier.isoformat())
    r = client.post("/charges/recurrentes/generer", json={}, headers=_h(a, "admin"))
    assert r.status_code == 201 and len(r.get_json()["charges"]) == 1
    r = client.post("/charges/recurrentes/generer", json={}, headers=_h(a, "admin"))
    assert r.status_code == 200 and r.get_json()["charges"] == []
    nouvelle = [c for c in _get(client, a, "/charges/") if c["statut"] == "a_payer"]
    assert len(nouvelle) == 1 and nouvelle[0]["date_charge"][:7] == f"{AUJOURD_HUI:%Y-%m}"


def test_amortissement_lineaire_au_prorata():
    actif = {"valeur_achat": 4800, "valeur_residuelle": 0, "duree_vie_annees": 4, "date_achat": datetime(2025, 1, 1)}
    annee = AssetModel.amortissement_entre(actif, datetime(2025, 1, 1), datetime(2026, 1, 1))
    assert abs(annee - 1200) < 5
    assert AssetModel.amortissement_entre(actif, datetime(2020, 1, 1), datetime(2024, 12, 31)) == 0
    assert AssetModel.valeur_nette(actif, datetime(2030, 1, 1)) == 0
    assert AssetModel.calculate_monthly_depreciation(actif, datetime(2025, 6, 1)) == 100
    assert AssetModel.calculate_monthly_depreciation(actif, datetime(2029, 6, 1)) == 0
