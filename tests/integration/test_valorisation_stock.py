"""Méthodes de valorisation des stocks : CMUP (défaut), FIFO, LIFO."""
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def magasin(app):
    with app.app_context():
        for table in ("magasin", "users", "produits", "approvisionnements", "stocks_livreur",
                      "livraisons", "clients", "retours_livraison"):
            db.tables[table].delete_many({})
        magasin_id = ObjectId()
        db.tables.magasin.insert_one({"_id": magasin_id, "denomination": "A", "is_active": True,
                                      "created_at": datetime.utcnow()})
        ids = {"magasin": str(magasin_id)}
        for role in ("admin", "manager", "gerant"):
            ids[role] = str(db.tables.users.insert_one({
                "name": role, "first_name": role, "email": f"{role}@a.test", "role": role,
                "magasin_id": magasin_id, "is_active": True, "created_at": datetime.utcnow(),
            }).inserted_id)
        yield ids


def _h(m, role):
    return {"Authorization": f"Bearer {generate_token(user_id=m[role], role=role)}"}


def _preparer_stock(client, m):
    """10 unités achetées à 100, puis 10 à 200 ; 15 remises au gérant."""
    admin = _h(m, "admin")
    r = client.post("/produits", data={"nom": "Riz", "prix": "300", "prix_achat": "100", "quantite": "10",
                                       "unite": "kg"}, headers=admin, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    produit_id = r.get_json()["produit_id"]
    r = client.post(f"/produits/{produit_id}/approvisionner", json={"quantite": 10, "prix_achat": 200}, headers=admin)
    assert r.status_code == 200, r.get_json()
    r = client.post("/approvisionnements", json={"livreur_id": m["gerant"],
                                                  "produits": [{"produit_id": produit_id, "quantite": 15}]}, headers=admin)
    assert r.status_code == 201, r.get_json()
    return produit_id


def _choisir(client, m, methode):
    r = client.put("/magasin/parametres/stock", json={"methode": methode}, headers=_h(m, "admin"))
    assert r.status_code == 200, r.get_json()


def _vendre_par_gerant(client, m, produit_id, quantite):
    r = client.post("/livraisons", json={"produits": [{"produit_id": produit_id, "quantite": quantite}],
                                          "statut_paiement": "payé", "montant_paye": 300 * quantite},
                    headers=_h(m, "gerant"))
    assert r.status_code == 201, r.get_json()
    livraison_id = r.get_json()["livraison_id"]
    with client.application.app_context():
        livraison = db.tables.livraisons.find_one({"_id": ObjectId(livraison_id)})
    return livraison_id, livraison["produits"][0]


def _valorisation(client, m, produit_id):
    r = client.get(f"/produits/{produit_id}/valorisation", headers=_h(m, "admin"))
    assert r.status_code == 200
    return r.get_json()


def test_cmup_est_la_methode_par_defaut(client, magasin):
    r = client.get("/magasin/parametres/stock", headers=_h(magasin, "gerant"))
    assert r.status_code == 200
    data = r.get_json()
    assert data["methode"] == "CMUP"
    assert {m["code"] for m in data["methodes"]} == {"CMUP", "FIFO", "LIFO"}


@pytest.mark.parametrize("methode, cout_unitaire, valeur_restante", [
    ("CMUP", 150.0, 8 * 150.0),          # (10×100 + 10×200) / 20
    ("FIFO", 1400 / 12, 8 * 200.0),      # 10×100 + 2×200 = 1 400, il reste 8 à 200
    ("LIFO", 2200 / 12, 8 * 100.0),      # 10×200 + 2×100, il reste 8 à 100
])
def test_la_livraison_est_valorisee_selon_la_methode(client, magasin, methode, cout_unitaire, valeur_restante):
    produit_id = _preparer_stock(client, magasin)
    _choisir(client, magasin, methode)
    _, ligne = _vendre_par_gerant(client, magasin, produit_id, 12)
    assert ligne["prix_achat"] == pytest.approx(cout_unitaire, abs=0.01)
    assert ligne["methode_valorisation"] == methode
    assert ligne["cout_achat_total"] == pytest.approx(cout_unitaire * 12, abs=0.05)
    etat = _valorisation(client, magasin, produit_id)
    assert etat["quantite_valorisee"] == pytest.approx(8)
    assert etat["valeur_stock"] == pytest.approx(valeur_restante, abs=0.05)


def test_changer_de_methode_s_applique_aux_ventes_suivantes(client, magasin):
    produit_id = _preparer_stock(client, magasin)
    _, premiere = _vendre_par_gerant(client, magasin, produit_id, 5)   # CMUP
    _choisir(client, magasin, "LIFO")
    _, seconde = _vendre_par_gerant(client, magasin, produit_id, 5)
    assert premiere["prix_achat"] == pytest.approx(150)
    # Après la 1re vente CMUP (prorata), il reste 7,5 à 100 et 7,5 à 200 : LIFO prend les 200.
    assert seconde["prix_achat"] == pytest.approx(200)
    with client.application.app_context():
        anciennes = [l["produits"][0]["prix_achat"] for l in db.tables.livraisons.find({})]
    assert sorted(anciennes) == pytest.approx([150, 200])


def test_vente_directe_par_un_manager(client, magasin):
    produit_id = _preparer_stock(client, magasin)  # 5 restent au magasin
    _choisir(client, magasin, "FIFO")
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": produit_id, "quantite": 5}], "statut_paiement": "payé", "montant_paye": 1500,
    }, headers=_h(magasin, "manager"))
    assert r.status_code == 201, r.get_json()
    with client.application.app_context():
        ligne = db.tables.livraisons.find_one({"_id": ObjectId(r.get_json()["livraison_id"])})["produits"][0]
    assert ligne["prix_achat"] == pytest.approx(100)


