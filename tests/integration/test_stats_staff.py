"""Statistiques du tableau de bord super-admin (/staff/stats/...)."""

from datetime import date, datetime, timedelta

import pytest
from bson import ObjectId

from extensions import db
from utils.jwt_token import generate_token


@pytest.fixture
def plateforme(app, clear_db):
    """Un staff, deux magasins (XAF actif, EUR désactivé), des ventes et connexions."""
    maintenant = datetime.utcnow()
    staff_id, admin_id, gerant_id = ObjectId(), ObjectId(), ObjectId()
    mag_xaf, mag_eur = ObjectId(), ObjectId()
    t = db.tables
    t.magasin.insert_many([
        {"_id": mag_xaf, "denomination": "Boutique A", "devise": "XAF", "is_active": True, "ville": "Douala",
         "licence": {"max_managers": 1, "max_livreurs": 2}, "created_at": maintenant},
        {"_id": mag_eur, "denomination": "Boutique B", "devise": "EUR", "is_active": False, "created_at": maintenant},
    ])
    t.users.insert_many([
        {"_id": staff_id, "role": "staff", "email": "staff@test.com", "is_active": True, "created_at": maintenant},
        {"_id": admin_id, "role": "admin", "email": "a@test.com", "magasin_id": mag_xaf, "is_active": True, "created_at": maintenant},
        # L'ancien rôle « livreur » est compté comme gérant.
        {"_id": gerant_id, "role": "livreur", "email": "g@test.com", "magasin_id": mag_xaf, "is_active": True, "created_at": maintenant},
        {"role": "admin", "email": "b@test.com", "magasin_id": mag_eur, "is_active": True, "created_at": maintenant},
    ])
    vente = {"magasin_id": mag_xaf, "livreur_id": gerant_id, "created_at": maintenant - timedelta(hours=1),
             "statut_livraison": "livrée", "produits": [{"quantite": 3}]}
    t.livraisons.insert_many([
        # CA HT = 1000 ; la TVA (192,5) et la dette ancienne (5000) ne comptent pas.
        {**vente, "montant_produits": 1000.0, "montant_tva": 192.5, "dette_ancienne": 5000.0,
         "montant_total": 6192.5, "montant_paye": 6192.5, "statut_paiement": "payé",
         "paiements": [{"methode": "om", "montant": 6192.5}]},
        {**vente, "montant_produits": 500.0, "montant_total": 500.0, "montant_paye": 0, "statut_paiement": "non payé"},
        # Exclues : annulée, et à la corbeille.
        {**vente, "montant_produits": 9999.0, "statut_livraison": "annulée", "statut_paiement": "payé"},
        {**vente, "montant_produits": 9999.0, "deleted_at": maintenant, "statut_paiement": "payé"},
    ])
    t.connexions.insert_many([
        {"user_id": admin_id, "magasin_id": mag_xaf, "role": "admin", "email": "a@test.com", "timestamp": maintenant},
        {"user_id": admin_id, "magasin_id": mag_xaf, "role": "admin", "email": "a@test.com", "timestamp": maintenant - timedelta(minutes=5)},
    ])
    token = generate_token(str(staff_id), "staff")
    return {"headers": {"Authorization": f"Bearer {token}"}, "mag_xaf": str(mag_xaf), "admin_id": admin_id}


def test_vue_ensemble(client, plateforme):
    r = client.get("/staff/stats/vue-ensemble?jours=30", headers=plateforme["headers"])
    assert r.status_code == 200
    d = r.get_json()

    assert d["kpis"]["magasins"] == {"total": 2, "actifs": 1, "desactives": 1, "archives": 0, "nouveaux": 2, "nouveaux_prec": 0}
    # Le staff n'est pas compté ; l'admin d'un magasin désactivé est inactif.
    assert d["kpis"]["utilisateurs"]["total"] == 3
    assert d["kpis"]["utilisateurs"]["actifs"] == 2
    assert {r["role"]: r["total"] for r in d["roles"]} == {"admin": 2, "manager": 0, "gerant": 1}
    assert d["kpis"]["ventes"]["nombre"] == 2
    assert d["kpis"]["ventes"]["articles"] == 6
    assert d["kpis"]["connexions"] == {"nombre": 2, "nombre_prec": 0, "utilisateurs_actifs": 1, "utilisateurs_actifs_prec": 0}

    statuts = {s["statut"]: s["nombre"] for s in d["paiements"]["statuts"]}
    assert statuts == {"payé": 1, "partiellement payé": 0, "non payé": 1}
    assert {m["methode"]: m["nombre"] for m in d["paiements"]["methodes"]}["om"] == 1

    magasin = next(m for m in d["magasins"] if m["id"] == plateforme["mag_xaf"])
    assert magasin["chiffre_affaire"] == 1500  # HT : ni TVA, ni dette, ni ventes annulées
    assert magasin["devise"] == "XAF"
    assert magasin["gerants"] == 1 and magasin["max_gerants"] == 2
    assert magasin["jours_sans_activite"] == 0
    assert d["devises"] == ["EUR", "XAF"]
    assert len(d["croissance"]) == 12 and d["croissance"][-1]["ventes"] == 2


def test_vue_ensemble_periode_invalide(client, plateforme):
    r = client.get("/staff/stats/vue-ensemble?jours=12", headers=plateforme["headers"])
    assert r.status_code == 400


def test_vue_ensemble_reservee_au_staff(client, plateforme):
    token = generate_token(str(plateforme["admin_id"]), "admin")
    r = client.get("/staff/stats/vue-ensemble", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.parametrize("granularite,nombre", [("jour", 7), ("semaine", 8), ("mois", 12), ("annee", 5)])
def test_utilisation_sans_periodes_futures(client, plateforme, granularite, nombre):
    r = client.get(f"/staff/stats/utilisation?granularite={granularite}", headers=plateforme["headers"])
    assert r.status_code == 200
    d = r.get_json()
    serie = d["series"]["livraisons"]
    assert len(serie) == nombre
    # La dernière période est celle en cours, aucune n'est dans le futur.
    assert serie[-1]["debut"] <= date.today().isoformat() <= serie[-1]["fin"]
    assert d["totaux"]["livraisons"] == 2
    assert d["totaux"]["produits_livres"] == 6
    assert d["growth_rate"]["livraisons"] is None  # rien avant : pas de pourcentage inventé


def test_utilisation_page_future_ramenee_a_aujourdhui(client, plateforme):
    r = client.get("/staff/stats/utilisation?granularite=jour&page=3", headers=plateforme["headers"])
    assert r.get_json()["page"] == 0
    assert client.get("/staff/stats/utilisation?granularite=xx", headers=plateforme["headers"]).status_code == 400


def test_top_magasins_ca_hors_taxes(client, plateforme):
    r = client.get("/staff/stats/top-magasins", headers=plateforme["headers"])
    top = r.get_json()["meilleurs_magasins"]
    assert top[0]["chiffre_affaire"] == 1500
    assert top[0]["devise"] == "XAF"
    assert top[0]["nombre_ventes"] == 2


def test_utilisateurs_recents_un_par_compte(client, plateforme):
    r = client.get("/staff/stats/utilisateurs-recents", headers=plateforme["headers"])
    users = r.get_json()["utilisateurs_recents_connectes"]
    assert len(users) == 1
    assert users[0]["magasin"]["denomination"] == "Boutique A"
