"""Isolation entre magasins des fournisseurs et des catégories."""
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def magasins(app):
    with app.app_context():
        for table in ("magasin", "users", "produits", "categories", "fournisseurs", "factures_fournisseurs"):
            db.tables[table].delete_many({})
        result = {}
        for nom in ("A", "B"):
            magasin_id = ObjectId()
            db.tables.magasin.insert_one({"_id": magasin_id, "denomination": nom, "is_active": True,
                                          "created_at": datetime.utcnow()})
            users = {}
            for role in ("admin", "gerant"):
                users[role] = str(db.tables.users.insert_one({
                    "name": role, "first_name": nom, "email": f"{role}@{nom}.test", "role": role,
                    "magasin_id": magasin_id, "is_active": True, "created_at": datetime.utcnow(),
                }).inserted_id)
            fournisseur = db.tables.fournisseurs.insert_one(
                {"nom": f"Fournisseur {nom}", "magasin_id": magasin_id, "created_at": datetime.utcnow()})
            categorie = db.tables.categories.insert_one(
                {"nom": f"Catégorie {nom}", "magasin_id": magasin_id, "created_at": datetime.utcnow()})
            result[nom] = {"id": magasin_id, "fournisseur": str(fournisseur.inserted_id),
                           "categorie": str(categorie.inserted_id), **users}
        yield result


def _headers(magasins, nom, role="admin"):
    return {"Authorization": f"Bearer {generate_token(user_id=magasins[nom][role], role=role)}"}


# --------------------------------------------------------------------------- fournisseurs
@pytest.mark.parametrize("method, kwargs", [
    ("get", {}), ("put", {"json": {"nom": "Piraté"}}), ("delete", {}),
])
def test_un_autre_magasin_ne_touche_pas_au_fournisseur(client, magasins, method, kwargs):
    fid = magasins["A"]["fournisseur"]
    response = getattr(client, method)(f"/fournisseurs/{fid}", headers=_headers(magasins, "B"), **kwargs)
    assert response.status_code == 404
    with client.application.app_context():
        assert db.tables.fournisseurs.find_one({"_id": ObjectId(fid)})["nom"] == "Fournisseur A"


def test_un_gerant_ne_modifie_ni_ne_supprime_un_fournisseur(client, magasins):
    fid = magasins["A"]["fournisseur"]
    gerant = _headers(magasins, "A", "gerant")
    assert client.put(f"/fournisseurs/{fid}", json={"nom": "X"}, headers=gerant).status_code == 403
    assert client.delete(f"/fournisseurs/{fid}", headers=gerant).status_code == 403


def test_l_admin_gere_son_fournisseur(client, magasins):
    fid = magasins["A"]["fournisseur"]
    admin = _headers(magasins, "A")
    assert client.get(f"/fournisseurs/{fid}", headers=admin).status_code == 200
    assert client.put(f"/fournisseurs/{fid}", json={"ville": "Douala"}, headers=admin).status_code == 200
    assert client.delete(f"/fournisseurs/{fid}", headers=admin).status_code == 200
    with client.application.app_context():
        assert db.tables.fournisseurs.find_one({"_id": ObjectId(fid)}) is None


def test_un_fournisseur_facture_ne_peut_pas_etre_supprime(client, magasins):
    fid = magasins["A"]["fournisseur"]
    with client.application.app_context():
        db.tables.factures_fournisseurs.insert_one(
            {"magasin_id": magasins["A"]["id"], "fournisseur_id": ObjectId(fid), "montant_ttc": 1000})
    assert client.delete(f"/fournisseurs/{fid}", headers=_headers(magasins, "A")).status_code == 409
    with client.application.app_context():
        assert db.tables.fournisseurs.find_one({"_id": ObjectId(fid)}) is not None


# --------------------------------------------------------------------------- catégories
@pytest.mark.parametrize("method, kwargs", [
    ("get", {}), ("put", {"json": {"nom": "Piraté"}}), ("delete", {}),
])
def test_un_autre_magasin_ne_touche_pas_a_la_categorie(client, magasins, method, kwargs):
    cid = magasins["A"]["categorie"]
    response = getattr(client, method)(f"/categorie/{cid}", headers=_headers(magasins, "B"), **kwargs)
    assert response.status_code == 404
    with client.application.app_context():
        assert db.tables.categories.find_one({"_id": ObjectId(cid)})["nom"] == "Catégorie A"


