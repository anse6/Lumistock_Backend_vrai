"""Codes-barres : recherche par scan (produit ou carton), association d'un code
lu par le lecteur, codes internes EAN-13 et unicité dans le magasin."""
import json
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from models.produit_model import cle_ean13
from utils.jwt_token import generate_token


@pytest.fixture
def magasin(app):
    with app.app_context():
        for table in ("magasin", "users", "produits"):
            db.tables[table].delete_many({})
        ids = {}
        for nom in ("A", "B"):
            magasin_id = ObjectId()
            db.tables.magasin.insert_one({"_id": magasin_id, "denomination": nom, "is_active": True,
                                          "created_at": datetime.utcnow()})
            ids[f"magasin_{nom}"] = str(magasin_id)
            for role in ("admin", "manager", "caissier"):
                cle = role if nom == "A" else f"{role}_B"
                ids[cle] = str(db.tables.users.insert_one({
                    "name": role, "first_name": role, "email": f"{role}@{nom}.test", "role": role,
                    "magasin_id": magasin_id, "is_active": True, "created_at": datetime.utcnow(),
                }).inserted_id)
        yield ids


def _h(m, cle):
    role = cle.split("_")[0]
    return {"Authorization": f"Bearer {generate_token(user_id=m[cle], role=role)}"}


