"""Page Rapports : ventes, encaissements, journal et mouvement annuel du stock, inventaire."""
from datetime import datetime

from bson import ObjectId

from extensions import db
from tests.integration.test_livraisons_magasin import _h, _produit, magasins  # noqa: F401
from tests.integration.test_retour_remboursement import _vente

AUJOURD_HUI = datetime.now().strftime("%Y-%m-%d")
PERIODE = f"debut={AUJOURD_HUI[:8]}01&fin={AUJOURD_HUI}"


def _get(client, m, url, role="admin"):
    r = client.get(url, headers=_h(m, role))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _scenario(client, m):
    """Produit de 20 ; vente payée de 10 ; retour de 4 remboursé en espèces ;
    vente partielle de 2 (100 payés) réglée ensuite par Orange Money."""
    produit_id = _produit(client, m, quantite=20)
    vente = _vente(client, m, produit_id, 10, 3000)
    client.post("/livraisons/retour", json={"livraison_id": vente, "motif": "Défaut",
                                             "produits": [{"produit_id": produit_id, "quantite": 4}]},
                headers=_h(m, "admin"))
    client.post(f"/livraisons/refund/{vente}", json={"mode": "especes"}, headers=_h(m, "admin"))
    partielle = _vente(client, m, produit_id, 2, 100, statut="partiellement payé")
    r = client.post(f"/livraisons/ajouter-paiement/{partielle}", json={"montant": 500, "methode": "om"},
                    headers=_h(m, "admin"))
    assert r.status_code == 200, r.get_json()
    return produit_id, vente, partielle


def test_rapport_des_ventes(client, magasins):
    a = magasins["A"]
    produit_id, vente, partielle = _scenario(client, a)
    d = _get(client, a, f"/rapports/ventes?{PERIODE}")
    t = d["totaux"]
    assert t["ventes"] == 2 and t["articles"] == 8  # 6 restants + 2
    assert t["montant_ttc"] == 2400 and t["montant_paye"] == 2400 and t["reste"] == 0
    assert t["montant_rembourse"] == 1200 and t["marge"] == 1600  # (300 - 100) × 8
    assert {v["facture"] for v in d["ventes"]} == {f"FAC-{vente[-8:].upper()}", f"FAC-{partielle[-8:].upper()}"}
    assert d["top_produits"][0] == {"nom": "Riz", "quantite": 8, "montant": 2400, "marge": 1600}

    # Filtres : statut, produit, client
    assert _get(client, a, f"/rapports/ventes?{PERIODE}&statut=non_paye")["totaux"]["ventes"] == 0
    assert _get(client, a, f"/rapports/ventes?{PERIODE}&produit_id={ObjectId()}")["totaux"]["ventes"] == 0
    assert _get(client, a, f"/rapports/ventes?{PERIODE}&client_id={a['client']}")["totaux"]["ventes"] == 2
    # Période sans vente
    assert _get(client, a, "/rapports/ventes?debut=2001-01-01&fin=2001-12-31")["ventes"] == []


def test_paiement_complementaire_trace_et_encaissements(client, app, magasins):
    a = magasins["A"]
    _, vente, partielle = _scenario(client, a)
    with app.app_context():
        paiements = db.tables.livraisons.find_one({"_id": ObjectId(partielle)})["paiements"]
    assert paiements[-1]["methode"] == "om" and paiements[-1]["montant"] == 500 and paiements[-1].get("date")

    d = _get(client, a, f"/rapports/encaissements?{PERIODE}")
    assert d["totaux"]["entrees"] == 3600 and d["totaux"]["sorties"] == 1200 and d["totaux"]["net"] == 2400
    modes = {m["mode"]: m for m in d["par_mode"]}
    assert modes["om"]["entrees"] == 500 and modes["cash"]["sorties"] == 1200
    natures = {o["nature"] for o in d["operations"]}
    assert {"Paiement à la vente", "Paiement complémentaire", "Remboursement"} <= natures
    assert len(_get(client, a, f"/rapports/encaissements?{PERIODE}&sens=sortie")["operations"]) == 1
    assert _get(client, a, f"/rapports/encaissements?{PERIODE}&mode=momo")["operations"] == []

    r = client.post(f"/livraisons/ajouter-paiement/{vente}", json={"montant": 1, "methode": "bitcoin"},
                    headers=_h(a, "admin"))
    assert r.status_code == 400


