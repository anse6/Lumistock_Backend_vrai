"""Support interne entre l'administrateur d'un magasin et le staff Lumistock."""
from datetime import datetime, timedelta
from unittest.mock import patch

from bson import ObjectId

from extensions import db
from utils.rate_limit import tout_reinitialiser
from tests.integration.test_auth import _bearer, bcrypt, setup_auth  # noqa: F401
from tests.integration.test_auth import MOT_DE_PASSE


def _connexion(client, email):
    r = client.post("/auth/login", json={"email": email, "password": MOT_DE_PASSE})
    return _bearer(r.get_json()["access_token"])


def _vider(app):
    tout_reinitialiser()
    with app.app_context():
        db.tables.support_conversations.delete_many({})
        db.tables.support_messages.delete_many({})


def _autre_admin(app):
    """Administrateur d'un second magasin."""
    with app.app_context():
        magasin_id = ObjectId()
        db.tables.magasin.insert_one({"_id": magasin_id, "denomination": "Autre Boutique", "is_active": True,
                                      "created_at": datetime.utcnow()})
        db.tables.users.insert_one({
            "name": "Ekane", "first_name": "Marc", "email": "autre-admin@test.com", "role": "admin",
            "password": bcrypt.generate_password_hash(MOT_DE_PASSE).decode("utf-8"),
            "is_active": True, "magasin_id": magasin_id, "created_at": datetime.utcnow(),
        })


def test_admin_ouvre_une_conversation_et_le_staff_repond(client, app, setup_auth):
    _vider(app)
    ha, hs = _connexion(client, "admin@test.com"), _connexion(client, "staff@test.com")

    with patch("utils.email._envoyer", return_value=True) as envoi:
        r = client.post("/support/conversations", json={"sujet": "Import Excel", "texte": "Mon fichier est refusé."}, headers=ha)
        assert r.status_code == 201, r.get_json()
        conv = r.get_json()
        assert conv["initie_par"] == "admin" and conv["magasin_nom"] == "Test Magasin"
        # Le staff est prévenu par email.
        assert envoi.call_count == 1 and envoi.call_args[0][1] == "staff@test.com"

        # Deuxième message avant que le staff ait regardé : pas de nouvel email.
        client.post(f"/support/conversations/{conv['id']}/messages", json={"texte": "C'est urgent."}, headers=ha)
        assert envoi.call_count == 1

    assert client.get("/support/non-lus", headers=hs).get_json()["non_lus"] == 1
    detail = client.get(f"/support/conversations/{conv['id']}", headers=hs).get_json()
    assert [m["auteur"] for m in detail["messages"]] == ["admin", "admin"]
    assert client.get("/support/non-lus", headers=hs).get_json()["non_lus"] == 0

    # L'administrateur a quitté la page depuis 10 minutes : la réponse lui est envoyée par email.
    with app.app_context():
        db.tables.support_conversations.update_one({"_id": ObjectId(conv["id"])}, {"$set": {"vu_admin_le": datetime.utcnow() - timedelta(minutes=10)}})
    with patch("utils.email._envoyer", return_value=True) as envoi:
        r = client.post(f"/support/conversations/{conv['id']}/messages", json={"texte": "Je regarde tout de suite."}, headers=hs)
        assert r.status_code == 201
        assert envoi.call_args[0][1] == "admin@test.com"
    vue = client.get(f"/support/conversations/{conv['id']}", headers=ha).get_json()
    assert vue["staff_nom"] == "Test staff_id" and vue["messages"][-1]["auteur_nom"] == "Test staff_id"

    # Fermer puis écrire rouvre la conversation.
    assert client.patch(f"/support/conversations/{conv['id']}", json={"statut": "fermee"}, headers=ha).get_json()["statut"] == "fermee"
    with patch("utils.email._envoyer", return_value=True):
        r = client.post(f"/support/conversations/{conv['id']}/messages", json={"texte": "Encore une chose."}, headers=ha)
    assert r.get_json()["statut"] == "ouverte"


def test_staff_choisit_l_administrateur(client, app, setup_auth):
    _vider(app)
    _autre_admin(app)
    hs = _connexion(client, "staff@test.com")
    liste = client.get("/support/admins", headers=hs).get_json()["admins"]
    assert {a["email"] for a in liste} >= {"admin@test.com", "autre-admin@test.com"}
    autre = next(a for a in liste if a["email"] == "autre-admin@test.com")
    assert autre["magasin_nom"] == "Autre Boutique"
    assert [a["email"] for a in client.get("/support/admins?q=boutique", headers=hs).get_json()["admins"]] == ["autre-admin@test.com"]

    assert client.post("/support/conversations", json={"sujet": "Bonjour", "texte": "Test"}, headers=hs).status_code == 400
    with patch("utils.email._envoyer", return_value=True) as envoi:
        r = client.post("/support/conversations", json={"admin_id": autre["id"], "sujet": "Votre licence", "texte": "Bonjour Marc !"}, headers=hs)
    assert r.status_code == 201 and r.get_json()["initie_par"] == "staff"
    assert envoi.call_args[0][1] == "autre-admin@test.com"
    conv_id = r.get_json()["id"]

    # Le destinataire la voit ; l'administrateur d'un autre magasin, non.
    hb = _connexion(client, "autre-admin@test.com")
    assert client.get("/support/conversations", headers=hb).get_json()["compteurs"]["non_lus"] == 1
    ha = _connexion(client, "admin@test.com")
    assert client.get("/support/conversations", headers=ha).get_json()["conversations"] == []
    assert client.get(f"/support/conversations/{conv_id}", headers=ha).status_code == 404
    assert client.post(f"/support/conversations/{conv_id}/messages", json={"texte": "intrus"}, headers=ha).status_code == 404


def test_acces_reserve(client, app, setup_auth):
    _vider(app)
    ha = _connexion(client, "admin@test.com")
    assert client.get("/support/admins", headers=ha).status_code == 403
    assert client.get("/support/conversations").status_code == 401