def _creer(client, m, nom, code=None, conditionnements=None, cle="admin", quantite="50"):
    data = {"nom": nom, "prix": "1000", "prix_achat": "700", "quantite": quantite, "unite": "pièce"}
    if code:
        data["code_barre"] = code
    if conditionnements:
        data["conditionnements"] = json.dumps(conditionnements)
    r = client.post("/produits", data=data, headers=_h(m, cle), content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    return r.get_json()["produit_id"]


def test_scan_retrouve_le_produit(client, magasin):
    pid = _creer(client, magasin, "Lait 400 g", code="6001087003142")
    r = client.get("/produits/code-barre/6001087003142", headers=_h(magasin, "admin"))
    assert r.status_code == 200
    corps = r.get_json()
    assert corps["_id"] == pid and corps["conditionnement_scanne"] is None
    assert "mouvements_stock" not in corps


def test_scan_caissier_sans_prix_achat(client, magasin):
    _creer(client, magasin, "Lait 400 g", code="6001087003142")
    r = client.get("/caisse/code-barre/6001087003142", headers=_h(magasin, "caissier"))
    assert r.status_code == 200
    assert "prix_achat" not in r.get_json()


def test_code_inconnu_et_autre_magasin(client, magasin):
    _creer(client, magasin, "Lait 400 g", code="6001087003142")
    assert client.get("/produits/code-barre/999", headers=_h(magasin, "admin")).status_code == 404
    # Le même code n'existe pas pour le magasin B.
    assert client.get("/produits/code-barre/6001087003142", headers=_h(magasin, "admin_B")).status_code == 404


def test_upc_a_et_ean13_equivalents(client, magasin):
    _creer(client, magasin, "Céréales", code="012345678905")  # UPC-A (12 chiffres)
    r = client.get("/produits/code-barre/0012345678905", headers=_h(magasin, "admin"))
    assert r.status_code == 200 and r.get_json()["nom"] == "Céréales"


def test_code_interne_ean13_valide_et_unique(client, magasin):
    codes = set()
    for _ in range(20):
        r = client.get("/produits/generate-barcode", headers=_h(magasin, "admin"))
        code = r.get_json()["code_barre"]
        assert len(code) == 13 and code.isdigit() and code.startswith("200")
        assert code[-1] == cle_ean13(code[:12])
        codes.add(code)
    assert len(codes) == 20


def test_associer_un_code_lu(client, app, magasin):
    pid = _creer(client, magasin, "Savon")
    r = client.put(f"/produits/{pid}/code-barre", json={"code_barre": " 6009612470017 "},
                   headers=_h(magasin, "admin"))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["code_barre"] == "6009612470017", "les espaces autour sont retirés"
    assert client.get("/produits/code-barre/6009612470017", headers=_h(magasin, "admin")).get_json()["_id"] == pid

    # Code déjà pris par un autre produit : refusé.
    autre = _creer(client, magasin, "Savon liquide")
    r = client.put(f"/produits/{autre}/code-barre", json={"code_barre": "6009612470017"}, headers=_h(magasin, "admin"))
    assert r.status_code == 409

    # Retirer le code.
    r = client.put(f"/produits/{pid}/code-barre", json={"code_barre": ""}, headers=_h(magasin, "admin"))
    assert r.status_code == 200
    with app.app_context():
        assert db.tables.produits.find_one({"_id": ObjectId(pid)})["code_barre"] is None


def test_associer_reserve_aux_responsables_du_magasin(client, magasin):
    pid = _creer(client, magasin, "Savon")
    corps = {"code_barre": "6009612470017"}
    assert client.put(f"/produits/{pid}/code-barre", json=corps, headers=_h(magasin, "caissier")).status_code == 403
    assert client.put(f"/produits/{pid}/code-barre", json=corps, headers=_h(magasin, "admin_B")).status_code == 404
    assert client.put(f"/produits/{pid}/code-barre", json=corps, headers=_h(magasin, "manager")).status_code == 200


def test_code_du_carton(client, app, magasin):
    pid = _creer(client, magasin, "Eau 1,5 L", code="6111111111111",
                 conditionnements=[{"nom": "Pack de 6", "facteur": 6, "prix_conditionnement": 5500}])
    with app.app_context():
        cond_id = str(db.tables.produits.find_one({"_id": ObjectId(pid)})["conditionnements"][0]["_id"])

    # Le code du produit ne peut pas servir au carton.
    r = client.put(f"/produits/{pid}/code-barre", json={"code_barre": "6111111111111", "conditionnement_id": cond_id},
                   headers=_h(magasin, "admin"))
    assert r.status_code == 409

    r = client.put(f"/produits/{pid}/code-barre", json={"code_barre": "6111111111128", "conditionnement_id": cond_id},
                   headers=_h(magasin, "admin"))
    assert r.status_code == 200, r.get_json()

    corps = client.get("/produits/code-barre/6111111111128", headers=_h(magasin, "admin")).get_json()
    assert corps["_id"] == pid
    assert corps["conditionnement_scanne"]["_id"] == cond_id
    assert corps["conditionnement_scanne"]["facteur"] == 6

    # Un autre produit ne peut pas reprendre le code du carton.
    r = client.post("/produits", data={"nom": "Eau 0,5 L", "prix": "300", "prix_achat": "200", "quantite": "5", "unite": "pièce",
                                       "code_barre": "6111111111128"},
                    headers=_h(magasin, "admin"), content_type="multipart/form-data")
    assert r.status_code == 409, r.get_json()

    # Modifier le produit (et ses conditionnements) garde le code du carton.
    with app.app_context():
        conds = db.tables.produits.find_one({"_id": ObjectId(pid)})["conditionnements"]
    r = client.put(f"/produits/{pid}", data={
        "nom": "Eau 1,5 L", "prix": "1000",
        "conditionnements": json.dumps([{"_id": str(c["_id"]), "nom": c["nom"], "facteur": c["facteur"],
                                         "prix_conditionnement": 5400} for c in conds]),
    }, headers=_h(magasin, "admin"), content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    corps = client.get("/produits/code-barre/6111111111128", headers=_h(magasin, "admin")).get_json()
    assert corps["conditionnement_scanne"]["prix_conditionnement"] == 5400


def test_generer_les_codes_manquants(client, app, magasin):
    sans = _creer(client, magasin, "Beignets")
    avec = _creer(client, magasin, "Lait", code="6001087003142")
    r = client.post("/produits/codes-barres/generer", json={"produit_ids": [sans, avec, str(ObjectId())]},
                    headers=_h(magasin, "admin"))
    assert r.status_code == 200
    generes = r.get_json()["generes"]
    assert [g["produit_id"] for g in generes] == [sans], "le produit qui a déjà un code n'est pas touché"
    with app.app_context():
        assert db.tables.produits.find_one({"_id": ObjectId(avec)})["code_barre"] == "6001087003142"
        assert db.tables.produits.find_one({"_id": ObjectId(sans)})["code_barre"].startswith("200")
    assert client.post("/produits/codes-barres/generer", json={"produit_ids": "x"},
                       headers=_h(magasin, "admin")).status_code == 400
