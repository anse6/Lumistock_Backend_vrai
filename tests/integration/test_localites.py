# tests/integration/test_localites.py
"""Base locale des villes et quartiers (data/localites/<PAYS>.json)."""
import json
from unittest.mock import patch

import pytest

from routes import location_routes

VILLES = [
    {"nom": "Doubaï", "autres_noms": ["Dubai"], "lat": 25.2, "lon": 55.3, "region": "Doubaï",
     "type": "city", "population": 3000000, "quartiers": ["Deira", "Jumeirah"]},
    {"nom": "Capitale", "lat": 24.4, "lon": 54.4, "region": "Centre",
     "type": "city", "population": 1000, "capitale": True, "quartiers": []},
    {"nom": "Hatta", "lat": 24.8, "lon": 56.1, "region": "Doubaï",
     "type": "village", "population": 0, "quartiers": ["Hatta Fort"]},
]


@pytest.fixture
def base_locale(client, tmp_path):
    dossier = tmp_path / "data" / "localites"
    dossier.mkdir(parents=True)
    (dossier / "ZZ.json").write_text(json.dumps({"pays": "ZZ", "villes": VILLES}), encoding="utf-8")
    location_routes._localites_cache.clear()
    with patch.object(client.application, "root_path", str(tmp_path)):
        yield
    location_routes._localites_cache.clear()


def test_villes_capitale_puis_population(client, base_locale):
    villes = client.get("/locations/villes?country_code=ZZ").get_json()
    assert [v["nom"] for v in villes] == ["Capitale", "Doubaï", "Hatta"]
    assert villes[1]["autres_noms"] == ["Dubai"]
    assert villes[1]["quartiers"] == 2
    assert villes[2]["rang"] == 2


def test_quartiers_par_autre_nom_sans_internet(client, base_locale):
    with patch("routes.location_routes._osm_neighborhoods") as osm:
        for nom in ("Dubai", "doubai", "Doubaï"):
            reponse = client.get(f"/locations/neighborhoods?country_code=ZZ&city={nom}")
            assert reponse.get_json() == ["Deira", "Jumeirah"]
    osm.assert_not_called()


def test_cities_inclut_la_base_locale(client, base_locale):
    assert client.get("/locations/cities?country_code=ZZ").get_json() == ["Capitale", "Doubaï", "Hatta"]


def test_base_manuelle_fusionnee_sans_doublon(client):
    location_routes._localites_cache.clear()
    quartiers = client.get("/locations/neighborhoods?country_code=CM&city=Yaounde").get_json()
    cles = [location_routes._normalize(q).replace("-", " ") for q in quartiers]
    assert "Bastos" in quartiers
    assert len(cles) == len(set(cles))


def test_villes_compressees_pour_le_navigateur(client, base_locale):
    import gzip
    reponse = client.get("/locations/villes?country_code=ZZ", headers={"Accept-Encoding": "gzip"})
    assert reponse.headers["Content-Encoding"] == "gzip"
    villes = json.loads(gzip.decompress(reponse.data))
    assert "population" not in villes[2] and villes[2]["rang"] == 2  # champs vides omis
