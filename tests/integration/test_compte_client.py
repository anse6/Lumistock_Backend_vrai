"""Dette d'ouverture (dette d'avant Lumistock), règlements répartis et abandon du reste."""
from datetime import datetime, timedelta

from bson import ObjectId

from extensions import db
from tests.integration.test_livraisons_magasin import _h, _produit, _stock_magasin, magasins  # noqa: F401
from tests.integration.test_retour_remboursement import _vente, _credit


def _doc(app, lid):
    with app.app_context():
        return db.tables.livraisons.find_one({"_id": ObjectId(lid)})


def _compte(client, m, role="admin"):
    r = client.get(f"/clients/{m['client']}/compte", headers=_h(m, role))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _dater(app, lid, jours):
    with app.app_context():
        quand = datetime.utcnow() - timedelta(days=jours)
        db.tables.livraisons.update_one({"_id": ObjectId(lid)}, {"$set": {"date_livraison": quand, "created_at": quand}})


def test_dette_d_ouverture_comptee_une_seule_fois(client, app, magasins):
    a = magasins["A"]
    r = client.put(f"/clients/{a['client']}/dette-ouverture",
                   json={"montant": 5000, "date_origine": "2026-03-01", "note": "Cahier p. 12"}, headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    pid = _produit(client, a, quantite=20)
    _vente(client, a, pid, 10, 1000, statut="partiellement payé")  # reste 2 000
    compte = _compte(client, a)
    assert compte["dette_ouverture"]["reste"] == 5000
    assert compte["total_ventes_impayees"] == 2000 and compte["total_du"] == 7000
    from services.client_service import dette_client
    with app.app_context():
        assert dette_client(a["client"], a["magasin"]) == 7000
    dettes = client.get("/clients/debt/manager/", headers=_h(a, "admin")).get_json()
    lignes = dettes if isinstance(dettes, list) else dettes.get("clients", dettes.get("data", []))
    assert [c["dette"] for c in lignes if c["client_id"] == a["client"]] == [7000]


def test_une_vente_ne_peut_plus_ajouter_de_dette_ancienne(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    r = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": pid, "quantite": 2, "prix": 300}], "statut_paiement": "non payé",
        "montant_paye": 0, "client_id": a["client"], "dette_ancienne": 4000}, headers=_h(a, "manager"))
    assert r.status_code == 400 and "fiche du client" in r.get_json()["error"]
    assert _stock_magasin(app, pid) == 20


def test_reglement_reparti_dette_d_ouverture_puis_ventes_les_plus_anciennes(client, app, magasins):
    a = magasins["A"]
    client.put(f"/clients/{a['client']}/dette-ouverture", json={"montant": 1000}, headers=_h(a, "admin"))
    pid = _produit(client, a, quantite=20)
    recente = _vente(client, a, pid, 5, 0, statut="non payé")    # 1 500
    ancienne = _vente(client, a, pid, 5, 500, statut="partiellement payé")  # reste 1 000
    _dater(app, ancienne, 10)
    _dater(app, recente, 2)

    r = client.post(f"/clients/{a['client']}/reglements", json={"montant": 2500, "methode": "om"},
                    headers=_h(a, "manager"))
    assert r.status_code == 200, r.get_json()
    corps = r.get_json()
    assert [(x["type"], x["montant"]) for x in corps["affectations"]] == [
        ("dette_ouverture", 1000), ("vente", 1000), ("vente", 500)]
    assert _doc(app, ancienne)["statut_paiement"] == "payé"
    assert _doc(app, recente)["montant_paye"] == 500
    assert corps["compte"]["total_du"] == 1000

    # Le surplus va sur le compte du client
    r = client.post(f"/clients/{a['client']}/reglements", json={"montant": 1600}, headers=_h(a, "manager"))
    assert r.get_json()["avance_creditee"] == 600 and _credit(app, a) == 600
    assert _compte(client, a)["total_du"] == 0


def test_reglements_de_dette_d_ouverture_dans_les_encaissements_et_le_bilan(client, app, magasins):
    a = magasins["A"]
    client.put(f"/clients/{a['client']}/dette-ouverture", json={"montant": 3000}, headers=_h(a, "admin"))
    client.post(f"/clients/{a['client']}/reglements", json={"montant": 1200, "methode": "cash"}, headers=_h(a, "admin"))
    from services.rapport_service import rapport_encaissements
    from services.compte_client_service import creances_ouverture_au
    with app.app_context():
        maintenant = datetime.now()
        ops = rapport_encaissements(a["magasin"], maintenant - timedelta(days=1), maintenant + timedelta(days=1))["operations"]
        assert [o["montant"] for o in ops if o["nature"] == "Règlement de dette d'ouverture"] == [1200]
        assert creances_ouverture_au(a["magasin"], datetime.utcnow() + timedelta(seconds=1)) == 1800


def test_dette_d_ouverture_ne_descend_pas_sous_le_deja_regle(client, app, magasins):
    a = magasins["A"]
    client.put(f"/clients/{a['client']}/dette-ouverture", json={"montant": 3000}, headers=_h(a, "admin"))
    client.post(f"/clients/{a['client']}/reglements", json={"montant": 1000}, headers=_h(a, "admin"))
    r = client.put(f"/clients/{a['client']}/dette-ouverture", json={"montant": 500}, headers=_h(a, "admin"))
    assert r.status_code == 400
    # Un gérant ne peut pas modifier une dette existante
    r = client.put(f"/clients/{a['client']}/dette-ouverture", json={"montant": 9000}, headers=_h(a, "gerant"))
    assert r.status_code == 403


