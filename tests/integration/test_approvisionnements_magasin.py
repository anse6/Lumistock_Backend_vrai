"""Approvisionnements : isolation par magasin, détails et modification."""
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def magasins(app):
    with app.app_context():
        for table in ("magasin", "users", "produits", "approvisionnements", "stocks_livreur"):
            db.tables[table].delete_many({})
        result = {}
        for nom in ("A", "B"):
            magasin_id = ObjectId()
            db.tables.magasin.insert_one({"_id": magasin_id, "denomination": nom, "is_active": True,
                                          "created_at": datetime.utcnow()})
            ids = {"magasin": str(magasin_id)}
            for role in ("admin", "manager", "gerant"):
                ids[role] = str(db.tables.users.insert_one({
                    "name": f"Nom{role}", "first_name": f"Prenom{role}{nom}", "email": f"{role}@{nom.lower()}.test",
                    "role": role, "magasin_id": magasin_id, "is_active": True, "telephone": "+237600000000",
                    "created_at": datetime.utcnow(),
                }).inserted_id)
            ids["produit"] = str(db.tables.produits.insert_one({
                "nom": f"Riz {nom}", "prix": 100.0, "prix_achat": 60.0, "unite": "kg", "quantite": 10,
                "etat": "en stock", "magasin_id": magasin_id, "mouvements_stock": [], "conditionnements": [],
                "created_at": datetime.utcnow(),
            }).inserted_id)
            result[nom] = ids
        yield result


def _h(user_id, role):
    return {"Authorization": f"Bearer {generate_token(user_id=user_id, role=role)}"}


def _creer(client, m, quantite=3, headers=None):
    response = client.post("/approvisionnements", json={
        "livreur_id": m["gerant"], "produits": [{"produit_id": m["produit"], "quantite": quantite}],
    }, headers=headers or _h(m["admin"], "admin"))
    assert response.status_code == 201, response.get_json()
    return response.get_json()["approvisionnement_id"]


def _stock(app, produit_id):
    with app.app_context():
        return db.tables.produits.find_one({"_id": ObjectId(produit_id)})["quantite"]


def test_details_renvoient_un_objet_complet(client, magasins):
    a = magasins["A"]
    appro_id = _creer(client, a)
    response = client.get(f"/approvisionnements/{appro_id}", headers=_h(a["admin"], "admin"))
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, dict)
    assert data["livreur_id"] == a["gerant"]
    assert (data["first_name"], data["last_name"]) == ("PrenomgerantA", "Nomgerant")
    assert data["livreur"]["nom_complet"] == "PrenomgerantA Nomgerant"
    assert data["approvisionne_par"]["nom_complet"] == "PrenomadminA Nomadmin"
    assert data["approvisionne_par"]["role"] == "Administrateur"
    produit = data["produits"][0]
    assert (produit["prix_achat"], produit["unite"]) == (60.0, "kg")
    assert data["montant"] == 300 and data["montant_achat"] == 180


def test_details_inconnus_repondent_404(client, magasins):
    response = client.get(f"/approvisionnements/{ObjectId()}", headers=_h(magasins["A"]["admin"], "admin"))
    assert response.status_code == 404


def test_un_manager_voit_les_approvisionnements_de_l_admin(client, magasins):
    a = magasins["A"]
    appro_id = _creer(client, a)
    liste = client.get("/approvisionnements", headers=_h(a["manager"], "manager")).get_json()
    assert [x["id"] for x in liste] == [appro_id]


def test_modification_garde_l_auteur_et_trace_le_modificateur(client, app, magasins):
    a = magasins["A"]
    appro_id = _creer(client, a, quantite=3)
    response = client.put(f"/approvisionnements/{appro_id}", json={
        "livreur_id": a["gerant"], "produits": [{"produit_id": a["produit"], "quantite": 5}],
    }, headers=_h(a["manager"], "manager"))
    assert response.status_code == 200, response.get_json()
    assert _stock(app, a["produit"]) == 5  # 10 - 5

    data = client.get(f"/approvisionnements/{appro_id}", headers=_h(a["admin"], "admin")).get_json()
    assert data["quantite"] == 5
    assert data["approvisionne_par"]["id"] == a["admin"]
    assert data["modifie_par"]["id"] == a["manager"]
    # Il reste visible dans la liste de l'admin qui l'a créé.
    assert [x["id"] for x in client.get("/approvisionnements", headers=_h(a["admin"], "admin")).get_json()] == [appro_id]


def test_reduire_un_produit_dont_le_stock_magasin_est_epuise(client, app, magasins):
    a = magasins["A"]
    appro_id = _creer(client, a, quantite=10)
    assert _stock(app, a["produit"]) == 0
    response = client.put(f"/approvisionnements/{appro_id}", json={
        "livreur_id": a["gerant"], "produits": [{"produit_id": a["produit"], "quantite": 4}],
    }, headers=_h(a["admin"], "admin"))
    assert response.status_code == 200, response.get_json()
    assert _stock(app, a["produit"]) == 6


@pytest.mark.parametrize("method, url, kwargs", [
    ("get", "/approvisionnements/{id}", {}),
    ("delete", "/approvisionnements/{id}", {}),
    ("put", "/approvisionnements/{id}", {"json": {"livreur_id": "{gerant_b}", "produits": []}}),
])
def test_un_autre_magasin_ne_peut_pas_toucher_a_l_approvisionnement(client, app, magasins, method, url, kwargs):
    a, b = magasins["A"], magasins["B"]
    appro_id = _creer(client, a)
    if "json" in kwargs:
        kwargs = {"json": {**kwargs["json"], "livreur_id": b["gerant"]}}
    response = getattr(client, method)(url.format(id=appro_id), headers=_h(b["admin"], "admin"), **kwargs)
    assert response.status_code == 404
    assert client.get("/approvisionnements", headers=_h(b["admin"], "admin")).get_json() == []
    assert _stock(app, a["produit"]) == 7


def test_impossible_d_utiliser_le_gerant_ou_les_produits_d_un_autre_magasin(client, app, magasins):
    a, b = magasins["A"], magasins["B"]
    headers = _h(a["admin"], "admin")
    response = client.post("/approvisionnements", json={
        "livreur_id": b["gerant"], "produits": [{"produit_id": a["produit"], "quantite": 1}]}, headers=headers)
    assert response.status_code == 400
    response = client.post("/approvisionnements", json={
        "livreur_id": a["gerant"], "produits": [{"produit_id": b["produit"], "quantite": 1}]}, headers=headers)
    assert response.status_code == 400
    assert _stock(app, b["produit"]) == 10
