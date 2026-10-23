"""Rôle caissier : création des comptes, accès limités, ventes au comptoir,
sessions de caisse, annulation sous 15 minutes et suivi par l'admin."""
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def m(app):
    """Un magasin avec admin, manager, deux caissiers, un client enregistré et un produit (prix 300, achat 100)."""
    with app.app_context():
        for table in ("magasin", "users", "produits", "livraisons", "clients", "sessions_caisse", "categories"):
            db.tables[table].delete_many({})
        magasin_id = ObjectId()
        db.tables.magasin.insert_one({"_id": magasin_id, "denomination": "Boutique", "is_active": True,
                                      "licence": {"max_managers": 2, "max_livreurs": 2},
                                      "created_at": datetime.utcnow()})
        ids = {"magasin": str(magasin_id)}
        for cle, role in (("admin", "admin"), ("manager", "manager"), ("caissier", "caissier"), ("caissier2", "caissier")):
            ids[cle] = str(db.tables.users.insert_one({
                "name": cle, "first_name": "Test", "email": f"{cle}@boutique.test", "role": role,
                "magasin_id": magasin_id, "is_active": True, "created_at": datetime.utcnow(),
            }).inserted_id)
        ids["roles"] = {"admin": "admin", "manager": "manager", "caissier": "caissier", "caissier2": "caissier"}
        ids["client"] = str(db.tables.clients.insert_one({
            "nom": "Mme Client", "telephone": "+237690000001", "magasin_id": magasin_id,
            "montant_en_caisse": 0.0, "created_at": datetime.utcnow()}).inserted_id)
    yield ids


def h(m, qui):
    return {"Authorization": f"Bearer {generate_token(user_id=m[qui], role=m['roles'][qui])}"}


