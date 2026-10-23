"""Corrections des calculs de vente : changement de client, restauration, ristournes, trop-perçu."""
from bson import ObjectId

from extensions import db
from tests.integration.test_livraisons_magasin import _h, _produit, _stock_magasin, magasins  # noqa: F401
from tests.integration.test_retour_remboursement import _vente, _credit


def _doc(app, lid):
    with app.app_context():
        return db.tables.livraisons.find_one({"_id": ObjectId(lid)})


def _nouveau_client(app, m, credit=0.0):
    with app.app_context():
        return str(db.tables.clients.insert_one({"nom": "Autre", "magasin_id": ObjectId(m["magasin"]),
                                                 "montant_en_caisse": credit}).inserted_id)


def _set_credit(app, cid, credit):
    with app.app_context():
        db.tables.clients.update_one({"_id": ObjectId(cid)}, {"$set": {"montant_en_caisse": credit}})


def _get_credit(app, cid):
    with app.app_context():
        return float(db.tables.clients.find_one({"_id": ObjectId(cid)}).get("montant_en_caisse") or 0)


def test_changement_de_client_rend_le_credit_sans_le_compter_paye(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    _set_credit(app, a["client"], 1000.0)
    # Vente 3000, client paie 2000 en espèces, 1000 pris sur son crédit
    lid = _vente(client, a, pid, 10, 2000, statut="partiellement payé")
    d = _doc(app, lid)
    autre = _nouveau_client(app, a)
    r = client.put(f"/admin_or_manager/livraisons/{lid}", json={"client_id": autre}, headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    d = _doc(app, lid)
    # Attendu : ancien client récupère 1000, la vente n'a plus que 2000 payés
    assert d["montant_paye"] == 2000, "les 1000 de crédit sont comptés comme payés ET rendus"


def test_restauration_refusee_apres_remboursement_especes(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _vente(client, a, pid, 10, 3000)
    client.delete(f"/livraisons/{lid}", json={"mode": "especes"}, headers=_h(a, "admin"))
    r = client.post(f"/livraisons/corbeille/{lid}/restaurer", headers=_h(a, "admin"))
    assert r.status_code == 400, "3000 rendus en espèces mais la vente restaurée reste payée"
    assert _stock_magasin(app, pid) == 20 and _doc(app, lid).get("deleted_at")


def test_validation_ristourne_solde_la_dette(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _vente(client, a, pid, 10, 2000, statut="partiellement payé")
    with app.app_context():  # le gérant a coché « a couché » : reste abandonné en attente
        db.tables.livraisons.update_one({"_id": ObjectId(lid)}, {"$set": {"have_ristourne": True, "ristourne": 1000}})
    r = client.post(f"/livraisons/valider-ristourne/{lid}", headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    d = _doc(app, lid)
    from services.client_service import dette_client
    with app.app_context():
        dette = dette_client(a["client"], a["magasin"])
    assert not (d["statut_paiement"] == "payé" and dette > 0), "« payé » mais dette toujours due"


def test_modification_qui_baisse_le_total_garde_le_trop_percu(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _vente(client, a, pid, 10, 3000)
    r = client.put(f"/admin_or_manager/livraisons/{lid}",
                   json={"produits": [{"produit_id": pid, "quantite": 5, "prix": 300}]}, headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    d = _doc(app, lid)
    assert (d.get("retour") or 0) + _credit(app, a) == 1500, "1500 payés en trop disparaissent"


def test_ristourne_baisse_ce_que_doit_le_client(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _vente(client, a, pid, 10, 1000, statut="partiellement payé")  # total 3000, payé 1000, reste 2000
    r = client.post(f"/livraisons/accorder-ristourne/{lid}", json={"montant": 500}, headers=_h(a, "manager"))
    d = _doc(app, lid)
    assert d["montant_total"] - d["montant_paye"] == 1500, "la ristourne ne baisse pas ce que doit le client"


def test_trop_paye_a_la_creation_refuse(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": pid, "quantite": 10, "prix": 300}], "statut_paiement": "payé",
        "paiements": [{"methode": "cash", "montant": 5000}], "client_id": a["client"]}, headers=_h(a, "manager"))
    assert r.status_code == 400, "2000 de trop enregistrés dans les paiements, ni crédités ni refusés"
    assert _stock_magasin(app, pid) == 20, "le stock réservé doit être libéré"


def test_ristourne_sur_vente_payee_devient_a_rembourser(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _vente(client, a, pid, 10, 3000)
    r = client.post(f"/livraisons/accorder-ristourne/{lid}", json={"montant": 500}, headers=_h(a, "manager"))
    d = _doc(app, lid)
    assert r.status_code == 200, r.get_json()
    assert d["montant_total"] == 2500 and d["montant_paye"] == 2500 and d["retour"] == 500
    assert d["statut_paiement"] == "payé" and d["montant_produits"] == 2500


def test_valider_sans_ristourne_en_attente_refuse(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _vente(client, a, pid, 10, 2000, statut="partiellement payé")
    with app.app_context():
        db.tables.livraisons.update_one({"_id": ObjectId(lid)}, {"$set": {"have_ristourne": False, "ristourne": 0}})
    r = client.post(f"/livraisons/valider-ristourne/{lid}", headers=_h(a, "admin"))
    assert r.status_code == 400
    assert _doc(app, lid)["statut_paiement"] == "partiellement payé"


def test_modif_reprend_le_meme_paiement_sans_doubler(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _vente(client, a, pid, 10, 3000)
    corps = {"produits": [{"produit_id": pid, "quantite": 5, "prix": 300}],
             "paiements": [{"methode": "cash", "montant": 3000}]}
    for _ in range(2):  # deux enregistrements du même formulaire
        assert client.put(f"/admin_or_manager/livraisons/{lid}", json=corps, headers=_h(a, "admin")).status_code == 200
    d = _doc(app, lid)
    assert d["retour"] == 1500 and d["montant_paye"] == 1500
    # Remboursement fait, puis nouvelle modification : rien de plus à rendre
    client.post(f"/livraisons/refund/{lid}", json={"mode": "especes"}, headers=_h(a, "admin"))
    assert client.put(f"/admin_or_manager/livraisons/{lid}", json=corps, headers=_h(a, "admin")).status_code == 200
    d = _doc(app, lid)
    assert d["retour"] == 0 and d["montant_paye"] == 1500 and d["statut_paiement"] == "payé"


def test_retour_d_une_vente_de_gerant_validee_par_un_manager_revient_au_gerant(client, app, magasins):
    from tests.integration.test_livraisons_magasin import _livraison_gerant
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _livraison_gerant(client, a, pid)  # 10 confiés au gérant, 3 vendus
    with app.app_context():  # une validation de ristourne inscrit le manager sur la vente
        db.tables.livraisons.update_one({"_id": ObjectId(lid)}, {"$set": {"manager_id": ObjectId(a["manager"])}})
    r = client.post("/livraisons/retour", json={"livraison_id": lid, "motif": "Défaut",
                                                "produits": [{"produit_id": pid, "quantite": 2}]},
                    headers=_h(a, "admin"))
    assert r.status_code in (200, 201), r.get_json()
    with app.app_context():
        stock = db.tables.stocks_livreur.find_one({"livreur_id": ObjectId(a["gerant"])})
    assert stock["quantite"] == 9 and _stock_magasin(app, pid) == 10
