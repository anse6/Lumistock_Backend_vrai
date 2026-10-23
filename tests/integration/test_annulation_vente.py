"""Annulation d'une vente : stock, argent du client et restauration."""
from bson import ObjectId

from extensions import db
from tests.integration.test_livraisons_magasin import _h, _produit, _stock_magasin, magasins  # noqa: F401
from tests.integration.test_retour_remboursement import _credit, _vente


def _annulation(app, livraison_id):
    with app.app_context():
        return db.tables.livraisons.find_one({"_id": ObjectId(livraison_id)}).get("annulation")


def test_annulation_vente_payee_rend_le_stock_et_l_argent_en_especes(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 10, 3000)
    assert _stock_magasin(app, produit_id) == 10

    r = client.delete(f"/livraisons/{livraison_id}", json={"mode": "especes", "motif": "Erreur de saisie"},
                      headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["montant_rendu"] == 3000
    assert _stock_magasin(app, produit_id) == 20
    assert _credit(app, a) == 0
    annulation = _annulation(app, livraison_id)
    assert annulation["mode"] == "especes" and annulation["montant_rendu"] == 3000
    assert annulation["motif"] == "Erreur de saisie" and annulation["par_nom"]

    # L'argent a été rendu en espèces : la vente ne peut plus être restaurée
    # (elle paraîtrait payée alors que le client a été remboursé).
    r = client.post(f"/livraisons/corbeille/{livraison_id}/restaurer", headers=_h(a, "admin"))
    assert r.status_code == 400 and "rendus" in r.get_json()["error"]
    assert _stock_magasin(app, produit_id) == 20
    assert _annulation(app, livraison_id)["montant_rendu"] == 3000


def test_restauration_d_une_vente_non_payee_annulee(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 10, 0, statut="non payé")
    r = client.delete(f"/livraisons/{livraison_id}", json={"mode": "especes"}, headers=_h(a, "admin"))
    assert r.status_code == 200 and r.get_json()["montant_rendu"] == 0
    r = client.post(f"/livraisons/corbeille/{livraison_id}/restaurer", headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    assert _stock_magasin(app, produit_id) == 10
    assert _annulation(app, livraison_id) is None


def test_annulation_avec_credit_client_puis_restauration(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 10, 1000, statut="partiellement payé")

    # Orange Money : référence et numéro exigés
    r = client.delete(f"/livraisons/{livraison_id}", json={"mode": "om"}, headers=_h(a, "admin"))
    assert r.status_code == 400
    assert _stock_magasin(app, produit_id) == 10  # rien n'a bougé

    r = client.delete(f"/livraisons/{livraison_id}", json={"mode": "credit_client"}, headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    assert _credit(app, a) == 1000 and _stock_magasin(app, produit_id) == 20

    r = client.post(f"/livraisons/corbeille/{livraison_id}/restaurer", headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    assert _credit(app, a) == 0 and _stock_magasin(app, produit_id) == 10


def test_restauration_refusee_si_le_credit_d_annulation_est_depense(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 10, 1000, statut="partiellement payé")
    client.delete(f"/livraisons/{livraison_id}", json={"mode": "credit_client"}, headers=_h(a, "admin"))
    with app.app_context():
        db.tables.clients.update_one({"_id": ObjectId(a["client"])}, {"$set": {"montant_en_caisse": 200.0}})
    r = client.post(f"/livraisons/corbeille/{livraison_id}/restaurer", headers=_h(a, "admin"))
    assert r.status_code == 400 and "crédit" in r.get_json()["error"]
    assert _stock_magasin(app, produit_id) == 20 and _credit(app, a) == 200


def test_annulation_apres_retour_rend_aussi_le_trop_percu(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 10, 3000)
    client.post("/livraisons/retour", json={"livraison_id": livraison_id, "motif": "Défaut",
                                            "produits": [{"produit_id": produit_id, "quantite": 4}]},
                headers=_h(a, "admin"))
    assert _stock_magasin(app, produit_id) == 14

    r = client.delete(f"/livraisons/{livraison_id}", headers=_h(a, "admin"))  # ancien appel sans corps
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["montant_rendu"] == 3000  # 1 800 payés + 1 200 de trop-perçu non remboursé
    assert _stock_magasin(app, produit_id) == 20  # les 6 restants reviennent, pas 10


def test_corbeille_expose_l_annulation(client, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _vente(client, a, produit_id, 2, 600)
    client.delete(f"/livraisons/{livraison_id}", json={"motif": "Doublon"}, headers=_h(a, "admin"))
    corbeille = client.get("/livraisons/corbeille", headers=_h(a, "admin")).get_json()
    vente = next(v for v in corbeille if v["id"] == livraison_id)
    assert vente["deleted_at"] and vente["annulation"]["motif"] == "Doublon"
    assert vente["annulation"]["montant_rendu"] == 600
