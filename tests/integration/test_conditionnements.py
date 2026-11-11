"""Conditionnements (carton, pack…) : règles de la fiche et des ventes."""
import json

from bson import ObjectId

from extensions import db
from tests.integration.test_numeros_serie import m, _h  # noqa: F401


def _creer(client, m, conds, prix="100", achat="80", nom="Savon", quantite="100"):
    return client.post("/produits", data={"nom": nom, "prix": prix, "prix_achat": achat, "unite": "pièce",
                                          "quantite": quantite, "conditionnements": json.dumps(conds)},
                       headers=_h(m), content_type="multipart/form-data")


def _article(client, pid):
    with client.application.app_context():
        return db.tables.produits.find_one({"_id": ObjectId(pid)})


def _modifier(client, m, pid, conds, **extra):
    return client.put(f"/produits/{pid}", data={"conditionnements": json.dumps(conds), **extra}, headers=_h(m),
                      content_type="multipart/form-data")


def _vendre(client, m, pid, cid, n=1):
    return client.post("/admin_or_manager/livraisons", json={"produits": [
        {"produit_id": pid, "conditionnement_id": cid, "quantite_conditionnement": n}],
        "statut_paiement": "non payé", "montant_paye": 0}, headers=_h(m))


def _vente(client, vid):
    with client.application.app_context():
        return db.tables.livraisons.find_one({"_id": ObjectId(vid)})


def test_regles_de_la_fiche(client, m):
    cas = [
        ([{"nom": "Unité", "facteur": 1, "prix_conditionnement": 100}], "au moins 2"),
        ([{"nom": "Carton", "facteur": 12}, {"nom": "carton ", "facteur": 24}], "Deux conditionnements s'appellent"),
        ([{"nom": "Carton", "facteur": 12}, {"nom": "Boîte", "facteur": 12}], "12 unités"),
        ([{"nom": "", "facteur": 12}], "nom"),
        ([{"nom": "Pack", "facteur": 3, "prix_conditionnement": 200}], "sous le coût"),  # 3 × 80 = 240
    ]
    for i, (conds, attendu) in enumerate(cas):
        r = _creer(client, m, conds, nom=f"A{i}")
        assert r.status_code == 400 and attendu in r.get_json()["error"], (conds, r.get_json())
    r = _creer(client, m, [{"nom": "Carton", "facteur": 12}, {"nom": "Pack", "facteur": 3, "prix_conditionnement": 270}])
    assert r.status_code == 201, r.get_json()
    conds = {c["nom"]: c for c in _article(client, r.get_json()["produit_id"])["conditionnements"]}
    assert conds["Carton"]["prix_conditionnement"] == 1200  # absent : 12 × prix unitaire
    assert conds["Pack"]["economie"] == -30


def test_prix_zero_refuse_en_modification(client, m):
    pid = _creer(client, m, [{"nom": "Pack", "facteur": 3, "prix_conditionnement": 270}]).get_json()["produit_id"]
    pack = _article(client, pid)["conditionnements"][0]
    r = _modifier(client, m, pid, [{"_id": str(pack["_id"]), "nom": "Pack", "facteur": 3, "prix_conditionnement": 0}])
    assert r.status_code == 400
    assert _article(client, pid)["conditionnements"][0]["prix_conditionnement"] == 270


def test_une_vente_passee_garde_son_prix_et_son_nom(client, m):
    pid = _creer(client, m, [{"nom": "Pack", "facteur": 3, "prix_conditionnement": 270}]).get_json()["produit_id"]
    pack = _article(client, pid)["conditionnements"][0]
    vid = _vendre(client, m, pid, str(pack["_id"]), 2).get_json()["livraison_id"]
    avant = _vente(client, vid)
    assert avant["montant_total"] == 540 and avant["produits"][0]["conditionnement_nom"] == "Pack"
    # Le prix du pack change, puis le pack est renommé et enfin supprimé de la fiche.
    assert _modifier(client, m, pid, [{"_id": str(pack["_id"]), "nom": "Pack 3", "facteur": 3,
                                       "prix_conditionnement": 300}]).status_code == 200
    assert _modifier(client, m, pid, []).status_code == 200
    # Modifier la vente (par exemple pour un paiement) ne la re-tarife pas.
    r = client.put(f"/admin_or_manager/livraisons/{vid}", json={"produits": [
        {"produit_id": pid, "conditionnement_id": str(pack["_id"]), "quantite_conditionnement": 2}]}, headers=_h(m))
    assert r.status_code == 200, r.get_json()
    apres = _vente(client, vid)
    assert apres["montant_total"] == 540 and apres["produits"][0]["conditionnement_nom"] == "Pack"
    lignes = client.get(f"/livraisons/{vid}", headers=_h(m)).get_json()
    lignes = (lignes.get("livraison") or lignes)["produits"]
    assert lignes[0]["conditionnement_nom"] == "Pack"


