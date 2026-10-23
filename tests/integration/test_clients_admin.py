"""Page Clients de l'administrateur : liste, isolation des magasins, règles métier."""
import io
import os

from bson import ObjectId

from extensions import db
from tests.integration.test_livraisons_magasin import _h, _produit, magasins  # noqa: F401
from tests.integration.test_retour_remboursement import _vente


def _creer(client, m, **champs):
    data = {"nom": "Jean Test", "telephone": "690 11 22 33"}
    data.update(champs)
    return client.post("/clients/create", data=data, headers=_h(m, "admin"), content_type="multipart/form-data")


def test_creation_et_liste_complete(client, app, magasins):
    a = magasins["A"]
    r = _creer(client, a)
    assert r.status_code == 201, r.get_json()
    client_id = r.get_json()["client_id"]
    assert isinstance(client_id, str) and ObjectId.is_valid(client_id)  # plus d'objet imbriqué

    liste = client.get("/clients/manager/", headers=_h(a, "admin")).get_json()
    nouveau = next(c for c in liste if c["client_id"] == client_id)  # visible même sans vente
    assert nouveau["telephone"] == "+237690112233" and nouveau["nombre_livraisons"] == 0
    assert nouveau["montant_en_caisse"] == 0 and nouveau["dette"] == 0
    assert nouveau["created_at"][:4].isdigit()  # date ISO, lisible par le navigateur


def test_doublon_de_telephone_refuse(client, magasins):
    a = magasins["A"]
    assert _creer(client, a, telephone="699000001").status_code == 201
    r = _creer(client, a, nom="Autre nom", telephone="699 000 001")
    assert r.status_code == 409 and "Jean Test" in r.get_json()["message"]
    autre = _creer(client, a, nom="Paul", telephone="699000002").get_json()["client_id"]
    r = client.put(f"/clients/{autre}", json={"telephone": "699000001"}, headers=_h(a, "admin"))
    assert r.status_code == 409


def test_erreur_de_validation_lisible(client, magasins):
    r = _creer(client, magasins["A"], email="pas-un-email")
    assert r.status_code == 400 and "email" in r.get_json()["message"]


def test_photo_envoyee_sous_image_enregistree_avec_nom_unique(client, app, magasins):
    a = magasins["A"]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    r = client.post("/clients/create", data={"nom": "Photo", "image": (io.BytesIO(png), "photo.png")},
                    headers=_h(a, "admin"), content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    with app.app_context():
        photo = db.tables.clients.find_one({"_id": ObjectId(r.get_json()["client_id"])})["photo_profil"]
        chemin = os.path.join(app.config["UPLOAD_FOLDER"], photo)
    assert photo.startswith("client_") and photo.endswith(".png") and os.path.exists(chemin)
    os.remove(chemin)
    r = client.post("/clients/create", data={"nom": "Pdf", "image": (io.BytesIO(b"%PDF"), "doc.pdf")},
                    headers=_h(a, "admin"), content_type="multipart/form-data")
    assert r.status_code == 400


def test_client_d_un_autre_magasin_inaccessible(client, magasins):
    a, b = magasins["A"], magasins["B"]
    cid = a["client"]
    for appel in (
        lambda: client.get(f"/clients/{cid}", headers=_h(b, "admin")),
        lambda: client.put(f"/clients/{cid}", json={"nom": "Pirate"}, headers=_h(b, "admin")),
        lambda: client.delete(f"/clients/{cid}", headers=_h(b, "admin")),
        lambda: client.post(f"/clients/{cid}/ajouter-caisse", json={"montant": 1000}, headers=_h(b, "admin")),
        lambda: client.post(f"/clients/{cid}/retirer-caisse", json={"montant": 1}, headers=_h(b, "admin")),
        lambda: client.get(f"/clients/{cid}/livraisons", headers=_h(b, "admin")),
    ):
        assert appel().status_code == 404
    assert client.get("/clients/pas-un-id", headers=_h(a, "admin")).status_code == 404
    assert client.get(f"/clients/{cid}", headers=_h(a, "admin")).status_code == 200


def test_modification_ne_change_pas_le_solde(client, app, magasins):
    a = magasins["A"]
    cid = _creer(client, a, telephone="698000001").get_json()["client_id"]
    r = client.put(f"/clients/{cid}", json={"nom": "Jean Modifié", "montant_en_caisse": 999999}, headers=_h(a, "manager"))
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        c = db.tables.clients.find_one({"_id": ObjectId(cid)})
    assert c["nom"] == "Jean Modifié" and float(c.get("montant_en_caisse") or 0) == 0


def test_suppression_protegee(client, app, magasins):
    from models.client_model import ClientModel
    a = magasins["A"]
    with app.app_context():
        autres = ClientModel.get_or_create_default_client(a["magasin"])
    r = client.delete(f"/clients/{autres}", headers=_h(a, "admin"))
    assert r.status_code == 400 and "Autres" in r.get_json()["message"]

    # Solde positif sur le compte
    cid = _creer(client, a, telephone="697000001").get_json()["client_id"]
    client.post(f"/clients/{cid}/ajouter-caisse", json={"montant": 5000}, headers=_h(a, "admin"))
    r = client.delete(f"/clients/{cid}", headers=_h(a, "admin"))
    assert r.status_code == 400 and "compte" in r.get_json()["message"]
    client.post(f"/clients/{cid}/retirer-caisse", json={"montant": 5000}, headers=_h(a, "admin"))
    assert client.delete(f"/clients/{cid}", headers=_h(a, "admin")).status_code == 200

    # Dette en cours (vente non payée)
    produit_id = _produit(client, a, quantite=20)
    _vente(client, a, produit_id, 2, 0, statut="non payé")
    r = client.delete(f"/clients/{a['client']}", headers=_h(a, "admin"))
    assert r.status_code == 400 and "doit encore" in r.get_json()["message"]
    dette = next(c for c in client.get("/clients/manager/", headers=_h(a, "admin")).get_json()
                 if c["client_id"] == a["client"])["dette"]
    assert dette == 600


def test_pays_et_numero_international(client, magasins):
    a = magasins["A"]
    r = _creer(client, a, nom="Kouassi", telephone="+225 07 01 02 03 04", pays="ci")
    assert r.status_code == 201, r.get_json()
    cid = r.get_json()["client_id"]
    fiche = client.get(f"/clients/{cid}", headers=_h(a, "admin")).get_json()
    assert fiche["pays"] == "CI" and fiche["telephone"] == "+2250701020304"
    assert fiche["created_at"][:4].isdigit() and fiche["created_by_nom"]
    # Un ancien numéro sans indicatif est le même que sa forme internationale.
    _creer(client, a, nom="Ancien", telephone="677000001")
    assert _creer(client, a, nom="Doublon", telephone="+237 677 00 00 01").status_code == 409
    assert _creer(client, a, nom="Pays faux", telephone="677000009", pays="CMR").status_code == 400
    r = client.put(f"/clients/{cid}", json={"pays": "fr"}, headers=_h(a, "admin"))
    assert r.status_code == 200 and client.get(f"/clients/{cid}", headers=_h(a, "admin")).get_json()["pays"] == "FR"
