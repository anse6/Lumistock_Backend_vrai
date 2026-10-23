"""Chat du site public avec Nansé : visiteur identifié, réponses de l'IA,
alerte du staff à la 3e question pertinente, reprise par un conseiller."""
import json
from unittest.mock import patch

from extensions import db
from services.ia_contexte import IANonConfiguree
from utils.rate_limit import tout_reinitialiser
from tests.integration.test_auth import _bearer, setup_auth  # noqa: F401
from tests.integration.test_auth import MOT_DE_PASSE


def _ia(reponse="Voici la réponse.", pertinente=True, conseiller=False):
    return json.dumps({"reponse": reponse, "pertinente": pertinente, "demande_conseiller": conseiller})


def _connexion(client, email):
    r = client.post("/auth/login", json={"email": email, "password": MOT_DE_PASSE})
    return _bearer(r.get_json()["access_token"])


def _vider(app):
    tout_reinitialiser()
    with app.app_context():
        db.tables.assistant_conversations.delete_many({})
        db.tables.assistant_messages.delete_many({})


def _demarrer(client, **extra):
    r = client.post("/assistant/conversations", json={"nom": "Awa Mballa", "email": "Awa@Exemple.cm", **extra})
    assert r.status_code == 201, r.get_json()
    data = r.get_json()
    return data["id"], {"X-Assistant-Jeton": data["jeton"]}


def _ecrire(client, conv_id, h, texte):
    return client.post(f"/assistant/conversations/{conv_id}/messages", json={"texte": texte}, headers=h)


def test_identification_et_jeton(client, app, setup_auth):
    _vider(app)
    r = client.post("/assistant/conversations", json={"nom": "A", "email": "awa@exemple.cm"})
    assert r.status_code == 400 and "nom" in r.get_json()["errors"]
    r = client.post("/assistant/conversations", json={"nom": "Awa", "email": "pas-un-email"})
    assert r.status_code == 400 and "email" in r.get_json()["errors"]

    conv_id, h = _demarrer(client)
    with app.app_context():
        conv = db.tables.assistant_conversations.find_one({})
        assert conv["email"] == "awa@exemple.cm" and conv["jeton"] != h["X-Assistant-Jeton"]  # seule l'empreinte est gardée
    assert client.get(f"/assistant/conversations/{conv_id}", headers=h).status_code == 200
    assert client.get(f"/assistant/conversations/{conv_id}", headers={"X-Assistant-Jeton": "faux"}).status_code == 404
    assert client.get(f"/assistant/conversations/{conv_id}").status_code == 404