def test_annuler_une_livraison_remet_les_lots(client, magasin):
    produit_id = _preparer_stock(client, magasin)
    _choisir(client, magasin, "FIFO")
    livraison_id, _ = _vendre_par_gerant(client, magasin, produit_id, 12)
    r = client.delete(f"/livraisons/{livraison_id}", headers=_h(magasin, "gerant"))
    assert r.status_code == 200, r.get_json()
    etat = _valorisation(client, magasin, produit_id)
    assert etat["quantite_valorisee"] == pytest.approx(20)
    assert etat["valeur_stock"] == pytest.approx(3000)


def test_retour_client_remet_les_articles_a_leur_cout(client, magasin):
    produit_id = _preparer_stock(client, magasin)
    _choisir(client, magasin, "FIFO")
    livraison_id, _ = _vendre_par_gerant(client, magasin, produit_id, 12)  # 10×100 + 2×200
    r = client.post("/livraisons/retour", json={
        "livraison_id": livraison_id, "livreur_id": magasin["gerant"],
        "produits": [{"produit_id": produit_id, "quantite": 2}],
    }, headers=_h(magasin, "admin"))
    assert r.status_code in (200, 201), r.get_json()
    etat = _valorisation(client, magasin, produit_id)
    # Les 2 derniers sortis (à 200) reviennent : 8 + 2 à 200 = 2000.
    assert etat["quantite_valorisee"] == pytest.approx(10)
    assert etat["valeur_stock"] == pytest.approx(2000)


def test_avarie_valorisee(client, magasin):
    produit_id = _preparer_stock(client, magasin)
    _choisir(client, magasin, "LIFO")
    r = client.post(f"/produits/{produit_id}/perime", json={"quantite": 3}, headers=_h(magasin, "admin"))
    assert r.status_code == 200, r.get_json()
    mouvement = client.get(f"/produits/{produit_id}/mouvements", headers=_h(magasin, "admin")).get_json()["mouvements"][-1]
    assert mouvement["valeur_perte"] == pytest.approx(600)


def test_seul_l_admin_change_la_methode(client, magasin):
    r = client.put("/magasin/parametres/stock", json={"methode": "LIFO"}, headers=_h(magasin, "manager"))
    assert r.status_code == 403
    r = client.put("/magasin/parametres/stock", json={"methode": "XYZ"}, headers=_h(magasin, "admin"))
    assert r.status_code == 400
    assert client.get("/magasin/parametres/stock", headers=_h(magasin, "admin")).get_json()["methode"] == "CMUP"


def test_produit_existant_sans_lots_est_initialise(client, app, magasin):
    with app.app_context():
        produit_id = str(db.tables.produits.insert_one({
            "nom": "Ancien", "prix": 300.0, "prix_achat": 80.0, "unite": "kg", "quantite": 4, "etat": "en stock",
            "magasin_id": ObjectId(magasin["magasin"]), "mouvements_stock": [], "conditionnements": [],
            "created_at": datetime.utcnow(),
        }).inserted_id)
    etat = _valorisation(client, magasin, produit_id)
    assert etat["quantite_valorisee"] == pytest.approx(4)
    assert etat["valeur_stock"] == pytest.approx(320)
