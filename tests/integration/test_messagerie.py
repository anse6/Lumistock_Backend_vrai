"""Messagerie clients : catégories, modèles, aperçu, envoi SMS/email et sécurité."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def m(app):
    """Deux magasins ; dans A : un débiteur, un client avec avance, un acheteur,
    un client sans téléphone et le client de passage."""
    app.config.update(SMS_PROVIDER="console", MESSAGERIE_SYNCHRONE=True, SMS_QUOTA_MENSUEL=0,
                      EMAIL_HOST="smtp.test", MAIL_SENDER="noreply@test")
    with app.app_context():
        for t in ("magasin", "users", "clients", "livraisons", "parametres_messages",
                  "campagnes_messages", "messages_clients"):
            db.tables[t].delete_many({})
        res = {}
        for nom in ("A", "B"):
            mid = ObjectId()
            db.tables.magasin.insert_one({"_id": mid, "denomination": f"Boutique {nom}", "is_active": True,
                                          "telephone": "+237699000000", "devise": "XAF",
                                          "email": f"contact@{nom.lower()}.test", "created_at": datetime.utcnow()})
            ids = {"magasin": str(mid)}
            for role in ("admin", "manager", "gerant"):
                ids[role] = str(db.tables.users.insert_one({
                    "name": role, "first_name": nom, "email": f"{role}@{nom.lower()}.test", "role": role,
                    "magasin_id": mid, "is_active": True, "created_at": datetime.utcnow()}).inserted_id)

            def client(nom_client, tel, email="", caisse=0.0):
                return str(db.tables.clients.insert_one({
                    "nom": nom_client, "telephone": tel, "email": email, "magasin_id": mid,
                    "montant_en_caisse": caisse, "created_at": datetime.utcnow()}).inserted_id)
            ids["debiteur"] = client("Paul Dette", "690112233", "paul@x.test")
            ids["avance"] = client("Awa Avance", "+237677000001", caisse=5000.0)
            ids["acheteur"] = client("Jean Simple", "+237677000002")
            ids["sans_tel"] = client("Sans Numero", "", "")
            ids["passage"] = client("Autres", "000000000")
            # Vente : 20 000 dont 5 000 payés -> doit 15 000. Une vente en corbeille ne compte pas.
            db.tables.livraisons.insert_one({"magasin_id": mid, "client_id": ObjectId(ids["debiteur"]),
                                             "montant_total": 20000.0, "montant_paye": 5000.0,
                                             "created_at": datetime.utcnow()})
            db.tables.livraisons.insert_one({"magasin_id": mid, "client_id": ObjectId(ids["acheteur"]),
                                             "montant_total": 9000.0, "montant_paye": 0.0,
                                             "deleted_at": datetime.utcnow(), "created_at": datetime.utcnow()})
            db.tables.livraisons.insert_one({"magasin_id": mid, "client_id": ObjectId(ids["sans_tel"]),
                                             "montant_total": 1000.0, "montant_paye": 0.0,
                                             "created_at": datetime.utcnow()})
            res[nom] = ids
        yield res


def _h(m, role="admin"):
    return {"Authorization": f"Bearer {generate_token(user_id=m[role], role=role)}"}


def _messages(app, **filtre):
    with app.app_context():
        return list(db.tables.messages_clients.find(filtre))


def test_categories_de_clients(client, m):
    r = client.get("/messagerie/parametres", headers=_h(m["A"]))
    assert r.status_code == 200
    seg = r.get_json()["resume"]["segments"]
    assert seg["debiteurs"]["clients"] == 2          # Paul + client sans numéro
    assert seg["debiteurs"]["joignables_sms"] == 1
    assert seg["avances"]["clients"] == 1
    assert seg["acheteurs"]["clients"] == 1          # la vente en corbeille ne compte pas
    assert seg["tous"]["clients"] == 4               # le client de passage est exclu
    assert r.get_json()["resume"]["total_dette"] == 16000


def test_apercu_rend_le_nom_et_la_dette(client, m):
    r = client.post("/messagerie/apercu", json={"segment": "debiteurs", "canaux": ["sms", "email"]},
                    headers=_h(m["A"]))
    assert r.status_code == 200
    data = r.get_json()
    paul = next(d for d in data["destinataires"] if d["nom"] == "Paul Dette")
    sms = paul["canaux"]["sms"]
    assert sms["joignable"] and "Paul Dette" in sms["texte"] and "15 000 FCFA" in sms["texte"]
    assert "Boutique A" in sms["texte"] and paul["telephone"] == "+237690112233"
    assert paul["canaux"]["email"]["joignable"] and "15 000 FCFA" in paul["canaux"]["email"]["texte"]
    sans = next(d for d in data["destinataires"] if d["nom"] == "Sans Numero")
    assert not sans["canaux"]["sms"]["joignable"] and "numéro" in sans["canaux"]["sms"]["raison"]
    assert data["totaux"]["sms"]["joignables"] == 1


def test_envoi_sms_aux_debiteurs_et_journal(client, app, m):
    with patch("services.sms_service.envoyer_sms", return_value=(True, "ref-1", None)) as sms:
        r = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"]},
                        headers=_h(m["A"]))
    assert r.status_code == 201, r.get_json()
    sms.assert_called_once()
    numero, texte = sms.call_args.args
    assert numero == "+237690112233" and "15 000 FCFA" in texte
    detail = client.get(f"/messagerie/campagnes/{r.get_json()['campagne_id']}", headers=_h(m["A"])).get_json()
    assert detail["statut"] == "terminee" and detail["envoyes"] == 1 and detail["ignores"] == 1
    hist = client.get(f"/messagerie/clients/{m['A']['debiteur']}/historique", headers=_h(m["A"])).get_json()
    assert len(hist["messages"]) == 1


def test_delai_entre_deux_relances(client, app, m):
    with patch("services.sms_service.envoyer_sms", return_value=(True, "r", None)):
        assert client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"]},
                           headers=_h(m["A"])).status_code == 201
        r = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"]},
                        headers=_h(m["A"]))
    assert r.status_code == 400  # déjà relancé il y a moins de 24 h
    apercu = client.post("/messagerie/apercu", json={"segment": "debiteurs", "canaux": ["sms"]},
                         headers=_h(m["A"])).get_json()
    paul = next(d for d in apercu["destinataires"] if d["nom"] == "Paul Dette")
    assert "Déjà contacté" in paul["canaux"]["sms"]["raison"]


def test_echec_d_envoi_journalise(client, app, m):
    with patch("services.sms_service.envoyer_sms", return_value=(False, None, "Crédit SMS épuisé")):
        r = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"]},
                        headers=_h(m["A"]))
    detail = client.get(f"/messagerie/campagnes/{r.get_json()['campagne_id']}", headers=_h(m["A"])).get_json()
    assert detail["echecs"] == 1 and detail["messages"][0]["erreur"] == "Crédit SMS épuisé"
    # Un échec ne compte pas comme une relance : on peut réessayer.
    with patch("services.sms_service.envoyer_sms", return_value=(True, "r", None)):
        assert client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"]},
                           headers=_h(m["A"])).status_code == 201


def test_envoi_email_avec_reponse_au_magasin(client, app, m):
    client.put("/messagerie/parametres", json={"canaux": {"email": True}}, headers=_h(m["A"]))
    with patch("utils.email._envoyer", return_value=True) as envoi:
        r = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["email"]},
                        headers=_h(m["A"]))
    assert r.status_code == 201, r.get_json()
    msg, destinataire = envoi.call_args.args
    assert destinataire == "paul@x.test" and msg["Reply-To"] == "contact@a.test"
    assert "Boutique A" in msg["Subject"]


def test_modeles_modifiables_et_valides(client, m):
    r = client.put("/messagerie/parametres", json={"modeles": {"avances": {
        "actif": True, "sms": "Salut {nom}, il vous reste {avance} chez {magasin}."}}}, headers=_h(m["A"]))
    assert r.status_code == 200
    apercu = client.post("/messagerie/apercu", json={"segment": "avances", "canaux": ["sms"]},
                         headers=_h(m["A"])).get_json()
    assert apercu["destinataires"][0]["canaux"]["sms"]["texte"] == "Salut Awa Avance, il vous reste 5 000 FCFA chez Boutique A."
    r = client.put("/messagerie/parametres", json={"modeles": {"avances": {"sms": "Bonjour {prenom}"}}},
                   headers=_h(m["A"]))
    assert r.status_code == 400 and "{prenom}" in r.get_json()["error"]
    r = client.put("/messagerie/parametres", json={"modeles": {"avances": {"sms": "x" * 481}}}, headers=_h(m["A"]))
    assert r.status_code == 400


def test_type_de_message_inactif_refuse(client, m):
    # Par défaut seul le rappel de dette est actif.
    r = client.post("/messagerie/campagnes", json={"segment": "acheteurs", "canaux": ["sms"]}, headers=_h(m["A"]))
    assert r.status_code == 400 and "activé" in r.get_json()["error"]


def test_canal_desactive_ou_non_configure(client, app, m):
    r = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["email"]}, headers=_h(m["A"]))
    assert r.status_code == 400  # email désactivé par défaut dans le magasin
    app.config["SMS_PROVIDER"] = ""
    r = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"]}, headers=_h(m["A"]))
    assert r.status_code == 400 and "configuré" in r.get_json()["error"]


def test_quota_mensuel(client, app, m):
    app.config["SMS_QUOTA_MENSUEL"] = 1
    client.put("/messagerie/parametres", json={"modeles": {"tous": {"actif": True, "sms": "Bonjour {nom}"}},
                                               "options": {"delai_relance_heures": 0}}, headers=_h(m["A"]))
    with patch("services.sms_service.envoyer_sms", return_value=(True, "r", None)):
        r = client.post("/messagerie/campagnes", json={"segment": "tous", "canaux": ["sms"]}, headers=_h(m["A"]))
    assert r.status_code == 400 and "Quota" in r.get_json()["error"]


def test_client_qui_refuse_les_messages(client, m):
    r = client.put(f"/messagerie/clients/{m['A']['debiteur']}/preference", json={"ne_pas_contacter": True},
                   headers=_h(m["A"]))
    assert r.status_code == 200
    r = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"]}, headers=_h(m["A"]))
    assert r.status_code == 400  # plus personne de joignable


def test_selection_d_une_partie_des_clients(client, app, m):
    client.put("/messagerie/parametres", json={"modeles": {"tous": {"actif": True, "sms": "Bonjour {nom}"}}},
               headers=_h(m["A"]))
    with patch("services.sms_service.envoyer_sms", return_value=(True, "r", None)) as sms:
        r = client.post("/messagerie/campagnes", json={"segment": "tous", "canaux": ["sms"],
                                                       "client_ids": [m["A"]["avance"], m["B"]["debiteur"]]},
                        headers=_h(m["A"]))
    assert r.status_code == 201
    # Le client du magasin B est ignoré : seul le client A sélectionné reçoit le SMS.
    assert [c.args[0] for c in sms.call_args_list] == ["+237677000001"]


def test_isolation_et_roles(client, m):
    with patch("services.sms_service.envoyer_sms", return_value=(True, "r", None)):
        cid = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"]},
                          headers=_h(m["A"])).get_json()["campagne_id"]
    assert client.get(f"/messagerie/campagnes/{cid}", headers=_h(m["B"])).status_code == 404
    assert client.get("/messagerie/campagnes", headers=_h(m["B"])).get_json()["campagnes"] == []
    assert client.put(f"/messagerie/clients/{m['A']['debiteur']}/preference", json={},
                      headers=_h(m["B"])).status_code == 404
    # Le manager envoie mais ne modifie pas les modèles ; le gérant n'a pas accès.
    assert client.put("/messagerie/parametres", json={}, headers=_h(m["A"], "manager")).status_code == 403
    assert client.post("/messagerie/apercu", json={"segment": "debiteurs"},
                       headers=_h(m["A"], "manager")).status_code == 200
    assert client.get("/messagerie/parametres", headers=_h(m["A"], "gerant")).status_code == 403
    assert client.get("/messagerie/parametres").status_code == 401


def test_envoi_de_test(client, m):
    with patch("services.sms_service.envoyer_sms", return_value=(True, "r", None)) as sms:
        r = client.post("/messagerie/test", json={"segment": "debiteurs", "canal": "sms",
                                                  "destinataire": "690000000"}, headers=_h(m["A"]))
    assert r.status_code == 200
    assert sms.call_args.args[0] == "+237690000000" and sms.call_args.args[1].startswith("[TEST]")


def test_reprise_d_une_campagne_interrompue(client, app, m):
    with patch("services.messagerie_service.lancer_campagne"):
        cid = client.post("/messagerie/campagnes", json={"segment": "debiteurs", "canaux": ["sms"]},
                          headers=_h(m["A"])).get_json()["campagne_id"]
    with app.app_context():
        db.tables.campagnes_messages.update_one({"_id": ObjectId(cid)},
                                                {"$set": {"updated_at": datetime.utcnow() - timedelta(hours=1)}})
    assert client.get(f"/messagerie/campagnes/{cid}", headers=_h(m["A"])).get_json()["statut"] == "interrompue"
    with patch("services.sms_service.envoyer_sms", return_value=(True, "r", None)):
        assert client.post(f"/messagerie/campagnes/{cid}/reprendre", headers=_h(m["A"])).status_code == 200
    assert client.get(f"/messagerie/campagnes/{cid}", headers=_h(m["A"])).get_json()["statut"] == "terminee"


def test_comptage_des_sms():
    from services.sms_service import compter_sms
    assert compter_sms("a" * 160) == {"caracteres": 160, "sms": 1, "encodage": "GSM"}
    assert compter_sms("a" * 161)["sms"] == 2
    assert compter_sms("Café élevé à Douala")["encodage"] == "GSM"
    assert compter_sms("Fête 🎉")["encodage"] == "Unicode"


def test_message_detaille_produits_et_paiements(client, app, m):
    with app.app_context():
        db.tables.livraisons.insert_one({
            "magasin_id": ObjectId(m["A"]["magasin"]), "client_id": ObjectId(m["A"]["avance"]),
            "montant_total": 50000.0, "montant_paye": 20000.0,
            "date_commande": datetime(2026, 10, 12, 9, 0), "created_at": datetime.utcnow(),
            "produits": [{"nom": "Riz 25 kg", "quantite": 2}, {"nom": "Huile 5 L", "quantite": 1},
                         {"nom": "Riz 25 kg", "quantite": 1}],
        })
    data = client.post("/messagerie/apercu", json={"segment": "debiteurs", "canaux": ["sms", "email"]},
                       headers=_h(m["A"])).get_json()
    awa = next(d for d in data["destinataires"] if d["nom"] == "Awa Avance")
    sms = awa["canaux"]["sms"]["texte"]
    assert "sauf erreur" not in sms.lower()
    assert "votre achat du 12/10/2026 (Riz 25 kg x3, Huile 5 L x1)" in sms
    assert "Sur un total de 50 000 FCFA, vous avez déjà réglé 20 000 FCFA ; il vous reste 30 000 FCFA à payer." in sms
    assert "Vous disposez par ailleurs d'un solde de 5 000 FCFA sur votre compte." in sms
    assert "  " not in sms and "+237 699 00 00 00" in sms
    email = awa["canaux"]["email"]["texte"]
    assert "Total : 50 000 FCFA | Déjà réglé : 20 000 FCFA | Reste à payer : 30 000 FCFA" in email
    # Sans paiement ni avance : phrase adaptée, pas de mention vide.
    paul = next(d for d in data["destinataires"] if d["nom"] == "Paul Dette")["canaux"]["sms"]["texte"]
    assert "déjà réglé 5 000 FCFA" in paul and "solde" not in paul.split("payer.")[1]


def test_ancien_texte_par_defaut_remplace(client, app, m):
    from services.messagerie_service import ANCIENS_MODELES, get_parametres
    ancien = next(t for t in ANCIENS_MODELES if "sauf erreur" in t and "{dette} à {magasin}" in t)
    with app.app_context():
        db.tables.parametres_messages.insert_one({
            "magasin_id": ObjectId(m["A"]["magasin"]),
            "modeles": {"debiteurs": {"actif": True, "sms": ancien}, "avances": {"sms": "Texte perso {nom}"}}})
        params = get_parametres(m["A"]["magasin"])
    assert "sauf erreur" not in params["modeles"]["debiteurs"]["sms"]
    assert params["modeles"]["avances"]["sms"] == "Texte perso {nom}"  # un texte modifié est conservé
