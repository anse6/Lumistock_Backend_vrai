"""Retour d'articles et remboursement du client."""
from bson import ObjectId

from extensions import db
from tests.integration.test_livraisons_magasin import _h, _produit, _stock_magasin, magasins  # noqa: F401


def _vente(client, m, produit_id, quantite, montant_paye, statut="payé", reductions=None):
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": produit_id, "quantite": quantite, "prix": 300}],
        "statut_paiement": statut, "montant_paye": montant_paye, "client_id": m["client"],
        "reductions": reductions or [],
    }, headers=_h(m, "manager"))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["livraison_id"]


def _livraison(client, m, livraison_id):
    data = client.get(f"/livraisons/{livraison_id}", headers=_h(m, "admin")).get_json()
    return data.get("livraison", data)


def _credit(app, m):
    with app.app_context():
        return float(db.tables.clients.find_one({"_id": ObjectId(m["client"])}).get("montant_en_caisse") or 0)


def test_retour_vente_payee_puis_remboursement_en_especes(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 10, 3000)
    assert _stock_magasin(app, produit_id) == 10

    r = client.post("/livraisons/retour", json={"livraison_id": livraison_id, "motif": "Défaut",
                                                 "produits": [{"produit_id": produit_id, "quantite": 4}]},
                    headers=_h(a, "admin"))
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["montant_a_rembourser"] == 1200
    assert _stock_magasin(app, produit_id) == 14
    assert _credit(app, a) == 0  # plus de crédit automatique

    data = _livraison(client, a, livraison_id)
    assert data["montant_total"] == 1800 and data["montant_paye"] == 1800 and data["retour"] == 1200
    assert data["retours"][0]["produits"][0]["quantite"] == 4

    # Remboursement partiel puis du solde
    r = client.post(f"/livraisons/refund/{livraison_id}", json={"mode": "especes", "montant": 500}, headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["restant"] == 700
    r = client.post(f"/livraisons/refund/{livraison_id}", json={"montant": 800}, headers=_h(a, "admin"))
    assert r.status_code == 400  # plus que le dû
    r = client.post(f"/livraisons/refund/{livraison_id}", headers=_h(a, "admin"))  # ancien appel sans corps
    assert r.status_code == 200, r.get_json()

    data = _livraison(client, a, livraison_id)
    assert data["retour"] == 0
    assert data["have_retour"] is True and len(data["retours"]) == 1  # historique conservé
    assert [x["montant"] for x in data["remboursements"]] == [500, 700]
    assert _credit(app, a) == 0
    r = client.post(f"/livraisons/refund/{livraison_id}", headers=_h(a, "admin"))
    assert r.status_code == 400


def test_remboursement_sur_le_compte_client(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 5, 1500)
    client.post("/livraisons/retour", json={"livraison_id": livraison_id, "motif": "x",
                                             "produits": [{"produit_id": produit_id, "quantite": 2}]},
                headers=_h(a, "admin"))
    r = client.post(f"/livraisons/refund/{livraison_id}", json={"mode": "credit_client"}, headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    assert _credit(app, a) == 600


def test_retour_vente_partiellement_payee_sans_trop_percu(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 10, 1000, statut="partiellement payé")
    r = client.post("/livraisons/retour", json={"livraison_id": livraison_id, "motif": "x",
                                                 "produits": [{"produit_id": produit_id, "quantite": 3}]},
                    headers=_h(a, "admin"))
    assert r.status_code == 201
    data = _livraison(client, a, livraison_id)
    # 2100 restants, 1000 payés : rien à rendre, la dette baisse.
    assert data["montant_total"] == 2100 and data["montant_paye"] == 1000 and data["retour"] == 0
    assert client.post(f"/livraisons/refund/{livraison_id}", headers=_h(a, "admin")).status_code == 400


def test_retour_tient_compte_des_reductions(client, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 10, 2700,
                          reductions=[{"type": "remise", "mode": "pourcentage", "valeur": 10}])
    r = client.post("/livraisons/retour", json={"livraison_id": livraison_id, "motif": "x",
                                                 "produits": [{"produit_id": produit_id, "quantite": 5}]},
                    headers=_h(a, "admin"))
    assert r.get_json()["montant_retour"] == 1350
    assert r.get_json()["montant_a_rembourser"] == 1350


def test_retour_quantite_superieure_refusee(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 3, 900)
    r = client.post("/livraisons/retour", json={"livraison_id": livraison_id, "motif": "x",
                                                 "produits": [{"produit_id": produit_id, "quantite": 4}]},
                    headers=_h(a, "admin"))
    assert r.status_code == 400
    assert _stock_magasin(app, produit_id) == 17


def test_remboursement_mobile_money_trace_et_tresorerie(client, app, magasins):
    from datetime import datetime, timedelta
    from services import compta_service

    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 10, 3000)
    r = client.post("/livraisons/retour", json={"livraison_id": livraison_id, "motif": "Défaut",
                                                 "produits": [{"produit_id": produit_id, "quantite": 5}]},
                    headers=_h(a, "admin"))
    assert r.status_code == 201, r.get_json()

    # Orange Money : référence et numéro obligatoires
    r = client.post(f"/livraisons/refund/{livraison_id}", json={"mode": "om", "montant": 1000}, headers=_h(a, "admin"))
    assert r.status_code == 400 and "référence" in r.get_json()["error"]
    r = client.post(f"/livraisons/refund/{livraison_id}", json={"mode": "om", "montant": 1000, "reference": "OM123"},
                    headers=_h(a, "admin"))
    assert r.status_code == 400 and "téléphone" in r.get_json()["error"]
    r = client.post(f"/livraisons/refund/{livraison_id}",
                    json={"mode": "om", "montant": 1000, "reference": "OM123", "telephone": "+237 6900000001"},
                    headers=_h(a, "admin"))
    assert r.status_code == 400 and "9 chiffres" in r.get_json()["error"]
    r = client.post(f"/livraisons/refund/{livraison_id}",
                    json={"mode": "momo", "montant": 200, "reference": "MM9", "telephone": "+225 07 01 02 03 04"},
                    headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()  # numéro étranger (Côte d'Ivoire)
    r = client.post(f"/livraisons/refund/{livraison_id}",
                    json={"mode": "om", "montant": 1000, "reference": "OM123", "telephone": "690000000"},
                    headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    r = client.post(f"/livraisons/refund/{livraison_id}", json={"mode": "credit_client"}, headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()

    data = _livraison(client, a, livraison_id)
    assert data["retour"] == 0
    assert data["montant_rembourse"] == 1500 and data["montant_retourne"] == 1500
    assert data["remboursements"][0]["telephone"] == "+2250701020304"
    om = data["remboursements"][1]
    assert om["reference"] == "OM123" and om["telephone"] == "+237690000000" and om["par_nom"]

    # Seul l'argent réellement rendu (Orange Money) sort de la trésorerie.
    with app.app_context():
        rapport = compta_service.synthese(a["magasin"], datetime.now() - timedelta(days=1),
                                          datetime.now() + timedelta(days=1))
    sorties = {s["libelle"]: s["montant"] for s in rapport["tresorerie"]["sorties"]}
    assert sorties["Remboursements clients"] == 1200
