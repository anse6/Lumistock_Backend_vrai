"""Tableau de bord de l'admin / du manager (/managers/stats/vue-ensemble)."""

from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def magasin(app, clear_db):
    maintenant = datetime.utcnow()
    mag, autre_mag = ObjectId(), ObjectId()
    admin, gerant, client, client_ancien = ObjectId(), ObjectId(), ObjectId(), ObjectId()
    t = db.tables
    t.magasin.insert_many([
        {"_id": mag, "denomination": "Boutique A", "devise": "XAF", "is_active": True, "created_at": maintenant},
        {"_id": autre_mag, "denomination": "Autre", "devise": "XAF", "is_active": True, "created_at": maintenant},
    ])
    t.users.insert_many([
        {"_id": admin, "role": "admin", "email": "a@test.com", "first_name": "Ada", "name": "Admin",
         "magasin_id": mag, "is_active": True, "created_at": maintenant},
        {"_id": gerant, "role": "gerant", "email": "g@test.com", "first_name": "Gil", "name": "Gerant",
         "magasin_id": mag, "is_active": True, "created_at": maintenant},
    ])
    t.clients.insert_many([
        {"_id": client, "nom": "Client 1", "telephone": "+237600000000", "magasin_id": mag, "created_at": maintenant},
        {"_id": client_ancien, "nom": "Client 2", "magasin_id": mag, "created_at": maintenant},
    ])
    t.produits.insert_many([
        {"nom": "Stylo", "quantite": 0, "prix": 100, "prix_achat": 50, "magasin_id": mag},
        {"nom": "Cahier", "quantite": 2, "seuil_alerte": 5, "prix": 500, "prix_achat": 300, "magasin_id": mag},
        {"nom": "Sac", "quantite": 10, "prix": 2000, "prix_achat": 1000, "magasin_id": mag},
    ])
    base = {"magasin_id": mag, "client_id": client, "statut_livraison": "livrée"}
    t.livraisons.insert_many([
        # Vente avec TVA : le chiffre d'affaires est le HT (1000), pas le total TTC.
        {**base, "livreur_id": gerant, "date_livraison": maintenant - timedelta(hours=2), "created_at": maintenant - timedelta(hours=2),
         "produits": [{"produit_id": ObjectId(), "nom": "Sac", "quantite": 2, "prix": 500, "prix_achat": 300}],
         "montant_produits": 1000.0, "montant_tva": 192.5, "montant_total": 1192.5, "montant_paye": 1192.5,
         "statut_paiement": "payé", "methode_paiement": "om", "paiements": [{"methode": "om", "montant": 1192.5}]},
        # Vente impayée de plus de 30 jours : créance en retard.
        {**base, "client_id": client_ancien, "livreur_id": admin,
         "date_livraison": maintenant - timedelta(days=45), "created_at": maintenant - timedelta(days=45),
         "produits": [{"produit_id": ObjectId(), "nom": "Cahier", "quantite": 1, "prix": 500, "prix_achat": 300}],
         "montant_produits": 500.0, "montant_total": 500.0, "montant_paye": 0.0, "statut_paiement": "non payé"},
        # Exclues : vente annulée (à la corbeille) et vente d'un autre magasin.
        {**base, "livreur_id": gerant, "date_livraison": maintenant, "created_at": maintenant, "deleted_at": maintenant,
         "montant_produits": 9999.0, "montant_total": 9999.0, "montant_paye": 0.0, "statut_paiement": "non payé", "produits": []},
        {**base, "magasin_id": autre_mag, "livreur_id": gerant, "date_livraison": maintenant, "created_at": maintenant,
         "montant_produits": 7777.0, "montant_total": 7777.0, "montant_paye": 7777.0, "statut_paiement": "payé", "produits": []},
    ])
    return {"admin": admin, "gerant": gerant, "headers": {"Authorization": f"Bearer {generate_token(str(admin), 'admin')}"}}


def test_vue_ensemble_magasin(client, magasin):
    r = client.get("/managers/stats/vue-ensemble?jours=30", headers=magasin["headers"])
    assert r.status_code == 200
    d = r.get_json()
    k = d["kpis"]

    assert k["chiffre_affaires"]["valeur"] == 1000  # HT, sans TVA, sans vente annulée ni autre magasin
    assert k["tva_collectee"] == 192.5
    assert k["ventes"]["valeur"] == 1 and k["ventes"]["articles"] == 2
    assert k["marge"]["valeur"] == 400  # 1000 - 2 x 300
    assert k["encaisse"]["valeur"] == 1192.5
    assert k["creances"] == {"valeur": 500.0, "clients": 1, "en_retard": 500.0}
    assert d["debiteurs"][0]["nom"] == "Client 2" and d["debiteurs"][0]["jours"] >= 44

    assert {s["statut"]: s["nombre"] for s in d["paiements"]["statuts"]} == {"payé": 1, "partiellement payé": 0, "non payé": 0}
    assert {m["methode"]: m["montant"] for m in d["paiements"]["methodes"]}["om"] == 1192.5
    assert d["top_produits"][0]["nom"] == "Sac"
    assert d["vendeurs"][0]["nom"] == "Gil Gerant" and d["vendeurs"][0]["role"] == "gerant"

    s = d["stock"]
    assert (s["produits"], s["ruptures"], s["stock_bas"]) == (3, 1, 1)
    assert s["valeur"] == 2 * 300 + 10 * 1000
    assert [p["nom"] for p in s["a_reapprovisionner"]] == ["Stylo", "Cahier"]

    assert d["periode"]["granularite"] == "jour" and len(d["serie"]) == 30
    assert sum(p["ca"] for p in d["serie"]) == 1000
    assert len(d["ventes_recentes"]) == 2  # pas la vente annulée (corbeille)


def test_vue_ensemble_periodes(client, magasin):
    assert client.get("/managers/stats/vue-ensemble?jours=12", headers=magasin["headers"]).status_code == 400
    d = client.get("/managers/stats/vue-ensemble?jours=365", headers=magasin["headers"]).get_json()
    assert d["periode"]["granularite"] == "mois"
    # La vente de plus de 30 jours entre dans la période de 12 mois.
    assert d["kpis"]["ventes"]["valeur"] == 2


def test_vue_ensemble_reservee_admin_manager(client, magasin):
    token = generate_token(str(magasin["gerant"]), "gerant")
    r = client.get("/managers/stats/vue-ensemble", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
