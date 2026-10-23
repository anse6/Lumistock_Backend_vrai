"""Messagerie : message à un client, renvoi, envois programmés, relances automatiques."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from bson import ObjectId

from extensions import db
from services import messagerie_planification as plan
from tests.integration.test_messagerie import m, _h  # noqa: F401

ENVOI_OK = (True, "r", None)


def _demain_a(heure=10):
    return (datetime.now() + timedelta(days=1)).replace(hour=heure, minute=0, second=0, microsecond=0)


def _utc(local):
    return plan.vers_utc(local)


def _campagnes(app, **filtre):
    with app.app_context():
        return list(db.tables.campagnes_messages.find(filtre))


# --- Message à un seul client --------------------------------------------------

def test_message_a_un_seul_client_ignore_le_delai(client, app, m):
    url = f"/messagerie/clients/{m['A']['debiteur']}/envoyer"
    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK) as sms:
        assert client.post(url, json={"segment": "debiteurs", "canaux": ["sms"]}, headers=_h(m["A"])).status_code == 201
        # Le lendemain ou le jour même : l'admin peut renvoyer quand il le décide.
        assert client.post(url, json={"segment": "debiteurs", "canaux": ["sms"]}, headers=_h(m["A"])).status_code == 201
    assert sms.call_count == 2
    assert all(c["type_envoi"] == "individuel" for c in _campagnes(app))


def test_message_individuel_personnalise(client, m):
    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK) as sms:
        r = client.post(f"/messagerie/clients/{m['A']['debiteur']}/envoyer", json={
            "segment": "debiteurs", "canaux": ["sms"],
            "personnalise": {"sms": "Bonjour {nom}, votre commande est prête. {magasin}"}}, headers=_h(m["A"]))
    assert r.status_code == 201
    assert sms.call_args.args[1] == "Bonjour Paul Dette, votre commande est prête. Boutique A"


def test_message_individuel_refuse_hors_categorie_ou_autre_magasin(client, m):
    # Jean n'a pas de dette : pas de rappel de dette.
    r = client.post(f"/messagerie/clients/{m['A']['acheteur']}/envoyer",
                    json={"segment": "debiteurs", "canaux": ["sms"]}, headers=_h(m["A"]))
    assert r.status_code == 400
    r = client.post(f"/messagerie/clients/{m['B']['debiteur']}/envoyer",
                    json={"segment": "debiteurs", "canaux": ["sms"]}, headers=_h(m["A"]))
    assert r.status_code == 400
    assert client.get(f"/messagerie/clients/{m['B']['debiteur']}", headers=_h(m["A"])).status_code == 404


def test_message_individuel_respecte_le_refus_du_client(client, m):
    client.put(f"/messagerie/clients/{m['A']['debiteur']}/preference", json={"ne_pas_contacter": True},
               headers=_h(m["A"]))
    r = client.post(f"/messagerie/clients/{m['A']['debiteur']}/envoyer",
                    json={"segment": "debiteurs", "canaux": ["sms"]}, headers=_h(m["A"]))
    assert r.status_code == 400 and "ne souhaite pas" in r.get_json()["error"]


def test_situation_d_un_client(client, m):
    r = client.get(f"/messagerie/clients/{m['A']['debiteur']}", headers=_h(m["A"]))
    assert r.status_code == 200
    data = r.get_json()
    assert data["dette"] == 15000 and "debiteurs" in data["segments"] and data["nombre_achats_non_soldes"] == 1


# --- Renvoi ----------------------------------------------------------------------

def test_renvoyer_un_envoi_avec_les_montants_du_jour(client, app, m):
    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK):
        cid = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"]},
                          headers=_h(m["A"])).get_json()["campagne_id"]
    with app.app_context():  # Paul paie 5 000 de plus
        db.tables.livraisons.update_one({"client_id": ObjectId(m["A"]["debiteur"])},
                                        {"$set": {"montant_paye": 10000.0}})
    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK) as sms:
        r = client.post(f"/messagerie/campagnes/{cid}/renvoyer", json={}, headers=_h(m["A"]))
    assert r.status_code == 201  # le délai de 24 h ne bloque pas un renvoi explicite
    assert "10 000 FCFA" in sms.call_args.args[1]
    renvoi = _campagnes(app, _id=ObjectId(r.get_json()["campagne_id"]))[0]
    assert renvoi["type_envoi"] == "renvoi" and renvoi["origine"]["campagne_id"] == cid


# --- Envois programmés -------------------------------------------------------------

def test_programmer_puis_envoyer_a_l_heure(client, app, m):
    demain = _demain_a(10)
    r = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"],
                                                   "date_envoi": demain.isoformat()}, headers=_h(m["A"]))
    assert r.status_code == 201, r.get_json()
    programme_id = r.get_json()["programme_id"]
    liste = client.get("/messagerie/programmes", headers=_h(m["A"])).get_json()["programmes"]
    assert liste[0]["statut"] == "programme" and liste[0]["date_prevue"].startswith(demain.strftime("%Y-%m-%dT10:00"))

    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK) as sms, app.app_context():
        plan.tick(_utc(demain - timedelta(minutes=5)))
        assert sms.call_count == 0  # pas encore l'heure
        plan.tick(_utc(demain + timedelta(minutes=1)))
        plan.tick(_utc(demain + timedelta(minutes=2)))  # une seule fois
    assert sms.call_count == 1
    with app.app_context():
        p = db.tables.envois_programmes.find_one({"_id": ObjectId(programme_id)})
    assert p["statut"] == "envoye" and p["campagne_id"]


def test_montants_calcules_au_moment_de_l_envoi(client, app, m):
    demain = _demain_a(10)
    client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"],
                                               "date_envoi": demain.isoformat()}, headers=_h(m["A"]))
    with app.app_context():  # Paul a tout payé avant la date prévue
        db.tables.livraisons.update_one({"client_id": ObjectId(m["A"]["debiteur"])},
                                        {"$set": {"montant_paye": 20000.0}})
    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK) as sms, app.app_context():
        plan.tick(_utc(demain + timedelta(minutes=1)))
    assert sms.call_count == 0  # plus de dette, plus de rappel
    liste = client.get("/messagerie/programmes", headers=_h(m["A"])).get_json()["programmes"]
    assert liste[0]["statut"] == "echec" and "Aucun client" in liste[0]["erreur"]


def test_programmer_un_seul_client_et_annuler(client, app, m):
    r = client.post(f"/messagerie/clients/{m['A']['debiteur']}/envoyer", json={
        "segment": "debiteurs", "canaux": ["sms"], "date_envoi": _demain_a(9).isoformat()}, headers=_h(m["A"]))
    assert r.status_code == 201
    pid = r.get_json()["programme_id"]
    liste = client.get("/messagerie/programmes", headers=_h(m["A"])).get_json()["programmes"]
    assert liste[0]["clients"] == ["Paul Dette"] and liste[0]["type_envoi"] == "individuel"
    assert client.delete(f"/messagerie/programmes/{pid}", headers=_h(m["B"])).status_code == 400  # autre magasin
    assert client.delete(f"/messagerie/programmes/{pid}", headers=_h(m["A"])).status_code == 200
    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK) as sms, app.app_context():
        plan.tick(_utc(_demain_a(9) + timedelta(minutes=1)))
    sms.assert_not_called()


@pytest.mark.parametrize("date, message", [
    ("hier", "futur"), ("nuit", "entre 8 h et 20 h"), ("", "Choisissez la date"), ("pas-une-date", "invalide"),
])
def test_date_de_programmation_invalide(client, m, date, message):
    valeurs = {"hier": (datetime.now() - timedelta(days=1)).isoformat(),
               "nuit": _demain_a(23).isoformat(), "": " ", "pas-une-date": "pas-une-date"}
    r = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"],
                                                   "date_envoi": valeurs[date]}, headers=_h(m["A"]))
    assert r.status_code == 400 and message in r.get_json()["error"]


def test_envoi_en_retard_attend_la_plage_horaire(client, app, m):
    demain = _demain_a(19)
    client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"],
                                               "date_envoi": demain.replace(minute=30).isoformat()}, headers=_h(m["A"]))
    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK) as sms, app.app_context():
        plan.tick(_utc(demain.replace(hour=23)))  # serveur arrêté : rattrapage à 23 h interdit
        assert sms.call_count == 0
        plan.tick(_utc(demain.replace(hour=8, minute=5) + timedelta(days=1)))  # le lendemain à 8 h 05
    assert sms.call_count == 1


# --- Relances automatiques ---------------------------------------------------------

REGLE = {"nom": "Relance du lundi", "segment": "debiteurs", "canaux": ["sms"], "frequence": "hebdomadaire",
         "jours": [0], "heure": "09:00", "filtres": {"dette_minimum": 10000}}


def test_creer_une_relance_et_prochaine_execution(client, m):
    r = client.post("/messagerie/regles", json=REGLE, headers=_h(m["A"]))
    assert r.status_code == 201, r.get_json()
    regle = r.get_json()["regle"]
    prochaine = datetime.fromisoformat(regle["prochaine_execution"])
    assert prochaine.weekday() == 0 and prochaine.hour == 9 and prochaine > datetime.now()
    assert regle["description"] == "Chaque lundi à 9 h"
    # Réservé à l'admin
    assert client.post("/messagerie/regles", json=REGLE, headers=_h(m["A"], "manager")).status_code == 403
    assert client.get("/messagerie/regles", headers=_h(m["B"])).get_json()["regles"] == []


@pytest.mark.parametrize("changement, message", [
    ({"jours": []}, "jour de la semaine"), ({"heure": "25:00"}, "Heure invalide"),
    ({"heure": "06:00"}, "entre 8 h et 20 h"), ({"frequence": "annuelle"}, "Fréquence"),
    ({"nom": ""}, "nom"), ({"frequence": "mensuelle", "jours": [31]}, "jour du mois"),
])
def test_relance_invalide(client, m, changement, message):
    r = client.post("/messagerie/regles", json={**REGLE, **changement}, headers=_h(m["A"]))
    assert r.status_code == 400 and message in r.get_json()["error"]


def test_relance_automatique_executee_une_fois_puis_replanifiee(client, app, m):
    regle = client.post("/messagerie/regles", json=REGLE, headers=_h(m["A"])).get_json()["regle"]
    prevue = datetime.fromisoformat(regle["prochaine_execution"])
    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK) as sms, app.app_context():
        plan.tick(_utc(prevue + timedelta(minutes=1)))
        plan.tick(_utc(prevue + timedelta(minutes=2)))
    assert sms.call_count == 1  # Paul doit 15 000 ≥ 10 000 ; le client sans numéro est ignoré
    apres = client.get("/messagerie/regles", headers=_h(m["A"])).get_json()["regles"][0]
    assert datetime.fromisoformat(apres["prochaine_execution"]) == prevue + timedelta(days=7)
    assert apres["dernier_resultat"]["campagne_id"]
    campagne = _campagnes(app, type_envoi="automatique")[0]
    assert campagne["origine"]["regle_id"] == regle["_id"] and campagne["cree_par"] is None


def test_relance_automatique_respecte_conditions_et_delai(client, app, m):
    regle = client.post("/messagerie/regles", json={**REGLE, "filtres": {"dette_minimum": 20000}},
                        headers=_h(m["A"])).get_json()["regle"]
    prevue = datetime.fromisoformat(regle["prochaine_execution"])
    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK) as sms, app.app_context():
        plan.tick(_utc(prevue + timedelta(minutes=1)))
    sms.assert_not_called()  # personne ne doit 20 000 ou plus
    resultat = client.get("/messagerie/regles", headers=_h(m["A"])).get_json()["regles"][0]["dernier_resultat"]
    assert "info" in resultat and "erreur" not in resultat


def test_relance_en_pause_et_execution_immediate(client, app, m):
    regle = client.post("/messagerie/regles", json={**REGLE, "actif": False}, headers=_h(m["A"])).get_json()["regle"]
    assert regle["prochaine_execution"] is None
    with patch("services.sms_service.envoyer_sms", return_value=ENVOI_OK) as sms:
        with app.app_context():
            plan.tick(_utc(datetime.now() + timedelta(days=8)))
        assert sms.call_count == 0
        r = client.post(f"/messagerie/regles/{regle['_id']}/executer", headers=_h(m["A"]))
    assert r.status_code == 200 and r.get_json()["resultat"]["campagne_id"]
    assert sms.call_count == 1
    assert client.put(f"/messagerie/regles/{regle['_id']}", json={"actif": True},
                      headers=_h(m["A"])).get_json()["regle"]["prochaine_execution"]
    assert client.delete(f"/messagerie/regles/{regle['_id']}", headers=_h(m["A"])).status_code == 200


def test_anciennete_de_la_dette(client, app, m):
    with app.app_context():
        db.tables.livraisons.update_many({"client_id": ObjectId(m["A"]["debiteur"])},
                                         {"$set": {"date_commande": datetime.utcnow() - timedelta(days=3)}})
    apercu = client.post("/messagerie/apercu", json={"segment": "debiteurs", "canaux": ["sms"],
                                                     "filtres": {"anciennete_minimum_jours": 7}},
                         headers=_h(m["A"])).get_json()
    assert all(d["nom"] != "Paul Dette" for d in apercu["destinataires"])  # dette de 3 jours seulement


def test_prochaine_execution_mensuelle():
    regle = {"frequence": "mensuelle", "jours": [1, 15], "heure": "09:30"}
    assert plan.prochaine_execution(regle, datetime(2026, 10, 16, 12)) == datetime(2026, 11, 1, 9, 30)
    assert plan.prochaine_execution(regle, datetime(2026, 11, 1, 9, 30)) == datetime(2026, 11, 15, 9, 30)
    quotidienne = {"frequence": "quotidienne", "jours": [], "heure": "08:00"}
    assert plan.prochaine_execution(quotidienne, datetime(2026, 10, 16, 7, 59)) == datetime(2026, 10, 16, 8, 0)


def test_programmation_refusee_pour_un_client_hors_categorie(client, m):
    r = client.post(f"/messagerie/clients/{m['A']['acheteur']}/envoyer", json={
        "segment": "debiteurs", "canaux": ["sms"], "date_envoi": _demain_a(10).isoformat()}, headers=_h(m["A"]))
    assert r.status_code == 400 and "pas de dette en cours" in r.get_json()["error"]
