"""Frais de retour retenus : recette à part (7078), avec leur TVA, comptés dans
le chiffre d'affaires SYSCOHADA (701 à 707) mais pas dans les ventes ni la marge."""
import json
from datetime import datetime

from bson import ObjectId

from extensions import db
from tests.integration.test_comptabilite_complete import _cameroun, _tables_vides  # noqa: F401
from tests.integration.test_livraisons_magasin import _h, magasins  # noqa: F401

PERIODE = f"debut={datetime.now():%Y-%m}-01&fin={datetime.now():%Y-%m-%d}"


def _vente_puis_retour(client, app, m, tva):
    """2 palettes de 6 à 1 700 (coût 250 la bouteille), payées ; 1 palette rendue, 10 % retenus."""
    if tva:
        _cameroun(app, m)
    r = client.post("/produits", data={"nom": "Eau", "prix": "300", "prix_achat": "250", "quantite": "120",
                                       "unite": "bouteille", "conditionnements": json.dumps(
                                           [{"nom": "Palette", "facteur": 6, "prix_conditionnement": 1700}])},
                    headers=_h(m, "admin"), content_type="multipart/form-data")
    pid = r.get_json()["produit_id"]
    with app.app_context():
        pal = str(db.tables.produits.find_one({"_id": ObjectId(pid)})["conditionnements"][0]["_id"])
    total = 4054.5 if tva else 3400
    v = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": pid, "conditionnement_id": pal, "quantite_conditionnement": 2}],
        "statut_paiement": "payé", "montant_paye": total, "paiements": [{"methode": "cash", "montant": total}],
        "client_id": m["client"], "appliquer_tva": tva}, headers=_h(m, "manager"))
    assert v.status_code == 201, v.get_json()
    rr = client.post("/livraisons/retour", json={
        "livraison_id": v.get_json()["livraison_id"], "motif": "test", "retenue_pourcentage": 10,
        "produits": [{"produit_id": pid, "is_conditionne": True, "conditionnement_id": pal, "quantite_conditionnement": 1}]},
        headers=_h(m, "manager"))
    assert rr.status_code == 201, rr.get_json()
    return rr.get_json()


def _synthese(client, m):
    return client.get(f"/compta/synthese?{PERIODE}", headers=_h(m, "admin")).get_json()


def _ecriture_frais(client, m):
    j = client.get(f"/compta/journal?{PERIODE}", headers=_h(m, "admin")).get_json()
    ecritures = j if isinstance(j, list) else j["ecritures"]
    e = next(e for e in ecritures if e["libelle"].startswith("Frais de retour"))
    return {l["compte"]: (l["debit"], l["credit"]) for l in e["lignes"]}


def test_sans_tva_frais_en_recette_et_dans_le_chiffre_affaires(client, app, magasins):
    m = magasins["A"]
    r = _vente_puis_retour(client, app, m, tva=False)
    assert (r["frais_retour"], r["frais_retour_ht"], r["frais_retour_tva"]) == (170, 170, 0)
    i = _synthese(client, m)["indicateurs"]
    assert i["ventes_marchandises"] == 1700 and i["frais_retour"] == 170
    assert i["chiffre_affaires"] == 1870          # 701 à 707
    assert i["marge"] == 200                      # marge sur les ventes seules
    assert _ecriture_frais(client, m) == {"411": (170, 0), "7078": (0, 170)}


def test_avec_tva_les_frais_contiennent_leur_tva(client, app, magasins):
    m = magasins["A"]
    r = _vente_puis_retour(client, app, m, tva=True)
    # 1 palette TTC = 1 700 × 1,1925 = 2 027,25 ; 10 % = 203 TTC = 170 HT + 33 de TVA
    assert r["frais_retour"] == 203 and r["frais_retour_ht"] == 170 and r["frais_retour_tva"] == 33
    s = _synthese(client, m)
    assert s["indicateurs"]["frais_retour"] == 170 and s["indicateurs"]["chiffre_affaires"] == 1870
    assert s["tva"]["collectee"] == 327 + 33      # TVA de la palette gardée + TVA des frais
    assert _ecriture_frais(client, m) == {"411": (203, 0), "7078": (0, 170), "4431": (0, 33)}
    lignes = {l["cle"]: l["montant"] for l in s["compte_resultat"]}
    assert lignes["ca"] == 1700 and lignes["frais_retour"] == 170 and lignes["ca_total"] == 1870


def test_retour_avec_unites_abimees(client, app, magasins):
    """3 bouteilles rendues dont 1 abîmée : 2 reviennent en stock vendable,
    la 3e part en avarie (perte au coût d'achat), le client est remboursé des 3."""
    m = magasins["A"]
    r = client.post("/produits", data={"nom": "Eau abîmée", "prix": "300", "prix_achat": "250", "quantite": "10",
                                       "unite": "bouteille"}, headers=_h(m, "admin"), content_type="multipart/form-data")
    pid = r.get_json()["produit_id"]
    v = client.post("/admin_or_manager/livraisons", json={
        "produits": [{"produit_id": pid, "quantite": 5, "prix": 300}], "statut_paiement": "payé", "montant_paye": 1500,
        "paiements": [{"methode": "cash", "montant": 1500}], "client_id": m["client"]}, headers=_h(m, "manager"))
    vid = v.get_json()["livraison_id"]
    # Plus d'abîmées que de rendues : refusé
    trop = client.post("/livraisons/retour", json={"livraison_id": vid, "motif": "x",
        "produits": [{"produit_id": pid, "quantite": 1, "abimes": 2}]}, headers=_h(m, "manager"))
    assert trop.status_code == 400
    rr = client.post("/livraisons/retour", json={"livraison_id": vid, "motif": "bouteille percée", "retenue_pourcentage": 0,
        "produits": [{"produit_id": pid, "quantite": 3, "abimes": 1}]}, headers=_h(m, "manager"))
    assert rr.status_code == 201, rr.get_json()
    assert rr.get_json()["abimes"] == 1 and rr.get_json()["montant_a_rembourser"] == 900
    with app.app_context():
        p = db.tables.produits.find_one({"_id": ObjectId(pid)})
    assert p["quantite"] == 10 - 5 + 2                       # seules les 2 bonnes reviennent en vente
    avaries = [mv for mv in p.get("mouvements_stock") or [] if mv.get("type") == "produit périmé"]
    assert len(avaries) == 1 and avaries[0].get("type_avarie") == "deteriore"
    assert "bouteille percée" in (avaries[0].get("description") or "")
    s = _synthese(client, m)
    pertes = {l["cle"]: l["montant"] for l in s["compte_resultat"]}["pertes"]
    assert pertes == 250                                       # la bouteille abîmée, au coût d'achat
