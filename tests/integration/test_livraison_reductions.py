"""Réductions commerciales (rabais, remise, ristourne) sur une vente."""
import pytest

from services.reductions_service import appliquer_reductions, valider_reductions
from tests.integration.test_livraisons_magasin import _h, _produit, _stock_magasin, magasins  # noqa: F401


def test_cascade_rabais_remise_ristourne():
    detail, total = appliquer_reductions([
        {"type": "ristourne", "mode": "montant", "valeur": 100},
        {"type": "rabais", "mode": "pourcentage", "valeur": 10, "motif": "Emballage abîmé"},
        {"type": "remise", "mode": "pourcentage", "valeur": 5},
    ], 10000)
    assert [d["type"] for d in detail] == ["rabais", "remise", "ristourne"]
    assert [d["montant"] for d in detail] == [1000, 450, 100]
    assert [d["base"] for d in detail] == [10000, 9000, 8550]
    assert total == 1550


def test_reduction_plafonnee_au_montant_restant():
    detail, total = appliquer_reductions([{"type": "remise", "mode": "montant", "valeur": 5000}], 1200)
    assert detail[0]["montant"] == 1200 and total == 1200


@pytest.mark.parametrize("reductions", [
    [{"type": "escompte", "mode": "montant", "valeur": 1}],
    [{"type": "rabais", "mode": "pourcentage", "valeur": 150}],
    [{"type": "rabais", "mode": "montant", "valeur": -1}],
    [{"type": "remise", "valeur": 1}, {"type": "remise", "valeur": 2}],
    "rabais",
])
def test_reductions_invalides(reductions):
    with pytest.raises(ValueError):
        valider_reductions(reductions)


def _vente(client, m, produit_id, reductions, montant_paye):
    return client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": produit_id, "quantite": 10, "prix": 300}], "statut_paiement": "payé",
        "montant_paye": montant_paye, "client_id": m["client"], "reductions": reductions,
    }, headers=_h(m, "manager"))


def test_vente_avec_reductions_et_historique(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    r = _vente(client, a, produit_id, [
        {"type": "rabais", "mode": "pourcentage", "valeur": 10, "motif": "Sacs abîmés"},
        {"type": "remise", "mode": "montant", "valeur": 200},
    ], 2500)
    assert r.status_code == 201, r.get_json()
    livraison_id = r.get_json()["livraison_id"]
    assert _stock_magasin(app, produit_id) == 10

    data = client.get(f"/livraisons/{livraison_id}", headers=_h(a, "admin")).get_json()
    data = data.get("livraison", data)
    assert data["montant_brut"] == 3000
    assert data["total_reductions"] == 500
    assert data["montant_produits"] == 2500
    assert data["montant_total"] == 2500
    assert data["statut_paiement"] in ("payé", "paye", "payée")
    assert data["reductions"][0]["motif"] == "Sacs abîmés"

    # Modification : on retire le rabais, la remise reste.
    r = client.put(f"/admin_or_manager/livraisons/{livraison_id}", json={
        "reductions": [{"type": "remise", "mode": "montant", "valeur": 200}]}, headers=_h(a, "manager"))
    assert r.status_code == 200, r.get_json()
    data = client.get(f"/livraisons/{livraison_id}", headers=_h(a, "admin")).get_json()
    data = data.get("livraison", data)
    assert data["montant_brut"] == 3000
    assert data["total_reductions"] == 200
    assert data["montant_total"] == 2800


def test_vente_avec_reduction_invalide_refusee_sans_toucher_au_stock(client, app, magasins):
    a = magasins["A"]
    produit_id = _produit(client, a, quantite=20)
    r = _vente(client, a, produit_id, [{"type": "rabais", "mode": "pourcentage", "valeur": 120}], 0)
    assert r.status_code == 400
    assert _stock_magasin(app, produit_id) == 20
