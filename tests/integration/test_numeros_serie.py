"""Suivi des articles par numéro de série (option du magasin)."""
import io
from datetime import datetime

import pytest
from bson import ObjectId
from openpyxl import load_workbook

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def m(app, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with app.app_context():
        for table in ("magasin", "users", "produits", "livraisons", "clients", "numeros_serie",
                      "approvisionnements", "stocks_livreur", "retours_livraison"):
            db.tables[table].delete_many({})
        ids = {}
        for nom in ("A", "B"):
            magasin_id = ObjectId()
            db.tables.magasin.insert_one({"_id": magasin_id, "denomination": nom, "is_active": True,
                                          "created_at": datetime.utcnow()})
            for role in ("admin", "manager", "gerant", "caissier"):
                cle = role if nom == "A" else f"{role}_B"
                ids[cle] = str(db.tables.users.insert_one({
                    "name": role, "first_name": nom, "email": f"{role}@{nom}.test", "role": role,
                    "magasin_id": magasin_id, "is_active": True, "created_at": datetime.utcnow(),
                }).inserted_id)
            ids[f"magasin_{nom}"] = str(magasin_id)
        yield ids


def _h(m, role="admin"):
    return {"Authorization": f"Bearer {generate_token(user_id=m[role], role=role.split('_')[0])}"}


def _activer(client, m, actif=True, role="admin"):
    return client.put("/magasin/parametres/stock", json={"suivi_numeros_serie": actif}, headers=_h(m, role))


def _creer(client, m, nom="Machine", numeros=("SN1", "SN2", "SN3"), suivi=True, role="admin", quantite=None):
    import json
    data = {"nom": nom, "prix": "1000", "prix_achat": "600", "unite": "pièce",
            "quantite": str(len(numeros) if quantite is None else quantite)}
    if suivi:
        data.update({"suivi_serie": "true", "numeros_serie": json.dumps(list(numeros))})
    return client.post("/produits", data=data, headers=_h(m, role), content_type="multipart/form-data")


def _machine(client, m, numeros=("SN1", "SN2", "SN3")):
    assert _activer(client, m).status_code == 200
    r = _creer(client, m, numeros=numeros)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["produit_id"]


def _vendre(client, m, pid, numeros, role="admin", **extra):
    ligne = {"produit_id": pid, "quantite": len(numeros), "numeros_serie": list(numeros), **extra}
    return client.post("/admin_or_manager/livraisons", json={"produits": [ligne], "statut_paiement": "payé",
                                                             "montant_paye": 1000 * len(numeros)},
                       headers=_h(m, role))


def _etat(client, pid):
    with client.application.app_context():
        return {n["numero_serie"]: n["statut"] for n in db.tables.numeros_serie.find({"produit_id": ObjectId(pid)})}


def _stock(client, pid):
    with client.application.app_context():
        return db.tables.produits.find_one({"_id": ObjectId(pid)})["quantite"]


# --------------------------------------------------------------------- réglage
def test_option_desactivee_par_defaut(client, m):
    r = client.get("/magasin/parametres/stock", headers=_h(m))
    assert r.get_json()["suivi_numeros_serie"] is False
    assert _creer(client, m).status_code == 400
    assert _activer(client, m, role="gerant").status_code == 403
    assert _activer(client, m).status_code == 200
    assert client.get("/magasin/parametres/stock", headers=_h(m)).get_json()["suivi_numeros_serie"] is True


# --------------------------------------------------------------------- entrées
def test_creation_exige_un_numero_par_unite(client, m):
    _activer(client, m)
    assert _creer(client, m, numeros=("A1", "A2"), quantite=3).status_code == 400
    assert _creer(client, m, numeros=("A1", "a1 ")).status_code == 400  # doublon (casse, espaces)
    pid = _creer(client, m, numeros=("A1", "A2")).get_json()["produit_id"]
    assert _etat(client, pid) == {"A1": "en_stock", "A2": "en_stock"}
    # Un numéro est unique dans le magasin, même sur un autre article.
    assert _creer(client, m, nom="Autre", numeros=("A2",)).status_code == 409
    # Un autre magasin peut avoir le même numéro.
    _activer(client, {"admin": m["admin_B"]})
    assert _creer(client, {"admin": m["admin_B"]}, numeros=("A1",)).status_code == 201


def test_approvisionnement_avec_numeros(client, m):
    pid = _machine(client, m)
    r = client.post(f"/produits/{pid}/approvisionner", json={"quantite": 2, "prix_achat": 700}, headers=_h(m))
    assert r.status_code == 400
    r = client.post(f"/produits/{pid}/approvisionner", json={"quantite": 2, "prix_achat": 700,
                                                             "numeros_serie": ["SN4", "SN5"]}, headers=_h(m))
    assert r.status_code == 200, r.get_json()
    assert _stock(client, pid) == 5 and len(_etat(client, pid)) == 5
    with client.application.app_context():
        assert db.tables.numeros_serie.find_one({"numero_serie": "SN5"})["cout_achat"] == 700


def test_quantite_ne_se_modifie_pas_a_la_main(client, m):
    pid = _machine(client, m)
    r = client.put(f"/produits/{pid}", data={"quantite": "10"}, headers=_h(m), content_type="multipart/form-data")
    assert r.status_code == 400 and _stock(client, pid) == 3


# --------------------------------------------------------------------- ventes
def test_vente_designe_les_unites(client, m):
    pid = _machine(client, m)
    assert _vendre(client, m, pid, ["SN1"], quantite=2).status_code == 400  # 1 numéro pour 2 unités
    assert _vendre(client, m, pid, ["SN9"]).status_code == 400              # inconnu
    r = _vendre(client, m, pid, ["sn1", "SN3"])                             # casse indifférente
    assert r.status_code == 201, r.get_json()
    assert _etat(client, pid) == {"SN1": "vendu", "SN2": "en_stock", "SN3": "vendu"}
    assert _stock(client, pid) == 1
    with client.application.app_context():
        vente = db.tables.livraisons.find_one({"_id": ObjectId(r.get_json()["livraison_id"])})
    assert vente["produits"][0]["numeros_serie"] == ["SN1", "SN3"]
    # Une unité vendue ne se revend pas.
    assert _vendre(client, m, pid, ["SN1"]).status_code == 409
    assert _stock(client, pid) == 1


def test_echec_de_vente_ne_laisse_rien(client, m):
    pid = _machine(client, m)
    r = client.post("/admin_or_manager/livraisons", json={"produits": [
        {"produit_id": pid, "quantite": 1, "numeros_serie": ["SN1"]},
        {"produit_id": pid, "quantite": 1, "numeros_serie": ["SN1"]},
    ], "statut_paiement": "payé", "montant_paye": 2000}, headers=_h(m))
    assert r.status_code == 400
    assert set(_etat(client, pid).values()) == {"en_stock"} and _stock(client, pid) == 3


def test_annulation_puis_restauration(client, m):
    pid = _machine(client, m)
    vente = _vendre(client, m, pid, ["SN2"]).get_json()["livraison_id"]
    r = client.delete(f"/livraisons/{vente}", json={"mode": "credit_client"}, headers=_h(m))
    assert r.status_code == 200, r.get_json()
    assert _etat(client, pid)["SN2"] == "en_stock"
    r = client.post(f"/livraisons/corbeille/{vente}/restaurer", headers=_h(m))
    assert r.status_code == 200, r.get_json()
    assert _etat(client, pid)["SN2"] == "vendu"


def test_restauration_refusee_si_l_unite_est_revendue(client, m):
    pid = _machine(client, m)
    vente = _vendre(client, m, pid, ["SN2"]).get_json()["livraison_id"]
    client.delete(f"/livraisons/{vente}", json={"mode": "credit_client"}, headers=_h(m))
    assert _vendre(client, m, pid, ["SN2"]).status_code == 201
    r = client.post(f"/livraisons/corbeille/{vente}/restaurer", headers=_h(m))
    assert r.status_code == 400 and "SN2" in r.get_json()["error"]


def test_modification_change_l_unite(client, m):
    pid = _machine(client, m)
    vente = _vendre(client, m, pid, ["SN1", "SN2"]).get_json()["livraison_id"]
    r = client.put(f"/admin_or_manager/livraisons/{vente}", json={
        "produits": [{"produit_id": pid, "quantite": 2, "numeros_serie": ["SN2", "SN3"]}]}, headers=_h(m))
    assert r.status_code == 200, r.get_json()
    assert _etat(client, pid) == {"SN1": "en_stock", "SN2": "vendu", "SN3": "vendu"}
    with client.application.app_context():
        sn2 = db.tables.numeros_serie.find_one({"numero_serie": "SN2"})
    assert [e["type"] for e in sn2["historique"]] == ["entree", "vente"]  # l'unité gardée ne bouge pas


def test_retour_client_choisit_les_unites(client, m):
    pid = _machine(client, m)
    vente = _vendre(client, m, pid, ["SN1", "SN2"]).get_json()["livraison_id"]
    retour = lambda n: client.post("/livraisons/retour", json={
        "livraison_id": vente, "motif": "Panne", "produits": [{"produit_id": pid, "quantite": 1, "numeros_serie": n}]},
        headers=_h(m))
    assert retour([]).status_code == 400
    assert retour(["SN3"]).status_code == 400  # pas dans cette vente
    r = retour(["SN2"])
    assert r.status_code == 201, r.get_json()
    assert _etat(client, pid) == {"SN1": "vendu", "SN2": "en_stock", "SN3": "en_stock"}
    with client.application.app_context():
        ligne = db.tables.livraisons.find_one({"_id": ObjectId(vente)})["produits"][0]
    assert ligne["numeros_serie"] == ["SN1"] and ligne["numeros_serie_retournes"] == ["SN2"]
    # Annuler ensuite ne rend que l'unité encore vendue.
    assert client.delete(f"/livraisons/{vente}", json={"mode": "especes"}, headers=_h(m)).status_code == 200
    assert set(_etat(client, pid).values()) == {"en_stock"}


def test_caissier_vend_et_scanne_un_numero(client, m):
    pid = _machine(client, m)
    r = client.get(f"/numeros-serie/disponibles/{pid}", headers=_h(m, "caissier"))
    assert r.status_code == 200 and r.get_json()["numeros"] == ["SN1", "SN2", "SN3"]
    assert "cout" not in r.get_data(as_text=True)
    r = client.get("/caisse/code-barre/sn2", headers=_h(m, "caissier"))
    assert r.status_code == 200 and r.get_json()["numero_serie_scanne"] == "SN2"
    assert r.get_json()["_id"] == pid


# --------------------------------------------------------------------- autres sorties
def test_avarie_designe_l_unite(client, m):
    pid = _machine(client, m)
    assert client.post(f"/produits/{pid}/perime", json={"quantite": 1}, headers=_h(m)).status_code == 400
    r = client.post(f"/produits/{pid}/perime", json={"quantite": 1, "numeros_serie": ["SN3"],
                                                     "type_avarie": "casse"}, headers=_h(m))
    assert r.status_code == 200, r.get_json()
    assert _etat(client, pid)["SN3"] == "avarie" and _stock(client, pid) == 2


def test_gerant_et_solde_pas_encore_possibles(client, m):
    pid = _machine(client, m)
    r = client.post("/approvisionnements", json={"livreur_id": m["gerant"],
                                                  "produits": [{"produit_id": pid, "quantite": 1}]}, headers=_h(m))
    assert r.status_code == 400 and "numéro de série" in r.get_json()["error"]


def test_arret_du_suivi_et_desactivation(client, m):
    pid = _machine(client, m)
    r = _activer(client, m, False)
    assert r.status_code == 400  # des unités suivies sont en stock
    r = client.put(f"/produits/{pid}", data={"suivi_serie": "false"}, headers=_h(m), content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    assert set(_etat(client, pid).values()) == {"retire"}
    assert _activer(client, m, False).status_code == 200
    # Réactiver le suivi : les mêmes numéros sont repris avec leur historique.
    _activer(client, m)
    import json
    r = client.put(f"/produits/{pid}", data={"suivi_serie": "true", "numeros_serie": json.dumps(["SN1", "SN2", "SN3"])},
                   headers=_h(m), content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    assert set(_etat(client, pid).values()) == {"en_stock"}


def test_activer_sur_un_article_existant(client, m):
    _activer(client, m)
    pid = _creer(client, m, numeros=(), suivi=False, quantite=2).get_json()["produit_id"]
    import json
    r = client.put(f"/produits/{pid}", data={"suivi_serie": "true", "numeros_serie": json.dumps(["X1"])},
                   headers=_h(m), content_type="multipart/form-data")
    assert r.status_code == 400  # 2 unités en stock, 1 numéro
    r = client.put(f"/produits/{pid}", data={"suivi_serie": "true", "numeros_serie": json.dumps(["X1", "X2"])},
                   headers=_h(m), content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    assert _etat(client, pid) == {"X1": "en_stock", "X2": "en_stock"}


# --------------------------------------------------------------------- rapports
def test_rapport_des_sorties_et_export(client, m):
    pid = _machine(client, m)
    vente = _vendre(client, m, pid, ["SN1"]).get_json()["livraison_id"]
    client.post(f"/produits/{pid}/perime", json={"quantite": 1, "numeros_serie": ["SN2"]}, headers=_h(m))
    client.delete(f"/livraisons/{vente}", json={"mode": "especes"}, headers=_h(m))
    r = client.get("/numeros-serie/sorties", headers=_h(m))
    assert r.status_code == 200
    lignes = {l["numero_serie"]: l for l in r.get_json()}
    assert set(lignes) == {"SN1", "SN2"}
    assert lignes["SN1"]["vente_id"] == vente and lignes["SN1"]["revenu"] is True
    assert lignes["SN2"]["type"] == "avarie"
    r = client.get("/numeros-serie/sorties/export", headers=_h(m))
    assert r.status_code == 200
    feuille = load_workbook(io.BytesIO(r.data)).active
    assert feuille.max_row == 3
    # Liste et recherche ; un autre magasin ne voit rien.
    assert [n["numero_serie"] for n in client.get("/numeros-serie?q=sn", headers=_h(m)).get_json()] == ["SN1", "SN2", "SN3"]
    assert client.get("/numeros-serie", headers=_h(m, "admin_B")).get_json() == []
    assert client.get(f"/numeros-serie/disponibles/{pid}", headers=_h(m, "admin_B")).status_code == 404


def test_generer_des_numeros_internes(client, m):
    _machine(client, m)
    numeros = client.get("/numeros-serie/generer?nombre=3", headers=_h(m)).get_json()["numeros"]
    assert len(set(numeros)) == 3 and all(n.startswith("LS") and len(n) == 12 for n in numeros)


def test_vente_a_la_caisse_avec_numeros(client, m):
    pid = _machine(client, m)
    caissier = _h(m, "caissier")
    assert client.post("/caisse/session/ouvrir", json={"fond_initial": 0}, headers=caissier).status_code == 201
    corps = {"produits": [{"produit_id": pid, "quantite": 1}], "paiements": [{"methode": "cash", "montant": 1000}],
             "statut_paiement": "payé", "montant_paye": 1000}
    r = client.post("/caisse/ventes", json=corps, headers=caissier)
    assert r.status_code == 400 and "numéro de série" in r.get_json()["error"]
    corps["produits"][0]["numeros_serie"] = ["SN2"]
    r = client.post("/caisse/ventes", json=corps, headers=caissier)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["lignes"][0]["numeros_serie"] == ["SN2"]
    assert _etat(client, pid)["SN2"] == "vendu"


def test_modification_depuis_l_ecran_reprend_les_numeros(client, m):
    """L'écran de modification relit la vente : les numéros vendus doivent y être."""
    pid = _machine(client, m)
    vente = _vendre(client, m, pid, ["SN1", "SN2"]).get_json()["livraison_id"]
    detail = client.get(f"/livraisons/{vente}", headers=_h(m))
    assert detail.status_code == 200, detail.get_json()
    ligne = detail.get_json()["produits"][0]
    assert ligne["suivi_serie"] is True
    assert ligne["numeros_serie"] == ["SN1", "SN2"]
    # L'écran renvoie la vente avec un numéro changé.
    r = client.put(f"/admin_or_manager/livraisons/{vente}", json={"produits": [
        {"produit_id": pid, "quantite": 2, "prix": ligne["prix"], "numeros_serie": ["SN1", "SN3"]}]},
        headers=_h(m))
    assert r.status_code == 200, r.get_json()
    assert _etat(client, pid) == {"SN1": "vendu", "SN2": "en_stock", "SN3": "vendu"}
