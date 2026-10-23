"""Livraisons : isolation entre magasins/gérants et stock des livraisons d'un manager."""
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def magasins(app):
    with app.app_context():
        for table in ("magasin", "users", "produits", "approvisionnements", "stocks_livreur",
                      "livraisons", "clients", "retours_livraison"):
            db.tables[table].delete_many({})
        result = {}
        for nom in ("A", "B"):
            magasin_id = ObjectId()
            db.tables.magasin.insert_one({"_id": magasin_id, "denomination": nom, "is_active": True,
                                          "created_at": datetime.utcnow()})
            ids = {"magasin": str(magasin_id)}
            for role in ("admin", "manager", "gerant", "gerant2"):
                ids[role] = str(db.tables.users.insert_one({
                    "name": role, "first_name": nom, "email": f"{role}@{nom.lower()}.test",
                    "role": "gerant" if role == "gerant2" else role, "magasin_id": magasin_id,
                    "is_active": True, "created_at": datetime.utcnow(),
                }).inserted_id)
            ids["client"] = str(db.tables.clients.insert_one({
                "nom": f"Client {nom}", "magasin_id": magasin_id, "montant_en_caisse": 0.0,
                "created_at": datetime.utcnow(),
            }).inserted_id)
            result[nom] = ids
        yield result


def _h(m, role):
    vrai_role = "gerant" if role == "gerant2" else role
    return {"Authorization": f"Bearer {generate_token(user_id=m[role], role=vrai_role)}"}


def _produit(client, m, quantite=20):
    r = client.post("/produits", data={"nom": "Riz", "prix": "300", "prix_achat": "100", "quantite": str(quantite),
                                       "unite": "kg"}, headers=_h(m, "admin"), content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    return r.get_json()["produit_id"]


def _stock_magasin(app, produit_id):
    with app.app_context():
        return db.tables.produits.find_one({"_id": ObjectId(produit_id)})["quantite"]


def _livraison_gerant(client, m, produit_id):
    r = client.post("/approvisionnements", json={"livreur_id": m["gerant"],
                                                  "produits": [{"produit_id": produit_id, "quantite": 10}]},
                    headers=_h(m, "admin"))
    assert r.status_code == 201, r.get_json()
    r = client.post("/livraisons", json={"produits": [{"produit_id": produit_id, "quantite": 3}],
                                          "statut_paiement": "payé", "montant_paye": 900, "client_id": m["client"]},
                    headers=_h(m, "gerant"))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["livraison_id"]


def _livraison_manager(client, m, produit_id, quantite=4):
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": produit_id, "quantite": quantite}], "statut_paiement": "payé",
        "montant_paye": 300 * quantite, "client_id": m["client"],
    }, headers=_h(m, "manager"))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["livraison_id"]


@pytest.mark.parametrize("method, url, kwargs", [
    ("get", "/livraisons/{id}", {}),
    ("delete", "/livraisons/{id}", {}),
    ("post", "/livraisons/ajouter-paiement/{id}", {"json": {"montant": 100}}),
    ("post", "/livraisons/refund/{id}", {}),
    ("post", "/livraisons/valider-ristourne/{id}", {}),
    ("post", "/livraisons/accorder-ristourne/{id}", {"json": {"montant": 50}}),
])
def test_un_autre_magasin_ne_peut_pas_toucher_a_la_livraison(client, app, magasins, method, url, kwargs):
    a, b = magasins["A"], magasins["B"]
    produit_id = _produit(client, a)
    livraison_id = _livraison_gerant(client, a, produit_id)
    response = getattr(client, method)(url.format(id=livraison_id), headers=_h(b, "admin"), **kwargs)
    assert response.status_code == 404
    with app.app_context():
        assert "deleted_at" not in db.tables.livraisons.find_one({"_id": ObjectId(livraison_id)})


def test_un_gerant_ne_voit_que_ses_propres_livraisons(client, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a)
    livraison_id = _livraison_gerant(client, a, produit_id)
    assert client.get(f"/livraisons/{livraison_id}", headers=_h(a, "gerant")).status_code == 200
    assert client.get(f"/livraisons/{livraison_id}", headers=_h(a, "gerant2")).status_code == 404
    assert client.delete(f"/livraisons/{livraison_id}", headers=_h(a, "gerant2")).status_code == 404
    # L'admin du magasin y a accès.
    assert client.get(f"/livraisons/{livraison_id}", headers=_h(a, "admin")).status_code == 200


def test_retour_sur_la_livraison_d_un_autre_magasin_refuse(client, magasins):
    a, b = magasins["A"], magasins["B"]
    produit_id = _produit(client, a)
    livraison_id = _livraison_gerant(client, a, produit_id)
    r = client.post("/livraisons/retour", json={"livraison_id": livraison_id, "livreur_id": a["gerant"],
                                                "produits": [{"produit_id": produit_id, "quantite": 1}]},
                    headers=_h(b, "admin"))
    assert r.status_code == 404


def test_creation_avec_produit_ou_client_d_un_autre_magasin_refusee(client, app, magasins):
    a, b = magasins["A"], magasins["B"]
    produit_b = _produit(client, b)
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": produit_b, "quantite": 1}], "statut_paiement": "payé", "montant_paye": 300,
    }, headers=_h(a, "manager"))
    assert r.status_code == 400
    assert _stock_magasin(app, produit_b) == 20
    produit_a = _produit(client, a)
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": produit_a, "quantite": 1}], "statut_paiement": "payé", "montant_paye": 300,
        "client_id": b["client"],
    }, headers=_h(a, "manager"))
    assert r.status_code == 400


def test_modifier_par_le_manager_une_livraison_de_gerant_est_refuse(client, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a)
    livraison_id = _livraison_gerant(client, a, produit_id)
    r = client.put(f"/admin_or_manager/livraisons/{livraison_id}", json={
        "produits": [{"produit_id": produit_id, "quantite": 1}]}, headers=_h(a, "manager"))
    assert r.status_code == 409


def test_corbeille_d_une_livraison_manager_rend_le_stock_au_magasin(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _livraison_manager(client, a, produit_id, quantite=4)
    assert _stock_magasin(app, produit_id) == 16

    # Remboursée sur le compte du client : la vente reste restaurable.
    r = client.delete(f"/livraisons/{livraison_id}", json={"mode": "credit_client"}, headers=_h(a, "manager"))
    assert r.status_code == 200, r.get_json()
    assert _stock_magasin(app, produit_id) == 20
    with app.app_context():
        # Rien n'a été déposé dans un « stock gérant » du manager.
        assert db.tables.stocks_livreur.find_one({"livreur_id": ObjectId(a["manager"])}) is None

    r = client.post(f"/livraisons/corbeille/{livraison_id}/restaurer", headers=_h(a, "manager"))
    assert r.status_code == 200, r.get_json()
    assert _stock_magasin(app, produit_id) == 16

    client.delete(f"/livraisons/{livraison_id}", headers=_h(a, "manager"))
    r = client.delete(f"/livraisons/corbeille/{livraison_id}/definitive", headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    assert _stock_magasin(app, produit_id) == 20


def test_corbeille_d_une_livraison_gerant_rend_le_stock_au_gerant(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    livraison_id = _livraison_gerant(client, a, produit_id)
    r = client.delete(f"/livraisons/{livraison_id}", headers=_h(a, "gerant"))
    assert r.status_code == 200
    with app.app_context():
        stock = db.tables.stocks_livreur.find_one({"livreur_id": ObjectId(a["gerant"])})
    assert stock["quantite"] == 10  # 10 remis, 3 livrés, 3 rendus
    assert _stock_magasin(app, produit_id) == 10
