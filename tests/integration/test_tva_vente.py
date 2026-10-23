"""TVA des ventes selon le pays du magasin."""
from datetime import datetime, timedelta

from bson import ObjectId

from extensions import db
from services.tva_service import code_pays, TAUX_TVA
from tests.integration.test_livraisons_magasin import _h, _produit, _stock_magasin, magasins  # noqa: F401
from tests.integration.test_retour_remboursement import _livraison


def _pays(app, m, pays="Cameroun", code="CM"):
    with app.app_context():
        db.tables.magasin.update_one({"_id": ObjectId(m["magasin"])}, {"$set": {"pays": pays, "country_code": code}})


def _vente_tva(client, m, produit_id, quantite, **extra):
    corps = {"produits": [{"produit_id": produit_id, "quantite": quantite, "prix": 1000}],
             "statut_paiement": "non payé", "montant_paye": 0, "client_id": m["client"], "appliquer_tva": True}
    corps.update(extra)
    return client.post("/admin_or_manager/livraisons", json=corps, headers=_h(m, "manager"))


def test_code_pays_depuis_le_nom():
    assert code_pays({"pays": "Côte d'Ivoire"}) == "CI"
    assert code_pays({"pays": "Cameroun"}) == "CM"
    assert code_pays({"country_code": "ga", "pays": "Cameroun"}) == "GA"
    assert TAUX_TVA["CM"] == 19.25


def test_tva_du_magasin_et_vente_camerounaise(client, app, magasins):
    a = magasins["A"]
    _pays(app, a)
    info = client.get("/livraisons/tva", headers=_h(a, "admin")).get_json()
    assert info == {"pays": "Cameroun", "code": "CM", "taux": 19.25, "decimales": 0}

    produit_id = _produit(client, a, quantite=20)
    r = _vente_tva(client, a, produit_id, 10, reductions=[{"type": "remise", "mode": "pourcentage", "valeur": 10}])
    assert r.status_code == 201, r.get_json()
    v = _livraison(client, a, r.get_json()["livraison_id"])
    # 10 000 brut, remise 10 % -> 9 000 HT, TVA 19,25 % = 1 732,50 -> 1 733 (pas de centimes en CFA)
    assert v["montant_produits"] == 9000 and v["appliquer_tva"] is True and v["taux_tva"] == 19.25
    assert v["montant_tva"] == 1733 and v["montant_total"] == 10733

    # Sans TVA : rien ne change
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": produit_id, "quantite": 1, "prix": 1000}], "statut_paiement": "payé",
        "montant_paye": 1000, "client_id": a["client"]}, headers=_h(a, "manager"))
    v2 = _livraison(client, a, r.get_json()["livraison_id"])
    assert v2["appliquer_tva"] is False and v2["montant_tva"] == 0 and v2["montant_total"] == 1000


def test_tva_refusee_si_pays_inconnu(client, app, magasins):
    a = magasins["A"]
    _pays(app, a, pays="Atlantide", code="")
    produit_id = _produit(client, a, quantite=20)
    r = _vente_tva(client, a, produit_id, 2)
    assert r.status_code == 400 and "pays du magasin" in str(r.get_json()["error"])
    assert _stock_magasin(app, produit_id) == 20  # aucun effet de bord


def test_retour_et_modification_recalculent_la_tva(client, app, magasins):
    a = magasins["A"]
    _pays(app, a)
    produit_id = _produit(client, a, quantite=20)
    r = _vente_tva(client, a, produit_id, 10, statut_paiement="payé", montant_paye=11925)
    livraison_id = r.get_json()["livraison_id"]
    v = _livraison(client, a, livraison_id)
    assert v["montant_total"] == 11925 and v["montant_paye"] == 11925

    # Retour de 2 articles : 8 000 HT + 1 540 TVA = 9 540 ; trop-perçu 2 385 TTC
    r = client.post("/livraisons/retour", json={"livraison_id": livraison_id, "motif": "Défaut",
                                                 "produits": [{"produit_id": produit_id, "quantite": 2}]},
                    headers=_h(a, "admin"))
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["montant_retour"] == 2385 and r.get_json()["montant_a_rembourser"] == 2385
    v = _livraison(client, a, livraison_id)
    assert v["montant_tva"] == 1540 and v["montant_total"] == 9540

    # Le magasin change de pays : la vente garde son taux
    _pays(app, a, pays="Gabon", code="GA")
    r = client.put(f"/admin_or_manager/livraisons/{livraison_id}", json={"dette_ancienne": 0}, headers=_h(a, "manager"))
    assert r.status_code == 200, r.get_json()
    assert _livraison(client, a, livraison_id)["taux_tva"] == 19.25

    # TVA retirée
    r = client.put(f"/admin_or_manager/livraisons/{livraison_id}", json={"appliquer_tva": False}, headers=_h(a, "manager"))
    assert r.status_code == 200, r.get_json()
    v = _livraison(client, a, livraison_id)
    assert v["appliquer_tva"] is False and v["montant_tva"] == 0 and v["montant_total"] == 8000


def test_chiffre_d_affaires_hors_tva(client, app, magasins):
    from services import compta_service
    a = magasins["A"]
    _pays(app, a)
    produit_id = _produit(client, a, quantite=20)
    _vente_tva(client, a, produit_id, 10)
    with app.app_context():
        d = compta_service.synthese(a["magasin"], datetime.now() - timedelta(days=1), datetime.now() + timedelta(days=1))
    assert d["indicateurs"]["chiffre_affaires"] == 10000
    assert d["tva"]["collectee"] == 1925


def test_arrondi_au_franc_pres():
    from services.reductions_service import appliquer_reductions
    from services.tva_service import arrondir, calculer_tva, decimales_devise
    assert arrondir(936112.5, 0) == 936113 and arrondir(2.5, 0) == 3  # pas d'arrondi au pair
    assert decimales_devise(None) == 0 and decimales_devise("XAF") == 0 and decimales_devise("EUR") == 2
    assert calculer_tva(9000, 19.25, 0) == 1733 and calculer_tva(9000, 19.25, 2) == 1732.5
    detail, total = appliquer_reductions([{"type": "remise", "mode": "pourcentage", "valeur": 7.5}], 999, 0)
    assert detail[0]["montant"] == 75 and total == 75  # 74,925 -> 75


def test_vente_tva_et_remise_en_francs_entiers(client, app, magasins):
    a = magasins["A"]
    _pays(app, a)
    produit_id = _produit(client, a, quantite=20)
    r = _vente_tva(client, a, produit_id, 3, reductions=[{"type": "remise", "mode": "pourcentage", "valeur": 7.5}])
    v = _livraison(client, a, r.get_json()["livraison_id"])
    # 3 000 - 225 = 2 775 HT ; TVA 534,19 -> 534 ; total 3 309
    assert v["total_reductions"] == 225 and v["montant_tva"] == 534 and v["montant_total"] == 3309
    assert float(v["montant_total"]).is_integer()
