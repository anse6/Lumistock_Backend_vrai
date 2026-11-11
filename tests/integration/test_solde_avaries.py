"""Mise en solde des produits avariés : les unités avariées (hors vols) entrent
dans un produit lié « Nom (soldé) », au coût 0, vendu ensuite normalement."""
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def magasin(app):
    with app.app_context():
        for table in ("magasin", "users", "produits", "approvisionnements", "stocks_livreur",
                      "livraisons", "clients", "retours_livraison"):
            db.tables[table].delete_many({})
        magasin_id = ObjectId()
        db.tables.magasin.insert_one({"_id": magasin_id, "denomination": "A", "is_active": True,
                                      "created_at": datetime.utcnow()})
        ids = {"magasin": str(magasin_id)}
        for role in ("admin", "manager", "gerant"):
            ids[role] = str(db.tables.users.insert_one({
                "name": role, "first_name": role, "email": f"{role}@a.test", "role": role,
                "magasin_id": magasin_id, "is_active": True, "created_at": datetime.utcnow(),
            }).inserted_id)
        yield ids


def _h(m, role):
    return {"Authorization": f"Bearer {generate_token(user_id=m[role], role=role)}"}


def _doc(app, produit_id):
    with app.app_context():
        return db.tables.produits.find_one({"_id": ObjectId(produit_id)})


def _produit_avarie(client, m, casse=5, vol=2):
    """Riz à 300 (acheté 100), 20 en stock ; `casse` cassés et `vol` volés."""
    admin = _h(m, "admin")
    r = client.post("/produits", data={"nom": "Riz", "prix": "300", "prix_achat": "100", "quantite": "20",
                                       "unite": "kg"}, headers=admin, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["produit_id"]
    for motif, quantite in (("casse", casse), ("vol", vol)):
        if quantite:
            r = client.post(f"/produits/{pid}/perime", json={"quantite": quantite, "type_avarie": motif},
                            headers=admin)
            assert r.status_code == 200, r.get_json()
    return pid


def _solder(client, m, pid, quantite, prix, role="admin"):
    return client.post(f"/produits/{pid}/solder", json={"quantite": quantite, "prix_solde": prix},
                       headers=_h(m, role))


def _avarie_listee(client, m, pid):
    produits = client.get("/produits", headers=_h(m, "admin")).get_json()
    return next(p for p in produits if p["_id"] == pid)["avarie"]


def test_mise_en_solde_cree_un_produit_solde(client, app, magasin):
    pid = _produit_avarie(client, magasin)
    r = _solder(client, magasin, pid, 4, 150)
    assert r.status_code == 200, r.get_json()
    solde = r.get_json()["produit_solde"]
    assert solde["nom"] == "Riz (soldé)"
    assert solde["quantite"] == 4 and solde["prix"] == 150 and solde["prix_achat"] == 0
    assert solde["etat"] == "en stock" and solde["code_barre"]

    doc = _doc(app, solde["_id"])
    assert doc["est_solde"] is True and str(doc["solde_de"]) == pid
    # Coût 0 : la perte est déjà comptée à l'avarie.
    valorisation = client.get(f"/produits/{solde['_id']}/valorisation", headers=_h(magasin, "admin")).get_json()
    assert valorisation["valeur_stock"] == 0

    origine = _doc(app, pid)
    assert origine["quantite"] == 13, "le stock normal ne bouge pas"
    assert origine["quantite_mise_en_solde"] == 4 and len(origine["mises_en_solde"]) == 1

    avarie = _avarie_listee(client, magasin, pid)
    assert avarie["quantite_soldable"] == 1, "7 retirés dont 2 volés : 5 soldables, 4 déjà soldés"
    assert avarie["produit_solde_id"] == solde["_id"]


def test_vols_et_unites_deja_soldees_ne_sont_pas_soldables(client, magasin):
    pid = _produit_avarie(client, magasin, casse=0, vol=3)
    r = _solder(client, magasin, pid, 1, 150)
    assert r.status_code == 400 and "0 unité" in r.get_json()["error"]

    pid = client.get("/produits", headers=_h(magasin, "admin")).get_json()[0]["_id"]
    r = client.post(f"/produits/{pid}/perime", json={"quantite": 2, "type_avarie": "deteriore"},
                    headers=_h(magasin, "admin"))
    assert _solder(client, magasin, pid, 2, 100).status_code == 200
    assert _solder(client, magasin, pid, 1, 100).status_code == 400


def test_prix_solde_doit_etre_inferieur_au_prix_normal(client, magasin):
    pid = _produit_avarie(client, magasin)
    assert _solder(client, magasin, pid, 1, 300).status_code == 400
    assert _solder(client, magasin, pid, 1, -5).status_code == 400
    assert _solder(client, magasin, pid, 0, 100).status_code == 400


def test_nouvelle_mise_en_solde_alimente_le_meme_produit(client, app, magasin):
    pid = _produit_avarie(client, magasin)
    premier = _solder(client, magasin, pid, 2, 150).get_json()["produit_solde"]
    second = _solder(client, magasin, pid, 3, 120).get_json()["produit_solde"]
    assert premier["_id"] == second["_id"]
    assert second["quantite"] == 5 and second["prix"] == 120
    mouvements = [m for m in _doc(app, second["_id"])["mouvements_stock"] if m["type"] == "mise en solde"]
    assert [m["quantite"] for m in mouvements] == [2, 3]


def test_vente_du_produit_solde_sans_cout(client, app, magasin):
    pid = _produit_avarie(client, magasin)
    solde_id = _solder(client, magasin, pid, 4, 150).get_json()["produit_solde"]["_id"]
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": solde_id, "quantite": 3}], "statut_paiement": "payé",
        "paiements": [{"methode": "cash", "montant": 450}]}, headers=_h(magasin, "admin"))
    assert r.status_code == 201, r.get_json()
    with app.app_context():
        vente = db.tables.livraisons.find_one({"_id": ObjectId(r.get_json()["livraison_id"])})
    ligne = vente["produits"][0]
    assert ligne["prix"] == 150 and ligne["prix_achat"] == 0
    assert _doc(app, solde_id)["quantite"] == 1
    assert _doc(app, pid)["quantite"] == 13