def test_identifiant_et_code_barre_conserves(client, m):
    pid = _creer(client, m, [{"nom": "Carton", "facteur": 12}]).get_json()["produit_id"]
    carton = _article(client, pid)["conditionnements"][0]
    r = client.put(f"/produits/{pid}/code-barre", json={"code_barre": "2001234567893", "conditionnement_id": str(carton["_id"])},
                   headers=_h(m))
    assert r.status_code == 200, r.get_json()
    assert _modifier(client, m, pid, [{"_id": str(carton["_id"]), "nom": "Carton", "facteur": 12,
                                       "prix_conditionnement": 1150}]).status_code == 200
    apres = _article(client, pid)["conditionnements"][0]
    assert apres["_id"] == carton["_id"] and apres.get("code_barre") == "2001234567893"
    # Changer le prix unitaire met à jour le prix de référence et l'économie.
    assert client.put(f"/produits/{pid}", data={"prix": "110"}, headers=_h(m),
                      content_type="multipart/form-data").status_code == 200
    apres = _article(client, pid)["conditionnements"][0]
    assert apres["prix_reference"] == 1320 and apres["economie"] == -170


def test_palette_faite_de_packs(client, m):
    """Palette = 84 packs de 6 : 504 unités, prix par défaut = 84 packs, suit le pack."""
    conds = [{"nom": "Palette", "contenu_nom": "pack", "contenu_nombre": 84},
             {"nom": "Pack", "facteur": 6, "prix_conditionnement": 1950}]
    r = _creer(client, m, conds, prix="350", achat="300", nom="Eau 1,5", quantite="1008")
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["produit_id"]
    c = {x["nom"]: x for x in _article(client, pid)["conditionnements"]}
    assert c["Palette"]["facteur"] == 504
    assert c["Palette"]["prix_conditionnement"] == 84 * 1950
    assert c["Palette"]["contenu_nom"] == "Pack" and c["Palette"]["contenu_nombre"] == 84
    # Vendre 1 palette sort 504 bouteilles.
    v = _vendre(client, m, pid, str(c["Palette"]["_id"]), 1)
    assert v.status_code == 201, v.get_json()
    ligne = _vente(client, v.get_json()["livraison_id"])["produits"][0]
    assert ligne["quantite"] == 504 and ligne["conditionnement_nom"] == "Palette"
    # Le pack passe à 12 : la palette suit (84 × 12), son prix fixé reste.
    garde = [{"_id": str(c["Pack"]["_id"]), "nom": "Pack", "facteur": 12, "prix_conditionnement": 3900},
             {"_id": str(c["Palette"]["_id"]), "nom": "Palette", "contenu_nom": "Pack", "contenu_nombre": 84,
              "prix_conditionnement": c["Palette"]["prix_conditionnement"] * 2}]
    assert _modifier(client, m, pid, garde).status_code == 200
    c2 = {x["nom"]: x for x in _article(client, pid)["conditionnements"]}
    assert c2["Palette"]["facteur"] == 1008 and c2["Palette"]["_id"] == c["Palette"]["_id"]


def test_palette_regles(client, m):
    cas = [
        ([{"nom": "Palette", "contenu_nom": "Carton", "contenu_nombre": 10}], "n'existe pas"),
        ([{"nom": "Pack", "facteur": 6}, {"nom": "Palette", "contenu_nom": "Pack", "contenu_nombre": 1}], "au moins 2"),
        ([{"nom": "A", "contenu_nom": "B", "contenu_nombre": 2}, {"nom": "B", "contenu_nom": "A", "contenu_nombre": 2}],
         "se contient lui-même"),
        ([{"nom": "Pack", "facteur": 6}, {"nom": "Carton", "facteur": 12},
          {"nom": "Double", "contenu_nom": "Pack", "contenu_nombre": 2}], "tous les deux"),
        ([{"nom": "Pack", "facteur": 6, "prix_conditionnement": 600},
          {"nom": "Palette", "contenu_nom": "Pack", "contenu_nombre": 10, "prix_conditionnement": 4000}], "sous le coût"),
    ]
    for i, (conds, attendu) in enumerate(cas):
        r = _creer(client, m, conds, nom=f"P{i}")
        assert r.status_code == 400 and attendu in r.get_json()["error"], (conds, r.get_json())
