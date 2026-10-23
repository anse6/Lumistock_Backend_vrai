"""Factures fournisseurs, rapprochement bancaire, clôture / verrouillage, pièces jointes."""
import io
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from extensions import db
from tests.integration.test_livraisons_magasin import _h, _produit, _stock_magasin, magasins  # noqa: F401
from tests.integration.test_retour_remboursement import _vente

AUJOURD_HUI = datetime.now()
PERIODE = f"debut={AUJOURD_HUI:%Y-%m}-01&fin={AUJOURD_HUI:%Y-%m-%d}"
HIER = (AUJOURD_HUI - timedelta(days=1)).strftime("%Y-%m-%d")
AVANT_HIER = (AUJOURD_HUI - timedelta(days=2)).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def _propre(app, tmp_path):
    app.config["PIECES_JOINTES_FOLDER"] = str(tmp_path)
    with app.app_context():
        for table in ("charges", "assets", "factures_fournisseurs", "releves_bancaires", "clotures"):
            db.tables[table].delete_many({})


def _ok(r, statut=200):
    assert r.status_code == statut, r.get_json()
    return r.get_json()


def _synthese(client, m):
    return _ok(client.get(f"/compta/synthese?{PERIODE}", headers=_h(m, "admin")))


# --------------------------------------------------------------------------- factures fournisseurs
def test_facture_fournisseur_achat_a_credit(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    f = _ok(client.post("/factures-fournisseurs", json={
        "fournisseur_nom": "Grossiste Douala", "numero": "FA-1", "date_echeance": AUJOURD_HUI.strftime("%Y-%m-%d"),
        "lignes": [{"produit_id": produit_id, "quantite": 10, "prix_achat": 120}],
        "paiement": {"montant": 500, "mode": "cash"},
    }, headers=_h(a, "manager")), 201)
    assert f["montant_ttc"] == 1200 and f["montant_paye"] == 500 and f["statut"] == "partielle"
    assert _stock_magasin(app, produit_id) == 30

    d = _synthese(client, a)
    sorties = {s["libelle"]: s["montant"] for s in d["tresorerie"]["sorties"]}
    assert sorties["Achats de marchandises au comptant"] == 0  # achat à crédit, pas au comptant
    assert sorties["Règlements des factures fournisseurs"] == 500
    assert d["a_payer"]["montant_factures"] == 700 and d["a_payer"]["factures"] == 1

    j = _ok(client.get(f"/compta/journal?{PERIODE}", headers=_h(a, "admin")))
    balance = {b["compte"]: b for b in j["balance"]}
    assert balance["601"]["debit"] == 1200 and balance["401"]["solde"] == -700

    # Règlements : pas au-delà du reste dû
    assert client.post(f"/factures-fournisseurs/{f['_id']}/paiements", json={"montant": 800},
                       headers=_h(a, "admin")).status_code == 400
    f = _ok(client.post(f"/factures-fournisseurs/{f['_id']}/paiements", json={"montant": 700, "mode": "om"},
                        headers=_h(a, "admin")))
    assert f["statut"] == "payee" and f["reste"] == 0
    # Annulation d'un règlement (admin seulement)
    pid = f["paiements"][-1]["id"]
    assert client.delete(f"/factures-fournisseurs/{f['_id']}/paiements/{pid}", headers=_h(a, "manager")).status_code == 403
    assert _ok(client.delete(f"/factures-fournisseurs/{f['_id']}/paiements/{pid}", headers=_h(a, "admin")))["reste"] == 700

    # Même numéro chez le même fournisseur : refusé ; produit d'un autre magasin : refusé
    assert client.post("/factures-fournisseurs", json={"fournisseur_nom": "Grossiste Douala", "numero": "fa-1",
                                                       "lignes": [{"produit_id": produit_id, "quantite": 1, "prix_achat": 1}]},
                       headers=_h(a, "admin")).status_code == 400
    autre = _produit(client, magasins["B"])
    assert client.post("/factures-fournisseurs", json={"fournisseur_nom": "X",
                                                       "lignes": [{"produit_id": autre, "quantite": 1, "prix_achat": 1}]},
                       headers=_h(a, "admin")).status_code == 400
    assert _ok(client.get("/factures-fournisseurs", headers=_h(magasins["B"], "admin"))) == []


# --------------------------------------------------------------------------- verrouillage et clôture
def test_verrouillage_bloque_les_operations_de_la_periode(client, app, magasins):
    a = magasins["A"]
    assert client.put("/compta/verrouillage", json={"date": HIER}, headers=_h(a, "manager")).status_code == 403
    assert client.put("/compta/verrouillage", json={"date": AUJOURD_HUI.strftime("%Y-%m-%d")},
                      headers=_h(a, "admin")).status_code == 400  # pas une période future
    produit_id = _produit(client, a)
    vente = _vente(client, a, produit_id, 2, 600)
    with app.app_context():  # vente de l'avant-veille
        db.tables.livraisons.update_one({"_id": ObjectId(vente)}, {"$set": {"date_livraison": datetime.now() - timedelta(days=2)}})
    _ok(client.put("/compta/verrouillage", json={"date": HIER}, headers=_h(a, "admin")))

    r = client.post("/charges/", json={"nom": "Loyer", "montant": 10, "categorie": "loyer", "date_charge": f"{AVANT_HIER}T10:00:00"},
                    headers=_h(a, "admin"))
    assert r.status_code == 409 and "clôturée" in r.get_json()["error"]
    _ok(client.post("/charges/", json={"nom": "Loyer", "montant": 10, "categorie": "loyer"}, headers=_h(a, "admin")), 201)
    # Modifier ou annuler la vente close : refusé
    assert client.delete(f"/livraisons/{vente}", headers=_h(a, "admin")).status_code == 409
    assert client.put(f"/admin_or_manager/livraisons/{vente}", json={"montant_paye": 0},
                      headers=_h(a, "admin")).status_code == 409

    # Le retour reste possible : il est compté aujourd'hui, la période close ne bouge pas.
    hier = f"debut={AVANT_HIER}&fin={HIER}"
    avant = _ok(client.get(f"/compta/synthese?{hier}", headers=_h(a, "admin")))["indicateurs"]["chiffre_affaires"]
    assert avant == 600
    _ok(client.post("/livraisons/retour", json={"livraison_id": vente, "motif": "x",
                                                 "produits": [{"produit_id": produit_id, "quantite": 1}]},
                    headers=_h(a, "admin")), 201)
    assert _ok(client.get(f"/compta/synthese?{hier}", headers=_h(a, "admin")))["indicateurs"]["chiffre_affaires"] == 600
    jour = AUJOURD_HUI.strftime("%Y-%m-%d")
    lignes = {l["cle"]: l["montant"] for l in
              _ok(client.get(f"/compta/synthese?debut={jour}&fin={jour}", headers=_h(a, "admin")))["compte_resultat"]}
    assert lignes["retours"] == 300 and lignes["ca"] == -300 and lignes["cout"] == -100


def test_cloture_exercice(client, magasins):
    a = magasins["A"]
    annee = AUJOURD_HUI.year - 1
    _ok(client.post("/charges/", json={"nom": "Loyer", "montant": 1000, "categorie": "loyer",
                                       "date_charge": f"{annee}-06-15T10:00:00"}, headers=_h(a, "admin")), 201)
    assert client.post("/compta/clotures", json={"exercice": AUJOURD_HUI.year}, headers=_h(a, "admin")).status_code == 400
    e = _ok(client.post("/compta/clotures", json={"exercice": annee}, headers=_h(a, "admin")))
    assert e["clotures"][0]["exercice"] == annee and e["clotures"][0]["resultat"] == -1000
    assert e["date_verrouillage"].startswith(f"{annee}-12-31")
    assert client.post("/compta/clotures", json={"exercice": annee}, headers=_h(a, "admin")).status_code == 400
    assert client.post("/charges/", json={"nom": "X", "montant": 1, "categorie": "autre", "date_charge": f"{annee}-03-01T10:00:00"},
                       headers=_h(a, "admin")).status_code == 409
    # Impossible de déverrouiller un exercice clôturé sans le rouvrir
    assert client.put("/compta/verrouillage", json={"date": None}, headers=_h(a, "admin")).status_code == 400
    e = _ok(client.delete(f"/compta/clotures/{annee}", headers=_h(a, "admin")))
    assert e["clotures"] == [] and e["date_verrouillage"] is None


# --------------------------------------------------------------------------- rapprochement bancaire
def test_rapprochement_bancaire(client, magasins):
    a = magasins["A"]
    jour = AUJOURD_HUI.strftime("%d/%m/%Y")
    _ok(client.post("/charges/", json={"nom": "Loyer", "montant": 5000, "categorie": "loyer", "mode_paiement": "bancaire"},
                    headers=_h(a, "admin")), 201)
    csv = f"Date;Libellé;Débit;Crédit;Référence\n{jour};PRLV LOYER;5 000;;R1\n{jour};FRAIS TENUE DE COMPTE;1 500;;R2\n"
    r = _ok(client.post("/compta/rapprochement/releve", data={"compte": "521", "fichier": (io.BytesIO(csv.encode()), "releve.csv")},
                        headers=_h(a, "admin"), content_type="multipart/form-data"))
    assert r["ajoutees"] == 2
    # Ré-import du même relevé : pas de doublon
    r = _ok(client.post("/compta/rapprochement/releve", data={"compte": "521", "fichier": (io.BytesIO(csv.encode()), "releve.csv")},
                        headers=_h(a, "admin"), content_type="multipart/form-data"))
    assert r["ajoutees"] == 0 and r["doublons"] == 2

    assert _ok(client.post("/compta/rapprochement/auto", json={"compte": "521"}, headers=_h(a, "admin")))["rapprochees"] == 1
    etat = _ok(client.get(f"/compta/rapprochement?compte=521&{PERIODE}", headers=_h(a, "admin")))
    frais = next(l for l in etat["lignes"] if l["statut"] != "rapproche")
    assert frais["montant"] == -1500 and etat["totaux"]["ecart_releve"] == -1500

    # Montant différent : refusé
    loyer = next(l for l in etat["lignes"] if l["statut"] == "rapproche")
    assert client.put(f"/compta/rapprochement/lignes/{frais['id']}", json={"refs": [loyer["operations"][0]["ref"]]},
                      headers=_h(a, "admin")).status_code == 400
    # Les frais deviennent une charge rapprochée
    _ok(client.post(f"/compta/rapprochement/lignes/{frais['id']}/charge", json={}, headers=_h(a, "admin")))
    etat = _ok(client.get(f"/compta/rapprochement?compte=521&{PERIODE}", headers=_h(a, "admin")))
    assert etat["totaux"]["lignes_non_rapprochees"] == 0 and etat["totaux"]["ecart_releve"] == 0
    assert etat["en_attente"] == []
    charges = _ok(client.get("/charges/", headers=_h(a, "admin")))
    assert any(c["categorie"] == "frais_bancaires" and c["montant"] == 1500 for c in charges)
    # Un autre magasin ne voit rien
    assert _ok(client.get(f"/compta/rapprochement?compte=521&{PERIODE}", headers=_h(magasins["B"], "admin")))["lignes"] == []


# --------------------------------------------------------------------------- pièces jointes
def test_pieces_jointes_des_charges(client, magasins):
    a, b = magasins["A"], magasins["B"]
    charge = _ok(client.post("/charges/", json={"nom": "Loyer", "montant": 10, "categorie": "loyer"}, headers=_h(a, "admin")), 201)["charge_id"]
    pdf = b"%PDF-1.4\n% facture\n"
    c = _ok(client.post(f"/charges/{charge}/pieces", data={"fichier": (io.BytesIO(pdf), "facture loyer.pdf")},
                        headers=_h(a, "admin"), content_type="multipart/form-data"), 201)["charge"]
    piece = c["pieces_jointes"][0]
    assert piece["type"] == "application/pdf" and "fichier" not in piece

    r = client.get(f"/charges/{charge}/pieces/{piece['id']}", headers=_h(a, "admin"))
    assert r.status_code == 200 and r.data == pdf
    assert client.get(f"/charges/{charge}/pieces/{piece['id']}", headers=_h(b, "admin")).status_code == 404

    r = client.post(f"/charges/{charge}/pieces", data={"fichier": (io.BytesIO(b"<script>"), "x.pdf")},
                    headers=_h(a, "admin"), content_type="multipart/form-data")
    assert r.status_code == 400
    c = _ok(client.delete(f"/charges/{charge}/pieces/{piece['id']}", headers=_h(a, "admin")))["charge"]
    assert c["pieces_jointes"] == []


# --------------------------------------------------------------------------- frais de retour
def test_frais_de_retour_politique_et_choix(client, app, magasins):
    a = magasins["A"]
    assert _ok(client.get("/compta/politique-retour", headers=_h(a, "manager")))["retenue_pourcentage"] == 0
    assert client.put("/compta/politique-retour", json={"retenue_pourcentage": 1}, headers=_h(a, "manager")).status_code == 403
    assert client.put("/compta/politique-retour", json={"retenue_pourcentage": 150}, headers=_h(a, "admin")).status_code == 400
    _ok(client.put("/compta/politique-retour", json={"retenue_pourcentage": 10}, headers=_h(a, "admin")))
    produit_id = _produit(client, a, quantite=20)

    # Politique du magasin : 10 % de 1 200 gardés, 1 080 à rembourser.
    vente = _vente(client, a, produit_id, 10, 3000)
    r = _ok(client.post("/livraisons/retour", json={"livraison_id": vente, "motif": "x",
                                                     "produits": [{"produit_id": produit_id, "quantite": 4}]},
                        headers=_h(a, "admin")), 201)
    assert r["frais_retour"] == 120 and r["montant_a_rembourser"] == 1080
    with app.app_context():
        v = db.tables.livraisons.find_one({"_id": ObjectId(vente)})
    assert v["montant_total"] == 1800 + 120 and v["frais_retour"] == 120

    # Le magasin peut renoncer aux frais pour ce client.
    vente2 = _vente(client, a, produit_id, 2, 600)
    r = _ok(client.post("/livraisons/retour", json={"livraison_id": vente2, "motif": "x", "retenue_pourcentage": 0,
                                                     "produits": [{"produit_id": produit_id, "quantite": 1}]},
                        headers=_h(a, "manager")), 201)
    assert r["frais_retour"] == 0 and r["montant_a_rembourser"] == 300

    d = _synthese(client, a)
    lignes = {l["cle"]: l["montant"] for l in d["compte_resultat"]}
    assert lignes["ventes_brutes"] == 3600 and lignes["retours"] == 1500 and lignes["ca"] == 2100
    assert lignes["frais_retour"] == 120
    j = _ok(client.get(f"/compta/journal?{PERIODE}", headers=_h(a, "admin")))
    assert j["totaux"]["debit"] == j["totaux"]["credit"]
    assert {b["compte"]: b for b in j["balance"]}["7078"]["credit"] == 120


# --------------------------------------------------------------------------- avoirs fournisseurs
def test_avoir_fournisseur_et_remboursement(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=0 + 1)
    f = _ok(client.post("/factures-fournisseurs", json={
        "fournisseur_nom": "Grossiste", "lignes": [{"produit_id": produit_id, "quantite": 10, "prix_achat": 100}],
        "montant_tva": 192.5, "paiement": {"montant": 1192.5, "mode": "cash"},
    }, headers=_h(a, "admin")), 201)
    assert f["statut"] == "payee" and _stock_magasin(app, produit_id) == 11

    # 4 articles rendus : stock −4, avoir de 400 HT + 77 TVA ; le fournisseur doit 477.
    f = _ok(client.post(f"/factures-fournisseurs/{f['_id']}/avoirs", json={
        "motif": "Défaut", "lignes": [{"produit_id": produit_id, "quantite": 4}]}, headers=_h(a, "admin")))
    assert _stock_magasin(app, produit_id) == 7
    assert f["montant_avoirs"] == 477 and f["a_recevoir"] == 477 and f["rendu"][produit_id] == 4
    assert client.post(f"/factures-fournisseurs/{f['_id']}/avoirs", json={
        "lignes": [{"produit_id": produit_id, "quantite": 7}]}, headers=_h(a, "admin")).status_code == 400
    d = _synthese(client, a)
    actif = {b["libelle"]: b["montant"] for b in d["bilan"]["actif"]}
    assert actif["Fournisseurs débiteurs (avoirs à rembourser)"] == 477
    assert d["tva"]["deductible"] == 116  # 115,5 arrondi au franc CFA

    assert client.post(f"/factures-fournisseurs/{f['_id']}/paiements", json={"montant": 500, "type": "remboursement"},
                       headers=_h(a, "admin")).status_code == 400
    f = _ok(client.post(f"/factures-fournisseurs/{f['_id']}/paiements", json={"montant": 477, "type": "remboursement", "mode": "cash"},
                        headers=_h(a, "admin")))
    assert f["a_recevoir"] == 0 and f["statut"] == "payee"

    # Annulation du reste de la facture (sans lignes) : tout est rendu.
    f = _ok(client.post(f"/factures-fournisseurs/{f['_id']}/avoirs", json={"motif": "Annulation"}, headers=_h(a, "admin")))
    assert f["montant_net"] == 0 and _stock_magasin(app, produit_id) == 1
    j = _ok(client.get(f"/compta/journal?{PERIODE}", headers=_h(a, "admin")))
    assert j["totaux"]["debit"] == j["totaux"]["credit"]
    balance = {b["compte"]: b for b in j["balance"]}
    assert balance["601"]["solde"] == 0  # achat entièrement annulé


# --------------------------------------------------------------------------- heure locale
def test_rapports_a_l_heure_locale(client, app, magasins):
    """Vente enregistrée en UTC la veille à 23 h 30 = 0 h 30 à Douala (UTC+1) :
    elle appartient au jour local, dans les rapports comme en comptabilité."""
    from datetime import timezone

    a = magasins["A"]
    produit_id = _produit(client, a)
    vente = _vente(client, a, produit_id, 1, 300)
    jour_local = datetime(2026, 3, 10)
    utc = (jour_local.replace(hour=0, minute=30).astimezone(timezone.utc)).replace(tzinfo=None)
    with app.app_context():
        db.tables.livraisons.update_one({"_id": ObjectId(vente)}, {"$set": {"date_livraison": utc}})
    jour = "debut=2026-03-10&fin=2026-03-10"
    ventes = _ok(client.get(f"/rapports/ventes?{jour}", headers=_h(a, "admin")))["ventes"]
    assert len(ventes) == 1 and ventes[0]["date"].startswith("2026-03-10T00:30")
    assert _ok(client.get(f"/compta/synthese?{jour}", headers=_h(a, "admin")))["indicateurs"]["chiffre_affaires"] == 300
