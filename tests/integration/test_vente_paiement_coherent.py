"""Une vente « payé » doit correspondre à de l'argent réellement reçu et inscrit."""
from bson import ObjectId

from extensions import db
from tests.integration.test_livraisons_magasin import _h, _produit, magasins  # noqa: F401


def _vendre(client, m, produit_id, **paiement):
    return client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": produit_id, "quantite": 2, "prix": 500}],
        "client_id": m["client"], **paiement,
    }, headers=_h(m, "manager"))


def _vente(app, reponse):
    with app.app_context():
        return db.tables.livraisons.find_one({"_id": ObjectId(reponse.get_json()["livraison_id"])})


def test_paye_sans_rien_encaisser_est_refuse(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a)
    r = _vendre(client, a, pid, statut_paiement="payé", montant_paye=0, paiements=[])
    assert r.status_code == 400
    with app.app_context():
        assert db.tables.livraisons.count_documents({"magasin_id": ObjectId(a["magasin"])}) == 0


def test_montant_paye_sans_detail_est_inscrit_comme_paiement(client, app, magasins):
    a = magasins["A"]
    r = _vendre(client, a, _produit(client, a), statut_paiement="payé", montant_paye=1000, methode_paiement="om")
    assert r.status_code == 201, r.get_json()
    vente = _vente(app, r)
    assert vente["statut_paiement"] == "payé"
    assert vente["paiements"] == [{"methode": "om", "montant": 1000}]


def test_paiement_partiel_sans_detail_est_inscrit(client, app, magasins):
    a = magasins["A"]
    r = _vendre(client, a, _produit(client, a), statut_paiement="partiellement payé", montant_paye=400)
    vente = _vente(app, r)
    assert vente["statut_paiement"] == "partiellement payé"
    assert sum(p["montant"] for p in vente["paiements"]) == 400


def test_vente_a_credit_reste_non_payee(client, app, magasins):
    a = magasins["A"]
    r = _vendre(client, a, _produit(client, a), statut_paiement="non payé", montant_paye=0)
    vente = _vente(app, r)
    assert vente["statut_paiement"] == "non payé" and vente["paiements"] == []
