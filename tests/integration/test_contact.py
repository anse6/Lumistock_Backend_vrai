"""Formulaire de contact public et boîte de réception du staff."""
from unittest.mock import patch

from extensions import db
from utils.rate_limit import tout_reinitialiser
from tests.integration.test_auth import _bearer, _login, setup_auth  # noqa: F401
from tests.integration.test_auth import MOT_DE_PASSE

MESSAGE = {
    "nom": "Awa Mballa", "email": "Awa@Exemple.cm", "telephone": "+237 6 90 12 34 56",
    "entreprise": "Quincaillerie du Marché", "sujet": "tarifs",
    "message": "Bonjour, combien coûte Lumistock pour deux caissiers ?", "consentement": True,
}


def _staff(client):
    r = client.post("/auth/login", json={"email": "staff@test.com", "password": MOT_DE_PASSE})
    return _bearer(r.get_json()["access_token"])


def _vider(app):
    with app.app_context():
        db.tables.messages_contact.delete_many({})


def test_envoi_public_puis_traitement_par_le_staff(client, app, setup_auth):
    _vider(app)
    r = client.post("/contact", json=MESSAGE)
    assert r.status_code == 201, r.get_json()

    h = _staff(client)
    liste = client.get("/contact/messages", headers=h).get_json()
    assert liste["compteurs"]["nouveau"] == 1 and liste["compteurs"]["non_lus"] == 1
    m = liste["messages"][0]
    assert m["email"] == "awa@exemple.cm" and m["sujet_libelle"] == "Tarifs et licence" and m["lu"] is False
    assert client.get("/contact/messages/non-lus", headers=h).get_json()["non_lus"] == 1

    detail = client.get(f"/contact/messages/{m['id']}", headers=h).get_json()
    assert detail["message"].startswith("Bonjour") and detail["lu"] is True
    assert client.get("/contact/messages/non-lus", headers=h).get_json()["non_lus"] == 0

    r = client.patch(f"/contact/messages/{m['id']}", json={"statut": "en_cours", "note": "Rappeler mardi"}, headers=h)
    assert r.status_code == 200 and r.get_json()["note"] == "Rappeler mardi"
    assert client.patch(f"/contact/messages/{m['id']}", json={"statut": "inconnu"}, headers=h).status_code == 400

    with patch("utils.email._envoyer", return_value=True) as envoi:
        r = client.post(f"/contact/messages/{m['id']}/repondre", json={"texte": "Bonjour Awa, voici notre offre."}, headers=h)
    assert r.status_code == 200, r.get_json()
    assert envoi.call_args[0][1] == "awa@exemple.cm"
    data = r.get_json()
    assert data["statut"] == "repondu" and len(data["reponses"]) == 1

    # Échec d'envoi : rien n'est ajouté à l'historique.
    with patch("utils.email._envoyer", return_value=False):
        r = client.post(f"/contact/messages/{m['id']}/repondre", json={"texte": "Relance"}, headers=h)
    assert r.status_code == 502
    assert len(client.get(f"/contact/messages/{m['id']}", headers=h).get_json()["reponses"]) == 1

    assert client.get("/contact/messages?q=quincaillerie", headers=h).get_json()["messages"]
    assert not client.get("/contact/messages?q=introuvable", headers=h).get_json()["messages"]

    assert client.delete(f"/contact/messages/{m['id']}", headers=h).status_code == 200
    assert client.get(f"/contact/messages/{m['id']}", headers=h).status_code == 404
    _vider(app)


def test_validation_du_formulaire(client, app, setup_auth):
    _vider(app)
    for changement, champ in (
        ({"consentement": False}, "consentement"),
        ({"email": "pas-un-email"}, "email"),
        ({"nom": ""}, "nom"),
        ({"sujet": "spam"}, "sujet"),
        ({"message": "court"}, "message"),
        ({"telephone": "abc"}, "telephone"),
    ):
        tout_reinitialiser()  # chaque essai compte dans la limite par IP
        r = client.post("/contact", json={**MESSAGE, **changement})
        assert r.status_code == 400, changement
        assert champ in r.get_json()["errors"], changement
    assert client.post("/contact", json={**MESSAGE, "site_web_societe": "http://spam"}).status_code == 400
    with app.app_context():
        assert db.tables.messages_contact.find_one({}) is None


def test_messages_limites_par_adresse_ip(client, app, setup_auth):
    _vider(app)
    statuts = [client.post("/contact", json=MESSAGE).status_code for _ in range(6)]
    assert statuts[:5] == [201] * 5 and statuts[5] == 429
    _vider(app)


def test_boite_de_reception_reservee_au_staff(client, app, setup_auth):
    h_admin = _bearer(_login(client).get_json()["access_token"])
    assert client.get("/contact/messages", headers=h_admin).status_code == 403
    assert client.get("/contact/messages").status_code == 401
