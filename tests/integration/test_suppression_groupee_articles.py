"""Suppression de plusieurs articles à la fois (cases à cocher de la liste)."""
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def magasins(app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with app.app_context():
        for table in ("magasin", "users", "produits"):
            db.tables[table].delete_many({})
        result = {}
        for nom in ("A", "B"):
            magasin_id = ObjectId()
            db.tables.magasin.insert_one({"_id": magasin_id, "denomination": nom, "is_active": True,
                                          "created_at": datetime.utcnow()})
            users = {}
            for role in ("admin", "manager", "gerant"):
                users[role] = str(db.tables.users.insert_one({
                    "name": role, "first_name": nom, "email": f"{role}@{nom}.test", "role": role,
                    "magasin_id": magasin_id, "is_active": True, "created_at": datetime.utcnow(),
                }).inserted_id)
            result[nom] = {"id": str(magasin_id), **users}
        yield result


def _h(m, role):
    return {"Authorization": f"Bearer {generate_token(user_id=m[role], role=role)}"}


def _creer(client, m, nom):
    r = client.post("/produits", data={"nom": nom, "prix": "100", "prix_achat": "50", "quantite": "5",
                                       "unite": "pièce"}, headers=_h(m, "admin"), content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    return r.get_json()["produit_id"]


def _supprimer(client, m, ids, role="admin"):
    return client.post("/produits/supprimer-plusieurs", json={"ids": ids}, headers=_h(m, role))


def _en_corbeille(client, produit_id):
    with client.application.app_context():
        return "deleted_at" in db.tables.produits.find_one({"_id": ObjectId(produit_id)})


def test_plusieurs_articles_vont_a_la_corbeille(client, magasins):
    a = magasins["A"]
    ids = [_creer(client, a, f"Article {i}") for i in range(3)]
    garde = _creer(client, a, "Gardé")
    r = _supprimer(client, a, ids + [ids[0]])  # un doublon est ignoré
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["supprimes"] == 3
    assert all(_en_corbeille(client, i) for i in ids)
    assert not _en_corbeille(client, garde)
    # Restaurables depuis la corbeille, comme une suppression simple.
    corbeille = client.get("/produits/corbeille", headers=_h(a, "admin")).get_json()
    assert len(corbeille if isinstance(corbeille, list) else corbeille.get("produits", [])) == 3


def test_un_article_d_un_autre_magasin_fait_tout_refuser(client, magasins):
    a, b = magasins["A"], magasins["B"]
    a1 = _creer(client, a, "A1")
    b1 = _creer(client, b, "B1")
    r = _supprimer(client, a, [a1, b1])
    assert r.status_code == 404
    assert r.get_json()["introuvables"] == [b1]
    assert not _en_corbeille(client, a1) and not _en_corbeille(client, b1)


def test_article_deja_en_corbeille_refuse(client, magasins):
    a = magasins["A"]
    a1, a2 = _creer(client, a, "A1"), _creer(client, a, "A2")
    assert _supprimer(client, a, [a1]).status_code == 200
    r = _supprimer(client, a, [a1, a2])
    assert r.status_code == 404 and not _en_corbeille(client, a2)


@pytest.mark.parametrize("ids", [[], None, "abc", ["pas-un-id"]])
def test_selection_invalide(client, magasins, ids):
    assert _supprimer(client, magasins["A"], ids).status_code == 400


def test_droits(client, magasins):
    a = magasins["A"]
    a1 = _creer(client, a, "A1")
    assert _supprimer(client, a, [a1], role="gerant").status_code == 403
    # Le manager n'a pas ce droit par défaut ; l'admin peut le lui donner.
    assert _supprimer(client, a, [a1], role="manager").status_code == 403
    assert not _en_corbeille(client, a1)
    r = client.put("/magasin/permissions", json={"droits": {"produits.supprimer": {"manager": True}}},
                   headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    assert _supprimer(client, a, [a1], role="manager").status_code == 200