def test_mouvement_classe_hors_achats_et_pertes(app):
    with app.app_context():
        from services.rapport_service import famille_mouvement
        assert famille_mouvement("mise en solde", 4)[0] == "ajustements"


def test_produit_solde_ne_se_solde_pas(client, magasin):
    pid = _produit_avarie(client, magasin)
    solde_id = _solder(client, magasin, pid, 4, 150).get_json()["produit_solde"]["_id"]
    client.post(f"/produits/{solde_id}/perime", json={"quantite": 1, "type_avarie": "casse"},
                headers=_h(magasin, "admin"))
    assert _solder(client, magasin, solde_id, 1, 50).status_code == 400


def test_droits(client, magasin):
    pid = _produit_avarie(client, magasin)
    assert _solder(client, magasin, pid, 1, 150, role="gerant").status_code == 403
    assert _solder(client, magasin, pid, 1, 150, role="manager").status_code == 200

    r = client.put("/magasin/permissions", json={"droits": {"stock.soldes": {"manager": False}}},
                   headers=_h(magasin, "admin"))
    assert r.status_code == 200, r.get_json()
    r = _solder(client, magasin, pid, 1, 150, role="manager")
    assert r.status_code == 403 and "Solder les articles avariés" in r.get_json()["message"]


def test_produit_d_un_autre_magasin(client, app, magasin):
    pid = _produit_avarie(client, magasin)
    with app.app_context():
        db.tables.produits.update_one({"_id": ObjectId(pid)}, {"$set": {"magasin_id": ObjectId()}})
    assert _solder(client, magasin, pid, 1, 150).status_code == 404