def test_trois_questions_pertinentes_puis_conseiller(client, app, setup_auth):
    _vider(app)
    conv_id, h = _demarrer(client)

    with patch("services.assistant_service.appeler_ia") as ia, patch("utils.email._envoyer", return_value=True) as envoi:
        ia.return_value = _ia("Bonjour Awa !", pertinente=False)
        r = _ecrire(client, conv_id, h, "Bonjour")
        assert r.status_code == 201
        data = r.get_json()
        assert [m["auteur"] for m in data["nouveaux"]] == ["visiteur", "ia"]
        assert data["nouveaux"][1]["auteur_nom"] == "Nansé" and data["statut"] == "ia"

        ia.return_value = _ia("Le tarif dépend de la licence.")
        _ecrire(client, conv_id, h, "Combien coûte Lumistock pour deux caissiers ?")
        _ecrire(client, conv_id, h, "Est-ce que je peux reprendre les dettes de mon cahier ?")
        assert not envoi.called
        r = _ecrire(client, conv_id, h, "Lumistock gère-t-il Orange Money ?")
        data = r.get_json()
        assert data["statut"] == "attente"
        assert data["nouveaux"][-1]["auteur"] == "systeme"
        # Le compte staff actif est prévenu par email, avec un lien vers la conversation.
        assert envoi.call_count == 1 and envoi.call_args[0][1] == "staff@test.com"
        assert conv_id in envoi.call_args[0][0].as_string()

        # La question suivante ne renvoie pas d'email.
        _ecrire(client, conv_id, h, "Et la comptabilité SYSCOHADA ?")
        assert envoi.call_count == 1
        # Les fiches de la base de connaissances sont transmises à l'IA.
        assert "SYSCOHADA" in ia.call_args[0][0][0]["content"]

    # Le staff voit la conversation à traiter, l'ouvre et répond.
    hs = _connexion(client, "staff@test.com")
    liste = client.get("/assistant/admin/conversations", headers=hs).get_json()
    assert liste["compteurs"]["attente"] == 1 and liste["conversations"][0]["questions_pertinentes"] == 4
    assert client.get("/assistant/admin/non-lus", headers=hs).get_json()["non_lus"] == 1
    detail = client.get(f"/assistant/admin/conversations/{conv_id}", headers=hs).get_json()
    assert detail["non_lus"] == 0 and detail["email"] == "awa@exemple.cm"

    r = client.post(f"/assistant/admin/conversations/{conv_id}/messages", json={"texte": "Bonjour Awa, je m'en occupe."}, headers=hs)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["statut"] == "conseiller"

    # Le visiteur voit le nom du conseiller ; Nansé ne répond plus.
    vue = client.get(f"/assistant/conversations/{conv_id}", headers=h).get_json()
    assert vue["agent_nom"] == "Test staff_id"
    dernier = vue["messages"][-1]
    assert dernier["auteur"] == "conseiller" and dernier["auteur_nom"] == "Test staff_id"
    depuis = dernier["created_at"]
    with patch("services.assistant_service.appeler_ia") as ia, patch("utils.email._envoyer", return_value=True):
        r = _ecrire(client, conv_id, h, "Merci, j'ai deux caissiers et un gérant.")
        assert not ia.called and [m["auteur"] for m in r.get_json()["nouveaux"]] == ["visiteur"]
    nouveaux = client.get(f"/assistant/conversations/{conv_id}?depuis={depuis}", headers=h).get_json()["messages"]
    assert [m["auteur"] for m in nouveaux] == ["visiteur"]

    # Rendre la main à Nansé, puis fermer : le visiteur ne peut plus écrire.
    assert client.patch(f"/assistant/admin/conversations/{conv_id}", json={"statut": "ia"}, headers=hs).get_json()["statut"] == "ia"
    assert client.patch(f"/assistant/admin/conversations/{conv_id}", json={"statut": "fermee"}, headers=hs).get_json()["statut"] == "fermee"
    assert _ecrire(client, conv_id, h, "Encore une question").status_code == 409


def test_demande_explicite_d_un_conseiller(client, app, setup_auth):
    _vider(app)
    conv_id, h = _demarrer(client)
    with patch("services.assistant_service.appeler_ia", return_value=_ia("Je préviens un conseiller.", conseiller=True)), \
            patch("utils.email._envoyer", return_value=True) as envoi:
        data = _ecrire(client, conv_id, h, "Je veux parler à un commercial").get_json()
    assert data["statut"] == "attente" and envoi.call_count == 1


def test_sans_ia_configuree_la_base_de_connaissances_repond(client, app, setup_auth):
    _vider(app)
    conv_id, h = _demarrer(client)
    with patch("services.assistant_service.appeler_ia", side_effect=IANonConfiguree()):
        data = _ecrire(client, conv_id, h, "Est-ce que Lumistock gère Orange Money et MTN MoMo ?").get_json()
    reponse = data["nouveaux"][1]
    assert reponse["auteur"] == "ia" and "MTN MoMo" in reponse["texte"]
    assert data["nouveaux"][0]["pertinente"] is True


def test_routes_staff_reservees_au_staff(client, app, setup_auth):
    _vider(app)
    ha = _connexion(client, "admin@test.com")
    assert client.get("/assistant/admin/conversations", headers=ha).status_code == 403
    assert client.get("/assistant/admin/conversations").status_code == 401


def test_champ_anti_robots(client, app, setup_auth):
    _vider(app)
    base = {"nom": "Awa Mballa", "email": "awa@exemple.cm"}
    # Remplissage automatique du navigateur (une adresse email) : accepté.
    assert client.post("/assistant/conversations", json={**base, "site_web_societe": "autre@gmail.com"}).status_code == 201
    # Un robot remplit le champ avec autre chose : refusé.
    assert client.post("/assistant/conversations", json={**base, "site_web_societe": "https://spam.example"}).status_code == 400


def test_alerte_reessayee_apres_une_coupure_reseau(client, app, setup_auth):
    _vider(app)
    conv_id, h = _demarrer(client)
    # 1er essai raté (réseau coupé), 2e réussi.
    with patch("services.assistant_service.appeler_ia", return_value=_ia(conseiller=True)), \
            patch("utils.email._envoyer", side_effect=[False, True]) as envoi:
        assert _ecrire(client, conv_id, h, "Je veux parler à un conseiller").get_json()["statut"] == "attente"
    assert envoi.call_count == 2
