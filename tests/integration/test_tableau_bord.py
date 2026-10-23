"""Onglets détaillés du tableau de bord admin (/managers/tableau-de-bord/<onglet>)."""

from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from extensions import db
from services.rapport_service import heure_locale
from utils.jwt_token import generate_token


@pytest.fixture
def magasin(app, clear_db):
    now = datetime.utcnow()
    mag, admin, gerant = ObjectId(), ObjectId(), ObjectId()
    cat_info, cat_maison = ObjectId(), ObjectId()
    sac, stylo, vieux = ObjectId(), ObjectId(), ObjectId()
    fidele, nouveau, perdu = ObjectId(), ObjectId(), ObjectId()
    t = db.tables
    t.magasin.insert_one({"_id": mag, "denomination": "Boutique", "devise": "XAF", "is_active": True, "created_at": now})
    t.users.insert_many([
        {"_id": admin, "role": "admin", "first_name": "Ada", "name": "Admin", "email": "a@t.com", "magasin_id": mag, "is_active": True},
        {"_id": gerant, "role": "gerant", "first_name": "Gil", "name": "Gerant", "email": "g@t.com", "magasin_id": mag, "is_active": True},
    ])
    t.categories.insert_many([{"_id": cat_info, "nom": "Informatique", "magasin_id": mag},
                              {"_id": cat_maison, "nom": "Maison", "magasin_id": mag}])
    t.produits.insert_many([
        # Sac : 10 en magasin, 3 chez le gérant. Il y a 5 jours : +20 reçus, 5 remis au gérant,
        # 2 vendus par le gérant. Il y a 2 jours : 4 vendus en magasin.
        {"_id": sac, "nom": "Sac", "quantite": 10, "prix": 2000, "prix_achat": 1000, "categorie_id": cat_info,
         "magasin_id": mag, "created_at": now - timedelta(days=100), "mouvements_stock": [
             {"type": "approvisionnement magasin", "quantite": 20, "date": now - timedelta(days=5)},
             {"type": "approvisionnement_livreur", "quantite": -5, "date": now - timedelta(days=5)},
             {"type": "livraison livreur", "quantite": -2, "date": now - timedelta(days=4)},
             {"type": "livraison manager", "quantite": -4, "date": now - timedelta(days=2)},
         ]},
        # Stylo : en rupture en magasin
        {"_id": stylo, "nom": "Stylo", "quantite": 0, "prix": 100, "prix_achat": 50, "categorie_id": cat_maison,
         "magasin_id": mag, "created_at": now - timedelta(days=100), "mouvements_stock": []},
        # Vieux : en stock, jamais vendu, créé il y a 100 jours → « dort »
        {"_id": vieux, "nom": "Vieux", "quantite": 2, "prix": 900, "prix_achat": 500, "categorie_id": cat_maison,
         "magasin_id": mag, "created_at": now - timedelta(days=100), "mouvements_stock": []},
    ])
    t.stocks_livreur.insert_one({"livreur_id": gerant, "produit_id": sac, "quantite": 3})
    t.clients.insert_many([
        {"_id": fidele, "nom": "Fidèle", "magasin_id": mag, "created_at": now - timedelta(days=200)},
        {"_id": nouveau, "nom": "Nouveau", "magasin_id": mag, "created_at": now - timedelta(days=3)},
        {"_id": perdu, "nom": "Perdu", "telephone": "+237600", "magasin_id": mag, "created_at": now - timedelta(days=200)},
    ])

    def vente(client, vendeur, jours, qte, paye=True):
        quand = now - timedelta(days=jours)
        total = qte * 2000.0
        return {"magasin_id": mag, "client_id": client, "livreur_id": vendeur, "statut_livraison": "livrée",
                "date_livraison": quand, "created_at": quand, "montant_produits": total, "montant_total": total,
                "montant_paye": total if paye else 0.0, "statut_paiement": "payé" if paye else "non payé",
                "produits": [{"produit_id": sac, "nom": "Sac", "quantite": qte, "prix": 2000, "prix_achat": 1000}]}

    t.livraisons.insert_many([
        vente(gerant and fidele, gerant, 4, 2),        # gérant, client déjà connu
        vente(nouveau, admin, 2, 4, paye=False),       # admin, nouveau client, impayé récent
        vente(fidele, admin, 150, 1),                  # ancien achat du client fidèle
        vente(perdu, admin, 120, 1, paye=False),       # client perdu, impayé de 120 jours
    ])
    t.charges.insert_one({"nom": "Loyer", "montant": 1000.0, "categorie": "loyer", "magasin_id": mag,
                          "date_charge": now - timedelta(days=1), "statut": "payee"})
    return {"h": {"Authorization": f"Bearer {generate_token(str(admin), 'admin')}"}, "gerant": gerant, "now": now}