def produit(client, m, quantite=20):
    r = client.post("/produits", data={"nom": "Riz", "prix": "300", "prix_achat": "100", "quantite": str(quantite),
                                       "unite": "kg"}, headers=h(m, "admin"), content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    return r.get_json()["produit_id"]


def stock(app, produit_id):
    with app.app_context():
        return db.tables.produits.find_one({"_id": ObjectId(produit_id)})["quantite"]


def ouvrir(client, m, qui="caissier", fond=5000):
    r = client.post("/caisse/session/ouvrir", json={"fond_initial": fond}, headers=h(m, qui))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["session"]


def vendre(client, m, lignes, qui="caissier", **extra):
    corps = {"produits": lignes, "statut_paiement": "payé",
             "paiements": [{"methode": "cash", "montant": sum(300 * l["quantite"] for l in lignes)}]}
    corps.update(extra)
    return client.post("/caisse/ventes", json=corps, headers=h(m, qui))


# --------------------------------------------------------------------------- comptes
def test_admin_et_manager_creent_des_caissiers_dans_la_limite_de_la_licence(client, app, m):
    with app.app_context():  # le magasin a déjà 2 caissiers (limite par défaut : 2)
        db.tables.users.delete_many({"role": "caissier"})
    r = client.post("/users/create_caissier", json={"name": "Ngono", "first_name": "Alice", "email": "alice@b.test"},
                    headers=h(m, "admin"))
    assert r.status_code == 201, r.get_json()
    r = client.post("/users/create_caissier", json={"name": "Mbarga", "first_name": "Paul", "email": "paul@b.test"},
                    headers=h(m, "manager"))
    assert r.status_code == 201, r.get_json()
    r = client.post("/users/create_caissier", json={"name": "Ewane", "first_name": "Lea", "email": "lea@b.test"},
                    headers=h(m, "admin"))
    assert r.status_code == 400 and "Limite de caissiers" in r.get_json()["error"]
    with app.app_context():
        cree = db.tables.users.find_one({"email": "alice@b.test"})
        assert cree["role"] == "caissier" and str(cree["magasin_id"]) == m["magasin"]
        # Le super-admin augmente la licence : un 3e caissier devient possible.
        db.tables.magasin.update_one({"_id": ObjectId(m["magasin"])}, {"$set": {"licence.max_caissiers": 3}})
    r = client.post("/users/create_caissier", json={"name": "Ewane", "first_name": "Lea", "email": "lea@b.test"},
                    headers=h(m, "admin"))
    assert r.status_code == 201


def test_un_caissier_ne_peut_pas_creer_de_compte(client, m):
    r = client.post("/users/create_caissier", json={"name": "X", "first_name": "Y", "email": "x@b.test"},
                    headers=h(m, "caissier"))
    assert r.status_code == 403


# --------------------------------------------------------------------------- accès limités
@pytest.mark.parametrize("url", ["/produits", "/managers/livraisons", "/users/", "/charges/", "/compta/bilan",
                                 "/admin_or_manager/livraisons", "/clients/corbeille", "/clients/manager/",
                                 "/managers/stock_par_categorie"])
def test_le_caissier_n_accede_qu_a_sa_caisse(client, m, url):
    assert client.get(url, headers=h(m, "caissier")).status_code == 403


def test_catalogue_sans_prix_d_achat(client, m):
    produit(client, m)
    r = client.get("/caisse/catalogue", headers=h(m, "caissier"))
    assert r.status_code == 200
    texte = r.get_data(as_text=True)
    assert "prix_achat" not in texte and "Riz" in texte
    p = r.get_json()[0]["produits"][0]
    assert p["prix"] == 300 and p["stock"] == 20


# --------------------------------------------------------------------------- vente
def test_vente_impossible_caisse_fermee(client, app, m):
    pid = produit(client, m)
    r = vendre(client, m, [{"produit_id": pid, "quantite": 1}])
    assert r.status_code == 409 and "Ouvrez votre caisse" in r.get_json()["error"]
    assert stock(app, pid) == 20


def test_vente_client_de_passage_au_prix_catalogue(client, app, m):
    pid = produit(client, m)
    session = ouvrir(client, m)
    # Le prix saisi (150) est ignoré : le prix catalogue (300) s'applique.
    r = vendre(client, m, [{"produit_id": pid, "quantite": 2, "prix": 150}])
    assert r.status_code == 201, r.get_json()
    vente = r.get_json()
    assert vente["montant_total"] == 600 and vente["reste"] == 0 and vente["client"]["passage"] is True
    assert vente["numero"].startswith("FAC-") and vente["annulable_jusqua"]
    assert stock(app, pid) == 18
    with app.app_context():
        doc = db.tables.livraisons.find_one({"_id": ObjectId(vente["id"])})
        assert doc["vendeur_role"] == "caissier" and str(doc["session_caisse_id"]) == session["id"]
        assert str(doc["livreur_id"]) == m["caissier"]


def test_pas_de_credit_pour_un_client_de_passage(client, app, m):
    pid = produit(client, m)
    ouvrir(client, m)
    r = vendre(client, m, [{"produit_id": pid, "quantite": 2}], statut_paiement="partiellement payé",
               paiements=[{"methode": "cash", "montant": 100}])
    assert r.status_code == 400 and "crédit" in r.get_json()["error"]
    # Statut « payé » mais somme insuffisante : refusé aussi, stock intact.
    r = vendre(client, m, [{"produit_id": pid, "quantite": 2}], paiements=[{"methode": "cash", "montant": 100}])
    assert r.status_code == 400
    assert stock(app, pid) == 20


def test_credit_possible_pour_un_client_enregistre(client, m):
    pid = produit(client, m)
    ouvrir(client, m)
    r = vendre(client, m, [{"produit_id": pid, "quantite": 2}], client_id=m["client"],
               statut_paiement="partiellement payé", paiements=[{"methode": "momo", "montant": 200}])
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["reste"] == 400 and r.get_json()["statut_paiement"] == "partiellement payé"


def test_monnaie_rendue_n_est_pas_un_encaissement(client, app, m):
    pid = produit(client, m)
    ouvrir(client, m)
    r = vendre(client, m, [{"produit_id": pid, "quantite": 1}], paiements=[{"methode": "cash", "montant": 1000}])
    assert r.status_code == 400 and "monnaie" in r.get_json()["error"]
    assert stock(app, pid) == 20


def test_remises_interdites_sauf_autorisation_de_l_admin(client, m):
    pid = produit(client, m)
    ouvrir(client, m)
    remise = [{"type": "remise", "mode": "montant", "valeur": 100}]
    r = vendre(client, m, [{"produit_id": pid, "quantite": 2}], reductions=remise,
               paiements=[{"methode": "cash", "montant": 500}])
    assert r.status_code == 403
    assert client.put("/caisse/parametres", json={"remises_autorisees": True}, headers=h(m, "caissier")).status_code == 403
    # Droit d'accès : réglé par l'admin seulement (Paramètres > Accès).
    assert client.put("/caisse/parametres", json={"remises_autorisees": True}, headers=h(m, "manager")).status_code == 403
    assert client.put("/caisse/parametres", json={"remises_autorisees": True}, headers=h(m, "admin")).status_code == 200
    apercu = client.post("/caisse/apercu", json={"produits": [{"produit_id": pid, "quantite": 2}], "reductions": remise},
                         headers=h(m, "caissier"))
    assert apercu.status_code == 200 and apercu.get_json()["montant_total"] == 500
    r = vendre(client, m, [{"produit_id": pid, "quantite": 2}], reductions=remise,
               paiements=[{"methode": "cash", "montant": 500}])
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["total_reductions"] == 100


def test_stock_insuffisant(client, app, m):
    pid = produit(client, m, quantite=3)
    ouvrir(client, m)
    r = vendre(client, m, [{"produit_id": pid, "quantite": 4}])
    assert r.status_code in (400, 409)
    assert stock(app, pid) == 3


def test_reservation_atomique_du_stock(app, client, m):
    """Deux caisses et une seule unité : la seconde réservation échoue, rien n'est perdu."""
    from models.produit_model import ProduitModel
    pid = produit(client, m, quantite=1)
    with app.app_context():
        assert ProduitModel.reserver_stock({pid: 1}) is None
        assert ProduitModel.reserver_stock({pid: 1}) == "Riz"
        assert db.tables.produits.find_one({"_id": ObjectId(pid)})["quantite"] == 0
        ProduitModel.liberer_stock({pid: 1})
        assert db.tables.produits.find_one({"_id": ObjectId(pid)})["etat"] == "en stock"


# --------------------------------------------------------------------------- annulation, paiements
def test_annulation_par_le_caissier_sous_15_minutes(client, app, m):
    pid = produit(client, m)
    ouvrir(client, m)
    vente = vendre(client, m, [{"produit_id": pid, "quantite": 2}]).get_json()
    assert stock(app, pid) == 18
    r = client.post(f"/caisse/ventes/{vente['id']}/annuler", json={}, headers=h(m, "caissier"))
    assert r.status_code == 400  # motif obligatoire
    r = client.post(f"/caisse/ventes/{vente['id']}/annuler", json={"motif": "Erreur de saisie", "mode": "especes"},
                    headers=h(m, "caissier"))
    assert r.status_code == 200, r.get_json()
    assert stock(app, pid) == 20


def test_annulation_refusee_apres_15_minutes(client, app, m):
    pid = produit(client, m)
    ouvrir(client, m)
    vente = vendre(client, m, [{"produit_id": pid, "quantite": 2}]).get_json()
    with app.app_context():
        db.tables.livraisons.update_one({"_id": ObjectId(vente["id"])},
                                        {"$set": {"created_at": datetime.utcnow() - timedelta(minutes=16)}})
    r = client.post(f"/caisse/ventes/{vente['id']}/annuler", json={"motif": "Trop tard"}, headers=h(m, "caissier"))
    assert r.status_code == 403
    # L'admin, lui, peut toujours annuler.
    assert client.delete(f"/livraisons/{vente['id']}", json={"mode": "especes"}, headers=h(m, "admin")).status_code == 200
    assert stock(app, pid) == 20


def test_un_caissier_ne_voit_pas_les_ventes_d_un_autre(client, m):
    pid = produit(client, m)
    ouvrir(client, m)
    vente = vendre(client, m, [{"produit_id": pid, "quantite": 1}]).get_json()
    assert client.get(f"/caisse/ventes/{vente['id']}", headers=h(m, "caissier")).status_code == 200
    assert client.get(f"/caisse/ventes/{vente['id']}", headers=h(m, "caissier2")).status_code == 404
    ventes2 = client.get("/caisse/ventes", headers=h(m, "caissier2")).get_json()
    assert ventes2["ventes"] == []


# --------------------------------------------------------------------------- session de caisse
def test_fermeture_de_caisse_et_ecart(client, m):
    pid = produit(client, m)
    session = ouvrir(client, m, fond=5000)
    assert client.post("/caisse/session/ouvrir", json={"fond_initial": 0}, headers=h(m, "caissier")).status_code == 409
    vendre(client, m, [{"produit_id": pid, "quantite": 2}])                                    # 600 espèces
    vendre(client, m, [{"produit_id": pid, "quantite": 1}], paiements=[{"methode": "om", "montant": 300}])
    credit = vendre(client, m, [{"produit_id": pid, "quantite": 2}], client_id=m["client"],
                    statut_paiement="non payé", paiements=[]).get_json()
    r = client.post(f"/caisse/ventes/{credit['id']}/paiement", json={"montant": 250, "methode": "cash"},
                    headers=h(m, "caissier"))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["reste"] == 350

    live = client.get("/caisse/session", headers=h(m, "caissier")).get_json()["session"]["resume"]
    assert live["ventes"] == 3 and live["chiffre_affaires"] == 1500
    assert live["especes_attendues"] == 5000 + 600 + 250
    assert {x["mode"]: x["net"] for x in live["modes"]} == {"cash": 850, "om": 300}

    r = client.post("/caisse/session/fermer", json={"montant_compte": 5800}, headers=h(m, "caissier"))
    assert r.status_code == 200, r.get_json()
    ferme = r.get_json()["session"]
    assert ferme["statut"] == "fermee" and ferme["ecart"] == -50
    # Caisse fermée : plus de vente.
    assert vendre(client, m, [{"produit_id": pid, "quantite": 1}]).status_code == 409
    # L'admin voit la session et son écart.
    liste = client.get("/caisse/sessions", headers=h(m, "admin")).get_json()
    assert liste["totaux"]["sessions_avec_ecart"] == 1 and liste["sessions"][0]["id"] == session["id"]


def test_le_manager_peut_fermer_la_caisse_d_un_caissier_avec_un_motif(client, m):
    session = ouvrir(client, m, fond=1000)
    url = f"/caisse/sessions/{session['id']}/fermer"
    assert client.post(url, json={"montant_compte": 1000}, headers=h(m, "manager")).status_code == 400
    r = client.post(url, json={"montant_compte": 1000, "note": "Caissière partie sans fermer"}, headers=h(m, "manager"))
    assert r.status_code == 200 and r.get_json()["session"]["ecart"] == 0
    assert client.post(url, json={"montant_compte": 1000}, headers=h(m, "caissier")).status_code == 403


# --------------------------------------------------------------------------- statistiques et vue admin
def test_statistiques_du_caissier(client, m):
    pid = produit(client, m)
    ouvrir(client, m)
    vendre(client, m, [{"produit_id": pid, "quantite": 2}])
    vendre(client, m, [{"produit_id": pid, "quantite": 1}], paiements=[{"methode": "momo", "montant": 300}])
    r = client.get("/caisse/statistiques?periode=jour", headers=h(m, "caissier"))
    assert r.status_code == 200
    s = r.get_json()
    assert s["kpis"]["ventes"] == 2 and s["kpis"]["chiffre_affaires"] == 900 and s["kpis"]["articles"] == 3
    assert s["kpis"]["encaisse"] == 900 and s["top_produits"][0]["nom"] == "Riz"
    assert "marge" not in r.get_data(as_text=True)


def test_l_admin_voit_les_ventes_par_vendeur(client, m):
    pid = produit(client, m)
    ouvrir(client, m)
    vendre(client, m, [{"produit_id": pid, "quantite": 1}])
    r = client.get("/managers/livraisons", headers=h(m, "admin"))
    assert r.status_code == 200
    ventes = r.get_json()
    assert ventes[0]["vendeur_role"] == "caissier" and ventes[0]["livreur_id"] == m["caissier"]
    equipe = client.get("/caisse/caissiers", headers=h(m, "admin")).get_json()
    assert equipe["limite"] == 2 and equipe["utilises"] == 2
    moi = next(c for c in equipe["caissiers"] if c["id"] == m["caissier"])
    assert moi["session"] and moi["aujourdhui"]["ventes"] == 1


def test_un_caissier_qui_a_vendu_est_desactive_et_non_supprime(client, m):
    pid = produit(client, m)
    ouvrir(client, m)
    vendre(client, m, [{"produit_id": pid, "quantite": 1}])
    r = client.delete(f"/users/{m['caissier']}", headers=h(m, "admin"))
    assert r.status_code == 400


def test_l_assistant_ia_du_caissier_ne_voit_que_ses_chiffres(client, app, m):
    from services.ia_contexte import faits_en_texte, faits_utilisateur, vider_cache
    pid = produit(client, m)
    ouvrir(client, m, fond=2000)
    vendre(client, m, [{"produit_id": pid, "quantite": 2}])
    with app.app_context():
        vider_cache()
        caissier = db.tables.users.find_one({"_id": ObjectId(m["caissier"])})
        texte = faits_en_texte(faits_utilisateur(caissier))
    assert "Mes ventes aujourd'hui : 1 vente(s), chiffre d'affaires 600 FCFA" in texte
    assert "Ma caisse : ouverte, fond de caisse 2 000 FCFA, espèces attendues 2 600 FCFA" in texte
    assert "marge" not in texte.lower() and "prix d'achat" not in texte.lower()



# --------------------------------------------------------------------------- écrans partagés avec l'admin
def test_ecrans_admin_pour_le_caissier_sans_prix_d_achat(client, app, m):
    pid = produit(client, m)
    with app.app_context():
        db.tables.produits.update_one({"_id": ObjectId(pid)}, {"$set": {"code_barre": "123456789"}})
    stock = client.get("/caisse/stock_par_categorie", headers=h(m, "caissier"))
    code = client.get("/caisse/code-barre/123456789", headers=h(m, "caissier"))
    assert stock.status_code == 200 and code.status_code == 200
    assert code.get_json()["_id"] == pid
    ouvrir(client, m)
    vente = vendre(client, m, [{"produit_id": pid, "quantite": 2}], client_id=m["client"],
                   statut_paiement="partiellement payé", paiements=[{"methode": "cash", "montant": 100}]).get_json()
    liste = client.get("/caisse/livraisons", headers=h(m, "caissier"))
    detail = client.get(f"/caisse/livraisons/{vente['id']}", headers=h(m, "caissier"))
    assert liste.status_code == 200 and detail.status_code == 200
    assert liste.get_json()[0]["id"] == vente["id"] and liste.get_json()[0]["annulable_jusqua"]
    for r in (stock, code, liste, detail):
        assert "prix_achat" not in r.get_data(as_text=True)
    # Les clients se listent et se créent depuis l'écran de vente.
    assert client.get("/clients/", headers=h(m, "caissier")).status_code == 200
    # Paiement et annulation avec les mêmes corps que les routes admin.
    r = client.post(f"/caisse/livraisons/ajouter-paiement/{vente['id']}", json={"montant": 200}, headers=h(m, "caissier"))
    assert r.status_code == 200 and r.get_json()["livraison"]["reste"] == 300
    r = client.delete(f"/caisse/livraisons/{vente['id']}", json={"mode": "especes", "motif": "Erreur"}, headers=h(m, "caissier"))
    assert r.status_code == 200, r.get_json()
    # La liste ne montre que ses ventes.
    assert client.get("/caisse/livraisons", headers=h(m, "caissier2")).get_json() == []


# --------------------------------------------------------------------------- ticket admin
def test_l_admin_imprime_le_meme_ticket_que_le_caissier(client, app, m):
    pid = produit(client, m)
    ouvrir(client, m)
    vente = vendre(client, m, [{"produit_id": pid, "quantite": 2}]).get_json()
    du_caissier = client.get(f"/caisse/ventes/{vente['id']}", headers=h(m, "caissier")).get_json()

    for qui in ("admin", "manager"):
        r = client.get(f"/caisse/tickets/{vente['id']}", headers=h(m, qui))
        assert r.status_code == 200, r.get_json()
        corps = r.get_json()
        assert corps["vente"]["numero"] == du_caissier["numero"]
        assert corps["vente"]["lignes"] == du_caissier["lignes"]
        assert corps["vente"]["montant_total"] == 600
        assert corps["magasin"]["denomination"] == "Boutique" and corps["magasin"]["devise"] == "XAF"

    # Réservé à l'admin et au manager.
    assert client.get(f"/caisse/tickets/{vente['id']}", headers=h(m, "caissier")).status_code == 403


def test_ticket_d_une_vente_admin_et_d_un_autre_magasin(client, app, m):
    pid = produit(client, m)
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": pid, "quantite": 3}], "statut_paiement": "payé",
        "paiements": [{"methode": "cash", "montant": 900}], "montant_paye": 900}, headers=h(m, "admin"))
    assert r.status_code == 201, r.get_json()
    vente_id = r.get_json()["livraison_id"]
    corps = client.get(f"/caisse/tickets/{vente_id}", headers=h(m, "admin")).get_json()
    assert corps["vente"]["montant_total"] == 900 and corps["vente"]["vendeur"]

    # Un admin d'un autre magasin ne voit pas ce ticket.
    with app.app_context():
        autre = db.tables.magasin.insert_one({"denomination": "Autre", "is_active": True,
                                              "created_at": datetime.utcnow()}).inserted_id
        admin_b = str(db.tables.users.insert_one({"name": "b", "email": "b@autre.test", "role": "admin",
                                                  "magasin_id": autre, "is_active": True,
                                                  "created_at": datetime.utcnow()}).inserted_id)
    entete = {"Authorization": f"Bearer {generate_token(user_id=admin_b, role='admin')}"}
    assert client.get(f"/caisse/tickets/{vente_id}", headers=entete).status_code == 404
    assert client.get("/caisse/tickets/pas-un-id", headers=h(m, "admin")).status_code == 404