def test_journal_des_mouvements_sans_double_comptage(client, magasins):
    a = magasins["A"]
    produit_id, _, _ = _scenario(client, a)
    d = _get(client, a, f"/rapports/mouvements?{PERIODE}")
    familles = {f["famille"]: f for f in d["par_famille"]}
    assert familles["ventes"]["sorties"] == 12 and familles["retours"]["entrees"] == 4
    assert d["totaux"]["solde"] == -8
    assert all(m["libelle"] for m in d["mouvements"])
    seulement = _get(client, a, f"/rapports/mouvements?{PERIODE}&famille=retours")
    assert [m["quantite"] for m in seulement["mouvements"]] == [4]

    # Ancienne route : plus de double comptage des retours.
    ancien = _get(client, a, f"/produits/mouvements/magasin?year={AUJOURD_HUI[:4]}")
    assert sum(m["quantite"] for m in ancien["mouvements"] if m["produit_id"] == produit_id) == -8


def test_mouvement_de_stock_annuel(client, app, magasins):
    a = magasins["A"]
    produit_id, _, _ = _scenario(client, a)
    d = _get(client, a, f"/rapports/stock-annuel?annee={AUJOURD_HUI[:4]}&avec_mouvements=1")
    ligne = next(p for p in d["produits"] if p["produit_id"] == produit_id)
    assert ligne["creation"] == 20 and ligne["stock_initial"] == 0
    assert ligne["ventes"] == 12 and ligne["retours"] == 4 and ligne["stock_final"] == 12
    assert ligne["stock_initial"] + ligne["entrees"] - ligne["sorties"] == ligne["stock_final"]
    assert ligne["valeur_finale"] == 1200  # 12 × prix d'achat 100
    mois = d["mois"][datetime.now().month - 1]
    assert mois["sorties"] == 12 and mois["entrees"] == 4

    # L'année précédente, le produit n'existait pas encore.
    avant = _get(client, a, f"/rapports/stock-annuel?annee={int(AUJOURD_HUI[:4]) - 1}")
    assert all(p["produit_id"] != produit_id for p in avant["produits"])

    # Produit créé l'an dernier : stock au 1er janvier reconstitué.
    with app.app_context():
        db.tables.produits.update_one({"_id": ObjectId(produit_id)},
                                      {"$set": {"created_at": datetime(int(AUJOURD_HUI[:4]) - 1, 6, 1)}})
    ligne = next(p for p in _get(client, a, f"/rapports/stock-annuel?annee={AUJOURD_HUI[:4]}")["produits"]
                 if p["produit_id"] == produit_id)
    assert ligne["stock_initial"] == 20 and ligne["creation"] == 0
    assert client.get("/rapports/stock-annuel?annee=1990", headers=_h(a, "admin")).status_code == 400


def test_inventaire_et_isolation(client, magasins):
    a, b = magasins["A"], magasins["B"]
    _scenario(client, a)
    d = _get(client, a, "/rapports/inventaire")
    assert d["totaux"]["quantite"] == 12 and d["totaux"]["valeur_achat"] == 1200
    assert d["totaux"]["valeur_vente"] == 3600
    assert _get(client, a, "/rapports/inventaire?etat=rupture")["produits"] == []

    # Le magasin B ne voit rien du magasin A.
    assert _get(client, b, f"/rapports/ventes?{PERIODE}")["ventes"] == []
    assert _get(client, b, f"/rapports/encaissements?{PERIODE}")["operations"] == []
    assert _get(client, b, "/rapports/inventaire")["produits"] == []
    # Le stock est réservé à l'administration ; un gérant ne voit que ses ventes.
    assert client.get("/rapports/inventaire", headers=_h(a, "gerant")).status_code == 403
    assert _get(client, a, f"/rapports/ventes?{PERIODE}", "gerant")["ventes"] == []
