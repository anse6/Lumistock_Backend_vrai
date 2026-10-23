"""Contrôle des lignes de vente : quantités, cumul par produit et prix minimum."""
import pytest
from bson import ObjectId

from extensions import db
from tests.integration.test_livraisons_magasin import magasins, _h, _produit, _stock_magasin  # noqa: F401


def _stock_gerant(app, m, produit_id):
    with app.app_context():
        ligne = db.tables.stocks_livreur.find_one({"livreur_id": ObjectId(m["gerant"]),
                                                   "produit_id": ObjectId(produit_id)})
        return ligne["quantite"] if ligne else 0


def _approvisionner_gerant(client, m, produit_id, quantite=10):
    r = client.post("/approvisionnements", json={"livreur_id": m["gerant"],
                                                  "produits": [{"produit_id": produit_id, "quantite": quantite}]},
                    headers=_h(m, "admin"))
    assert r.status_code == 201, r.get_json()


def _vente_gerant(client, m, lignes):
    return client.post("/livraisons", json={"produits": lignes, "statut_paiement": "non payé",
                                            "client_id": m["client"]}, headers=_h(m, "gerant"))


def _vente_manager(client, m, lignes):
    return client.post("/admin_or_manager/livraisons", json={
        "produits": lignes, "statut_paiement": "non payé", "client_id": m["client"],
    }, headers=_h(m, "manager"))


@pytest.mark.parametrize("quantite", [-50, 0, 2.5, "abc", None])
def test_quantite_invalide_refusee_et_stock_intact(client, app, magasins, quantite):
    m = magasins["A"]
    produit_id = _produit(client, m)
    _approvisionner_gerant(client, m, produit_id)
    avant_gerant, avant_magasin = _stock_gerant(app, m, produit_id), _stock_magasin(app, produit_id)

    assert _vente_gerant(client, m, [{"produit_id": produit_id, "quantite": quantite}]).status_code == 400
    assert _vente_manager(client, m, [{"produit_id": produit_id, "quantite": quantite}]).status_code == 400
    assert _stock_gerant(app, m, produit_id) == avant_gerant
    assert _stock_magasin(app, produit_id) == avant_magasin


def test_meme_produit_sur_plusieurs_lignes_est_cumule(client, app, magasins):
    m = magasins["A"]
    produit_id = _produit(client, m)
    _approvisionner_gerant(client, m, produit_id, quantite=10)
    # 6 + 6 = 12 > 10 en stock : refusé (avant, chaque ligne passait seule).
    lignes = [{"produit_id": produit_id, "quantite": 6}, {"produit_id": produit_id, "quantite": 6}]
    assert _vente_gerant(client, m, lignes).status_code == 400
    assert _stock_gerant(app, m, produit_id) == 10
    # Le stock du magasin est de 10 après l'approvisionnement du gérant.
    lignes = [{"produit_id": produit_id, "quantite": 6}, {"produit_id": produit_id, "quantite": 5}]
    assert _vente_manager(client, m, lignes).status_code == 400
    # 5 + 5 = 10 : accepté.
    lignes = [{"produit_id": produit_id, "quantite": 5}, {"produit_id": produit_id, "quantite": 5}]
    assert _vente_gerant(client, m, lignes).status_code == 201
    assert _stock_gerant(app, m, produit_id) == 0


@pytest.mark.parametrize("prix", [0, -300, "abc", 99.99])
def test_prix_nul_negatif_ou_sous_le_prix_d_achat_refuse(client, app, magasins, prix):
    m = magasins["A"]
    produit_id = _produit(client, m)  # prix 300, prix d'achat 100
    _approvisionner_gerant(client, m, produit_id)
    for vente in (_vente_gerant, _vente_manager):
        r = vente(client, m, [{"produit_id": produit_id, "quantite": 1, "prix": prix}])
        assert r.status_code == 400
        # Le prix d'achat n'est jamais affiché au gérant.
        assert "100" not in r.get_json()["error"]


@pytest.mark.parametrize("prix", [100, 250, 400])
def test_prix_modifie_au_dessus_du_prix_d_achat_accepte(client, app, magasins, prix):
    m = magasins["A"]
    produit_id = _produit(client, m)
    _approvisionner_gerant(client, m, produit_id)
    r = _vente_gerant(client, m, [{"produit_id": produit_id, "quantite": 1, "prix": prix}])
    assert r.status_code == 201, r.get_json()
    with app.app_context():
        vente = db.tables.livraisons.find_one({"_id": ObjectId(r.get_json()["livraison_id"])})
    assert vente["produits"][0]["prix"] == prix


def test_modification_d_une_vente_controle_aussi_les_lignes(client, app, magasins):
    m = magasins["A"]
    produit_id = _produit(client, m)
    r = _vente_manager(client, m, [{"produit_id": produit_id, "quantite": 2}])
    assert r.status_code == 201, r.get_json()
    livraison_id = r.get_json()["livraison_id"]
    stock = _stock_magasin(app, produit_id)
    r = client.put(f"/admin_or_manager/livraisons/{livraison_id}",
                   json={"produits": [{"produit_id": produit_id, "quantite": -10}]}, headers=_h(m, "manager"))
    assert r.status_code == 400
    assert _stock_magasin(app, produit_id) == stock