def test_en_tete_du_ticket_avec_les_mentions_legales(client, app, m):
    with app.app_context():
        db.tables.magasin.update_one({"_id": ObjectId(m["magasin"])}, {"$set": {
            "denomination": "COMPULEC", "raison_sociale": "COMPULEC", "forme_juridique": "SARL",
            "numero_immatriculation": "RC/YAO/2026/B/1234", "numero_fiscal": "M012345678901A",
            "telephone": "+237652478484", "email": "compulec@compulec.com",
            "adresse_ligne_1": "yaounde simbock, Biyem-Assi Carrefour", "adresse_ligne_2": "rue-50AT25",
            "ville": "Yaoundé", "pays": "Cameroun"}})
    pid = produit(client, m)
    ouvrir(client, m)
    vente = vendre(client, m, [{"produit_id": pid, "quantite": 1}]).get_json()
    for url, qui in (("/caisse/contexte", "caissier"), (f"/caisse/tickets/{vente['id']}", "admin")):
        mag = client.get(url, headers=h(m, qui)).get_json()["magasin"]
        assert mag["forme_juridique"] == "SARL" and mag["raison_sociale"] == "COMPULEC"
        assert mag["rccm"] == "RC/YAO/2026/B/1234" and mag["niu"] == "M012345678901A"
        assert mag["telephone"] == "+237652478484" and mag["email"] == "compulec@compulec.com"
        assert mag["adresse_lignes"] == ["yaounde simbock, Biyem-Assi Carrefour", "rue-50AT25", "Yaoundé, Cameroun"]