def get(client, magasin, onglet, jours=30):
    r = client.get(f"/managers/tableau-de-bord/{onglet}?jours={jours}", headers=magasin["h"])
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_stock(client, magasin):
    d = get(client, magasin, "stock")
    k = d["kpis"]
    assert k["valeur_magasin"] == 10 * 1000 + 2 * 500
    assert k["valeur_gerants"] == 3 * 1000
    assert k["valeur_totale"] == 14000
    # Il y a 30 jours : 10 − 20 + 5 + 4 = −1 en magasin → 0 ; gérant 3 − 5 + 2 = 0 ; vieux 2 × 500.
    assert k["valeur_debut"] == 1000
    assert k["ruptures"] == 1  # le stylo (rupture en magasin)
    assert k["dormants"] == 1 and d["dormants"][0]["nom"] == "Vieux" and d["dormants"][0]["jamais_vendu"]
    # Entrées / sorties : la remise au gérant n'est ni une entrée ni une sortie.
    f = d["totaux_flux"]
    assert f["approvisionnements"]["unites"] == 20
    assert f["ventes"]["unites"] == -6
    # La valeur reconstruite en fin de période est la valeur actuelle.
    fin = d["evolution"][-1]
    assert fin["magasin"] + fin["gerants"] == 14000
    assert d["gerants"][0]["valeur"] == 3000
    assert {c["nom"] for c in d["categories"]} == {"Informatique", "Maison"}
    sac = next(p for p in d["couverture"] if p["nom"] == "Sac")
    assert sac["stock"] == 13 and sac["vendus"] == 6


def test_ventes(client, magasin):
    d = get(client, magasin, "ventes")
    assert d["kpis"]["ventes"] == 2 and d["kpis"]["ca"] == 12000
    quand = heure_locale(magasin["now"] - timedelta(days=2))
    assert d["chaleur"][quand.weekday()][quand.hour]["ventes"] == 1
    info = next(c for c in d["categories"] if c["nom"] == "Informatique")
    assert info["ca"] == 12000 and info["taux_marge"] == 50.0


def test_clients(client, magasin):
    d = get(client, magasin, "clients")
    k = d["kpis"]
    assert k["acheteurs"] == 2 and k["fideles"] == 1 and k["taux_fidelite"] == 50.0
    tranches = {t["tranche"]: t["montant"] for t in d["creances"]}
    assert tranches["0–30 j"] == 8000 and tranches["+ 90 j"] == 2000
    assert [c["nom"] for c in d["a_relancer"]] == ["Perdu"]


def test_finances(client, magasin):
    d = get(client, magasin, "finances")
    assert d["kpis"]["ca"] == 12000
    assert d["cascade"][0]["cle"] == "ventes_brutes" and d["cascade"][-1]["cle"] == "net"
    assert d["cascade"][-1]["montant"] == d["kpis"]["resultat"]
    assert len(d["mois"]) == 12


def test_equipe(client, magasin):
    d = get(client, magasin, "equipe")
    membres = {m["nom"]: m for m in d["membres"]}
    assert membres["Gil Gerant"]["ventes"] == 1 and membres["Gil Gerant"]["stock_valeur"] == 3000
    assert membres["Ada Admin"]["ventes"] == 1 and membres["Ada Admin"]["reste"] == 8000
    assert sum(m["part"] for m in d["membres"]) == pytest.approx(100, abs=0.2)


def test_controles(client, magasin):
    assert client.get("/managers/tableau-de-bord/inconnu", headers=magasin["h"]).status_code == 404
    assert client.get("/managers/tableau-de-bord/stock?jours=12", headers=magasin["h"]).status_code == 400
    token = generate_token(str(magasin["gerant"]), "gerant")
    assert client.get("/managers/tableau-de-bord/stock", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    for onglet in ("stock", "ventes", "clients", "finances", "equipe"):
        for jours in (7, 90, 365):
            get(client, magasin, onglet, jours)
