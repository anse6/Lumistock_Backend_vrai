"""Droits d'accès configurables par magasin (Paramètres > Accès)."""
from datetime import datetime

import pytest
from bson import ObjectId

from extensions import db
from models.magasin_model import MagasinModel
from models.user_model import UserModel
from services.caisse_service import parametres_caisse
from utils.jwt_token import generate_token


@pytest.fixture
def magasin(app):
    with app.app_context():
        magasin_id = ObjectId()
        MagasinModel.get_collection().insert_one({
            "_id": magasin_id, "denomination": "Boutique Droits", "is_active": True,
            "created_at": datetime.utcnow(), "pays": "Cameroun", "ville": "Douala", "quartier": "Akwa",
            "email": "droits@test.com", "activité": "Commerce",
            "licence": {"max_managers": 2, "max_livreurs": 5},
        })
        ids = {}
        for role in ("admin", "manager", "livreur", "caissier"):
            ids[role] = str(UserModel.get_collection().insert_one({
                "name": role.capitalize(), "first_name": "Test", "email": f"{role}@droits.test",
                "password": "x", "role": role, "magasin_id": magasin_id, "is_active": True,
                "created_at": datetime.utcnow(),
            }).inserted_id)
        yield {"magasin_id": str(magasin_id), **ids}
        UserModel.get_collection().delete_many({"magasin_id": magasin_id})
        MagasinModel.get_collection().delete_many({"_id": magasin_id})
        db.tables.clients.delete_many({"magasin_id": magasin_id})


def _h(ids, role):
    return {"Authorization": f"Bearer {generate_token(user_id=ids[role], role=role)}"}


def _droit(config, cle):
    return next(d for d in config["droits"] if d["cle"] == cle)


def test_droits_par_defaut(client, magasin):
    r = client.get("/magasin/permissions", headers=_h(magasin, "admin"))
    assert r.status_code == 200
    data = r.get_json()
    assert data["modifiable"] is True
    creer = _droit(data, "produits.creer")["roles"]
    assert creer["manager"] == {"actif": False, "modifiable": True, "defaut": False}
    assert creer["gerant"]["modifiable"] is False
    assert _droit(data, "ventes.annuler")["roles"]["caissier"]["actif"] is True

    # Le manager consulte, sans pouvoir modifier.
    r = client.get("/magasin/permissions", headers=_h(magasin, "manager"))
    assert r.status_code == 200 and r.get_json()["modifiable"] is False
    r = client.put("/magasin/permissions", json={"droits": {"produits.creer": {"manager": True}}},
                   headers=_h(magasin, "manager"))
    assert r.status_code == 403


def test_activer_creation_produit_pour_le_manager(client, magasin):
    r = client.post("/produits", json={}, headers=_h(magasin, "manager"))
    assert r.status_code == 403
    assert "Créer des produits" in r.get_json()["message"]

    r = client.put("/magasin/permissions", json={"droits": {"produits.creer": {"manager": True}}},
                   headers=_h(magasin, "admin"))
    assert r.status_code == 200
    assert _droit(r.get_json(), "produits.creer")["roles"]["manager"]["actif"] is True

    # Le droit est accordé : la requête passe le contrôle d'accès (et échoue
    # ensuite sur les données vides, ce qui n'est plus un 403).
    r = client.post("/produits", json={}, headers=_h(magasin, "manager"))
    assert r.status_code != 403

    perms = client.get("/users/me/permissions", headers=_h(magasin, "manager")).get_json()["permissions"]
    assert "produits.creer" in perms

    # Retour aux valeurs par défaut.
    r = client.delete("/magasin/permissions", headers=_h(magasin, "admin"))
    assert r.status_code == 200
    assert client.post("/produits", json={}, headers=_h(magasin, "manager")).status_code == 403


def test_refus_des_reglages_impossibles(client, magasin):
    for droits in (
        {"produits.creer": {"gerant": True}},  # pas d'écran produit pour le gérant
        {"droit.inconnu": {"manager": True}},
        {"produits.creer": {"admin": False}},  # l'admin garde tout
        {"produits.creer": {"manager": "oui"}},
    ):
        r = client.put("/magasin/permissions", json={"droits": droits}, headers=_h(magasin, "admin"))
        assert r.status_code == 400, droits


def test_retirer_la_gestion_de_l_equipe_au_manager(client, magasin):
    client.put("/magasin/permissions", json={"droits": {"equipe.gerer": {"manager": False}}},
               headers=_h(magasin, "admin"))
    h = _h(magasin, "manager")
    r = client.put(f"/users/{magasin['livreur']}", json={"first_name": "Autre"}, headers=h)
    assert r.status_code == 403
    assert client.put(f"/users/deactivate-user/{magasin['caissier']}", headers=h).status_code == 403
    # Son propre profil reste modifiable.
    r = client.put(f"/users/{magasin['manager']}", json={"first_name": "Moi"}, headers=h)
    assert r.status_code == 200


def test_remises_du_caissier(client, magasin):
    def magasin_doc():
        return MagasinModel.get_collection().find_one({"_id": ObjectId(magasin["magasin_id"])})

    assert parametres_caisse(magasin_doc())["remises_autorisees"] is False
    # Ancien réglage de caisse : toujours respecté tant que rien n'est choisi ici.
    MagasinModel.get_collection().update_one({"_id": ObjectId(magasin["magasin_id"])},
                                             {"$set": {"parametres_caisse.remises_autorisees": True}})
    assert parametres_caisse(magasin_doc())["remises_autorisees"] is True

    client.put("/magasin/permissions", json={"droits": {"ventes.remises_caisse": {"caissier": False}}},
               headers=_h(magasin, "admin"))
    assert parametres_caisse(magasin_doc())["remises_autorisees"] is False
    perms = client.get("/users/me/permissions", headers=_h(magasin, "caissier")).get_json()["permissions"]
    assert "ventes.remises_caisse" not in perms and "ventes.annuler" in perms


def test_magasin_modifiable_par_le_manager_si_autorise(client, magasin):
    url = f"/magasin/update/{magasin['magasin_id']}"
    h = _h(magasin, "manager")
    assert client.put(url, json={"denomination": "Par le manager"}, headers=h).status_code == 403
    client.put("/magasin/permissions", json={"droits": {"magasin.modifier": {"manager": True}}},
               headers=_h(magasin, "admin"))
    assert client.put(url, json={"denomination": "Par le manager"}, headers=h).status_code == 200


def test_le_gerant_ne_peut_pas_se_donner_de_droits(client, magasin):
    h = _h(magasin, "livreur")
    assert client.get("/magasin/permissions", headers=h).status_code == 403
    assert client.put("/magasin/permissions", json={"droits": {"ventes.abandon_reste": {"gerant": True}}},
                      headers=h).status_code == 403