def test_abandon_direct_du_reste_par_un_manager(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _vente(client, a, pid, 10, 2000, statut="partiellement payé")
    r = client.post(f"/livraisons/{lid}/abandonner-reste", json={"motif": "Client en difficulté"},
                    headers=_h(a, "manager"))
    assert r.status_code == 200, r.get_json()
    d = _doc(app, lid)
    assert d["montant_total"] == 2000 and d["statut_paiement"] == "payé"
    assert d["abandon_reste"]["statut"] == "valide" and d["abandon_reste"]["montant"] == 1000
    assert _compte(client, a)["total_du"] == 0


def test_demande_d_abandon_du_gerant_validee_ou_refusee(client, app, magasins):
    from tests.integration.test_livraisons_magasin import _livraison_gerant
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _livraison_gerant(client, a, pid)
    with app.app_context():  # vente de gérant : 900, payée 400
        db.tables.livraisons.update_one({"_id": ObjectId(lid)}, {"$set": {
            "montant_paye": 400.0, "statut_paiement": "partiellement payé"}})

    assert client.post(f"/livraisons/{lid}/demander-abandon", json={}, headers=_h(a, "gerant")).status_code == 400
    r = client.post(f"/livraisons/{lid}/demander-abandon", json={"motif": "Client parti"}, headers=_h(a, "gerant"))
    assert r.status_code == 200, r.get_json()
    vente = client.get(f"/livraisons/{lid}", headers=_h(a, "admin")).get_json()
    vente = vente.get("livraison", vente)
    assert vente["abandon_en_attente"] is True

    r = client.post(f"/livraisons/rejeter-ristourne/{lid}", headers=_h(a, "admin"))
    assert r.status_code == 200
    assert _doc(app, lid)["montant_total"] == 900 and _doc(app, lid)["abandon_reste"]["statut"] == "refuse"

    client.post(f"/livraisons/{lid}/demander-abandon", json={"motif": "Client parti"}, headers=_h(a, "gerant"))
    r = client.post(f"/livraisons/valider-ristourne/{lid}", headers=_h(a, "admin"))
    assert r.status_code == 200, r.get_json()
    d = _doc(app, lid)
    assert d["montant_total"] == 400 and d["statut_paiement"] == "payé"



def test_gerant_autorise_abandonne_directement_et_modifie_la_dette(client, app, magasins):
    """Paramètres > Accès : l'admin donne au gérant « Abandonner le reste » et
    « Modifier une dette d'avant Lumistock »."""
    from tests.integration.test_livraisons_magasin import _livraison_gerant
    a = magasins["A"]
    r = client.put("/magasin/permissions", headers=_h(a, "admin"), json={"droits": {
        "ventes.abandon_reste": {"gerant": True}, "clients.dette_ouverture": {"gerant": True}}})
    assert r.status_code == 200, r.get_json()

    pid = _produit(client, a, quantite=20)
    lid = _livraison_gerant(client, a, pid)
    with app.app_context():  # vente de gérant : 900, payée 400
        db.tables.livraisons.update_one({"_id": ObjectId(lid)}, {"$set": {
            "montant_paye": 400.0, "statut_paiement": "partiellement payé"}})
    r = client.post(f"/livraisons/{lid}/demander-abandon", json={"motif": "Client parti"}, headers=_h(a, "gerant"))
    assert r.status_code == 200, r.get_json()
    d = _doc(app, lid)
    assert d["abandon_reste"]["statut"] == "valide" and d["montant_total"] == 400

    client.put(f"/clients/{a['client']}/dette-ouverture", json={"montant": 3000}, headers=_h(a, "admin"))
    r = client.put(f"/clients/{a['client']}/dette-ouverture", json={"montant": 2500}, headers=_h(a, "gerant"))
    assert r.status_code == 200, r.get_json()

    # Sans le droit, le manager ne peut plus abandonner un reste.
    client.put("/magasin/permissions", headers=_h(a, "admin"),
               json={"droits": {"ventes.abandon_reste": {"manager": False}}})
    lid2 = _vente(client, a, pid, 10, 2000, statut="partiellement payé")
    r = client.post(f"/livraisons/{lid2}/abandonner-reste", json={}, headers=_h(a, "manager"))
    assert r.status_code == 403

def test_etat_de_la_vente_independant_du_paiement(client, app, magasins):
    a = magasins["A"]
    pid = _produit(client, a, quantite=20)
    lid = _vente(client, a, pid, 10, 0, statut="non payé")

    def etat():
        vente = client.get(f"/livraisons/{lid}", headers=_h(a, "admin")).get_json()
        return vente.get("livraison", vente)["etat"]

    assert etat() == "validee"
    client.post("/livraisons/retour", json={"livraison_id": lid, "motif": "x",
                                            "produits": [{"produit_id": pid, "quantite": 4}]}, headers=_h(a, "admin"))
    assert etat() == "retour_partiel"
    client.post("/livraisons/retour", json={"livraison_id": lid, "motif": "x",
                                            "produits": [{"produit_id": pid, "quantite": 6}]}, headers=_h(a, "admin"))
    assert etat() == "retournee"
