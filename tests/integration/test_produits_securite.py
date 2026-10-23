"""Isolation entre magasins et cohérence des réponses des API produit."""
import io
import os
from datetime import datetime

import pandas as pd
import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


@pytest.fixture
def magasins(app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with app.app_context():
        for table in ("magasin", "users", "produits", "categories"):
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
            result[nom] = {"id": str(magasin_id), **users}
        yield result


def _headers(user_id, role):
    return {"Authorization": f"Bearer {generate_token(user_id=user_id, role=role)}"}


def _create(client, headers, nom, **extra):
    data = {"nom": nom, "prix": "100", "prix_achat": "50", "quantite": "10", "unite": "pièce", **extra}
    return client.post("/produits", data=data, headers=headers, content_type="multipart/form-data")


@pytest.fixture
def produit_a(client, magasins):
    response = _create(client, _headers(magasins["A"]["admin"], "admin"), "Riz")
    assert response.status_code == 201
    return response.get_json()["produit_id"]


@pytest.mark.parametrize("method, url, kwargs", [
    ("get", "/produits/{id}", {}),
    ("put", "/produits/{id}", {"data": {"prix": "1"}, "content_type": "multipart/form-data"}),
    ("delete", "/produits/{id}", {}),
    ("post", "/produits/{id}/approvisionner", {"json": {"quantite": 5}}),
    ("post", "/produits/{id}/perime", {"json": {"quantite": 1}}),
    ("get", "/produits/{id}/mouvements", {}),
])
def test_un_autre_magasin_ne_peut_pas_toucher_au_produit(client, magasins, produit_a, method, url, kwargs):
    headers = _headers(magasins["B"]["admin"], "admin")
    response = getattr(client, method)(url.format(id=produit_a), headers=headers, **kwargs)
    assert response.status_code == 404
    with client.application.app_context():
        produit = db.tables.produits.find_one({"_id": ObjectId(produit_a)})
    assert produit["prix"] == 100 and produit["quantite"] == 10 and "deleted_at" not in produit


def test_un_autre_magasin_ne_peut_pas_restaurer_ni_purger(client, magasins, produit_a):
    admin_a = _headers(magasins["A"]["admin"], "admin")
    admin_b = _headers(magasins["B"]["admin"], "admin")
    assert client.delete(f"/produits/{produit_a}", headers=admin_a).status_code == 200
    assert client.post(f"/produits/corbeille/{produit_a}/restaurer", headers=admin_b).status_code == 404
    assert client.delete(f"/produits/corbeille/{produit_a}/definitive", headers=admin_b).status_code == 404
    assert client.post(f"/produits/corbeille/{produit_a}/restaurer", headers=admin_a).status_code == 200


def test_un_gerant_ne_peut_pas_modifier_un_produit(client, magasins, produit_a):
    response = client.put(f"/produits/{produit_a}", data={"prix": "1"},
                          headers=_headers(magasins["A"]["gerant"], "gerant"),
                          content_type="multipart/form-data")
    assert response.status_code == 403


def test_le_gerant_lit_les_produits_de_son_magasin(client, magasins, produit_a):
    response = client.get(f"/produits/{produit_a}", headers=_headers(magasins["A"]["gerant"], "gerant"))
    assert response.status_code == 200


def test_doublon_de_nom_renvoie_409(client, magasins, produit_a):
    response = _create(client, _headers(magasins["A"]["admin"], "admin"), "Riz")
    assert response.status_code == 409
    assert "existe déjà" in response.get_json()["error"]


def test_codes_barres_uniques_par_magasin(client, magasins):
    admin_a = _headers(magasins["A"]["admin"], "admin")
    assert _create(client, admin_a, "Sucre", code_barre="123").status_code == 201
    assert _create(client, admin_a, "Sel", code_barre="123").status_code == 409
    sel = _create(client, admin_a, "Sel", code_barre="456").get_json()["produit_id"]
    response = client.put(f"/produits/{sel}", data={"code_barre": "123"}, headers=admin_a,
                          content_type="multipart/form-data")
    assert response.status_code == 409
    # Le même code reste libre pour un autre magasin.
    assert _create(client, _headers(magasins["B"]["admin"], "admin"), "Sucre", code_barre="123").status_code == 201


def test_reponses_coherentes_pour_un_produit_inexistant(client, magasins):
    admin_a = _headers(magasins["A"]["admin"], "admin")
    inconnu = str(ObjectId())
    assert client.get(f"/produits/{inconnu}", headers=admin_a).status_code == 404
    assert client.delete(f"/produits/{inconnu}", headers=admin_a).status_code == 404
    response = client.post(f"/produits/{inconnu}/approvisionner", json={"quantite": 5}, headers=admin_a)
    assert response.status_code == 404
    assert isinstance(response.get_json(), dict)


def test_approvisionnement_renvoie_un_objet_et_refuse_un_corps_vide(client, magasins, produit_a):
    admin_a = _headers(magasins["A"]["admin"], "admin")
    response = client.post(f"/produits/{produit_a}/approvisionner", json={"quantite": 5}, headers=admin_a)
    assert response.status_code == 200
    assert response.get_json()["produit"]["quantite"] == 15
    assert client.post(f"/produits/{produit_a}/approvisionner", headers=admin_a).status_code == 400


def test_un_produit_a_la_corbeille_ne_peut_pas_etre_approvisionne(client, magasins, produit_a):
    admin_a = _headers(magasins["A"]["admin"], "admin")
    client.delete(f"/produits/{produit_a}", headers=admin_a)
    response = client.post(f"/produits/{produit_a}/approvisionner", json={"quantite": 5}, headers=admin_a)
    assert response.status_code == 404


def test_mouvements_du_produit_et_filtre_par_type(client, magasins, produit_a):
    admin_a = _headers(magasins["A"]["admin"], "admin")
    client.post(f"/produits/{produit_a}/approvisionner", json={"quantite": 5}, headers=admin_a)
    client.post(f"/produits/{produit_a}/perime", json={"quantite": 1}, headers=admin_a)
    tous = client.get(f"/produits/{produit_a}/mouvements", headers=admin_a)
    assert tous.status_code == 200 and len(tous.get_json()["mouvements"]) == 2
    filtres = client.get(f"/produits/{produit_a}/mouvements?type=produit périmé", headers=admin_a)
    assert [m["type"] for m in filtres.get_json()["mouvements"]] == ["produit périmé"]


def test_upload_refuse_un_fichier_qui_n_est_pas_une_image(client, magasins):
    admin_a = _headers(magasins["A"]["admin"], "admin")
    response = _create(client, admin_a, "Lait",
                       image=(io.BytesIO(b"<script>alert(1)</script>"), "page.html", "text/html"))
    assert response.status_code == 400
    deguise = _create(client, admin_a, "Lait",
                      image=(io.BytesIO(b"<script>alert(1)</script>"), "photo.png", "image/png"))
    assert deguise.status_code == 400
    assert os.listdir(client.application.config["UPLOAD_FOLDER"]) == []


def test_deux_images_de_meme_nom_ne_s_ecrasent_pas(client, magasins):
    _create(client, _headers(magasins["A"]["admin"], "admin"), "Huile",
            image=(io.BytesIO(PNG + b"A"), "photo.png", "image/png"))
    _create(client, _headers(magasins["B"]["admin"], "admin"), "Savon",
            image=(io.BytesIO(PNG + b"B"), "photo.png", "image/png"))
    with client.application.app_context():
        images = {p["nom"]: p["image"] for p in db.tables.produits.find({})}
    assert images["Huile"] != images["Savon"]
    assert "photo" not in images["Huile"]
    with open(os.path.join(client.application.config["UPLOAD_FOLDER"], images["Huile"]), "rb") as handle:
        assert handle.read().endswith(b"A")


def test_import_utilise_les_categories_du_magasin(client, magasins):
    with client.application.app_context():
        db.tables.categories.insert_one({"nom": "Boissons", "magasin_id": ObjectId(magasins["B"]["id"]),
                                         "created_at": datetime.utcnow()})
    buffer = io.BytesIO()
    pd.DataFrame([{"Nom": "Jus", "Prix": 10, "Prix d'achat": 5, "Unité": "L", "Quantité": 3,
                   "Catégorie": "Boissons"}]).to_excel(buffer, index=False)
    buffer.seek(0)
    response = client.post("/produits/import", data={"fichier": (buffer, "produits.xlsx")},
                           headers=_headers(magasins["A"]["admin"], "admin"),
                           content_type="multipart/form-data")
    assert response.status_code == 201
    assert response.get_json()["produits_crees"] == ["Jus"]
    with client.application.app_context():
        produit = db.tables.produits.find_one({"nom": "Jus"})
        categorie = db.tables.categories.find_one({"_id": produit["categorie_id"]})
    assert str(categorie["magasin_id"]) == magasins["A"]["id"]


def test_mode_code_barre_obligatoire_a_la_creation(client, magasins):
    with client.application.app_context():
        db.tables.users.update_one({"_id": ObjectId(magasins["A"]["admin"])}, {"$set": {"barcode_mode": True}})
    response = _create(client, _headers(magasins["A"]["admin"], "admin"), "Café")
    assert response.status_code == 400


def test_liste_des_produits_resume_les_avaries(client, magasins, produit_a):
    admin_a = _headers(magasins["A"]["admin"], "admin")
    _create(client, admin_a, "Sucre")
    client.post(f"/produits/{produit_a}/perime", json={"quantite": 2, "type_avarie": "perime"}, headers=admin_a)
    client.post(f"/produits/{produit_a}/perime",
                json={"quantite": 1, "type_avarie": "casse", "description": "Carton tombé", "responsable": "Paul"},
                headers=admin_a)

    produits = {p["nom"]: p for p in client.get("/produits", headers=admin_a).get_json()}
    avarie = produits["Riz"]["avarie"]
    assert avarie["quantite_totale"] == 3
    assert avarie["nombre"] == 2
    assert avarie["dernier_type"] == "casse"
    assert avarie["derniere_description"] == "Carton tombé"
    assert produits["Riz"]["quantite"] == 7
    assert produits["Sucre"]["avarie"] is None


def test_la_declaration_d_avarie_conserve_le_motif(client, magasins, produit_a):
    admin_a = _headers(magasins["A"]["admin"], "admin")
    client.post(f"/produits/{produit_a}/perime",
                json={"quantite": 1, "type_avarie": "vol", "description": "Rayon vide", "responsable": "Awa"},
                headers=admin_a)
    mouvement = client.get(f"/produits/{produit_a}/mouvements", headers=admin_a).get_json()["mouvements"][-1]
    assert mouvement["type"] == "produit périmé"
    assert (mouvement["type_avarie"], mouvement["description"], mouvement["responsable"]) == ("vol", "Rayon vide", "Awa")