def test_categorie_inexistante_repond_404(client, magasins):
    admin = _headers(magasins, "A")
    absent = str(ObjectId())
    assert client.get(f"/categorie/{absent}", headers=admin).status_code == 404
    assert client.delete(f"/categorie/{absent}", headers=admin).status_code == 404


def test_l_admin_gere_sa_categorie(client, magasins):
    cid = magasins["A"]["categorie"]
    admin = _headers(magasins, "A")
    assert client.put(f"/categorie/{cid}", json={"nom": "Boissons"}, headers=admin).status_code == 200
    assert client.get(f"/categorie/{cid}", headers=admin).get_json()["nom"] == "Boissons"
    assert client.delete(f"/categorie/{cid}", headers=admin).status_code == 200


def test_categorie_avec_produits_ne_peut_pas_etre_supprimee(client, magasins):
    cid = magasins["A"]["categorie"]
    with client.application.app_context():
        db.tables.produits.insert_one({"nom": "Riz", "categorie_id": ObjectId(cid), "magasin_id": magasins["A"]["id"]})
    assert client.delete(f"/categorie/{cid}", headers=_headers(magasins, "A")).status_code == 409


def test_nom_de_categorie_deja_pris(client, magasins):
    admin = _headers(magasins, "A")
    with client.application.app_context():
        db.tables.categories.insert_one({"nom": "Boissons", "magasin_id": magasins["A"]["id"]})
    response = client.put(f"/categorie/{magasins['A']['categorie']}", json={"nom": "Boissons"}, headers=admin)
    assert response.status_code == 409


# --------------------------------------------------------------------------- listes et droits
def test_listes_avec_nombre_de_produits(client, magasins):
    a = magasins["A"]
    with client.application.app_context():
        db.tables.produits.insert_many([
            {"nom": "Riz", "categorie_id": ObjectId(a["categorie"]), "fournisseur_id": ObjectId(a["fournisseur"]),
             "magasin_id": a["id"]},
            {"nom": "Sucre", "categorie_id": ObjectId(a["categorie"]), "magasin_id": a["id"],
             "deleted_at": datetime.utcnow()},  # corbeille : non compté
        ])
    admin = _headers(magasins, "A")
    categories = client.get("/categorie/", headers=admin).get_json()
    fournisseurs = client.get("/fournisseurs", headers=admin).get_json()
    assert [(c["nom"], c["nb_produits"]) for c in categories] == [("Catégorie A", 1)]
    assert [(f["nom"], f["nb_produits"]) for f in fournisseurs] == [("Fournisseur A", 1)]


def test_un_gerant_ne_modifie_pas_une_categorie(client, magasins):
    cid = magasins["A"]["categorie"]
    gerant = _headers(magasins, "A", "gerant")
    assert client.put(f"/categorie/{cid}", json={"nom": "X"}, headers=gerant).status_code == 403
    assert client.delete(f"/categorie/{cid}", headers=gerant).status_code == 403


def test_nom_de_fournisseur_deja_pris(client, magasins):
    with client.application.app_context():
        db.tables.fournisseurs.insert_one({"nom": "Sodeco", "magasin_id": magasins["A"]["id"]})
    response = client.put(f"/fournisseurs/{magasins['A']['fournisseur']}", json={"nom": "Sodeco"},
                          headers=_headers(magasins, "A"))
    assert response.status_code == 409


# --------------------------------------------------------------------------- session glissante
def _refresh(client, user_id, jours):
    from datetime import timedelta
    from flask_jwt_extended import create_refresh_token
    with client.application.app_context():
        jeton = create_refresh_token(identity=user_id, expires_delta=timedelta(days=jours))
    return client.post("/auth/refresh", headers={"Authorization": f"Bearer {jeton}"})


def test_refresh_recent_ne_change_pas_de_refresh_token(client, magasins):
    r = _refresh(client, magasins["A"]["admin"], 6)
    assert r.status_code == 200 and "access_token" in r.get_json() and "refresh_token" not in r.get_json()


def test_refresh_ancien_est_renouvele(client, magasins):
    r = _refresh(client, magasins["A"]["admin"], 1)
    assert r.status_code == 200 and r.get_json().get("refresh_token")
