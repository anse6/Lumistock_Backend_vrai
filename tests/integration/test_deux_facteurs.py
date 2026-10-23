"""Double authentification facultative (TOTP)."""
import time

from extensions import db
from services import deux_facteurs_service as df
from tests.integration.test_auth import MOT_DE_PASSE, _bearer, _login, setup_auth  # noqa: F401


def _code(secret, decalage=0):
    return df.code_totp(secret, df.pas_courant(time.time()) + decalage)


def _activer(client):
    token = _login(client).get_json()["access_token"]
    h = _bearer(token)
    r = client.post("/auth/2fa/initialiser", headers=h)
    assert r.status_code == 200
    secret = r.get_json()["secret"]
    assert r.get_json()["uri"].startswith("otpauth://totp/Lumistock:admin%40test.com?secret=")
    r = client.post("/auth/2fa/activer", json={"code": _code(secret)}, headers=h)
    assert r.status_code == 200, r.get_json()
    return h, secret, r.get_json()["codes_secours"]


def test_rfc6238_vecteur_de_reference():
    # RFC 6238, annexe B : secret ASCII "12345678901234567890", T = 59 s.
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert df.code_totp(secret, 59 // 30) == "287082"
    assert df.code_totp(secret, 1111111109 // 30) == "081804"


def test_sans_2fa_la_connexion_ne_change_pas(client, setup_auth):
    r = _login(client)
    assert r.status_code == 200 and "access_token" in r.get_json()
    h = _bearer(r.get_json()["access_token"])
    assert client.get("/auth/2fa", headers=h).get_json()["actif"] is False


def test_activation_puis_connexion_en_deux_etapes(client, app, setup_auth):
    h, secret, codes = _activer(client)
    assert len(codes) == 10
    assert client.get("/auth/2fa", headers=h).get_json() | {"active_le": None} == {
        "actif": True, "active_le": None, "codes_secours_restants": 10}

    # Le secret n'est jamais exposé avec le profil ou la liste de l'équipe.
    assert "secret" not in str(client.get("/users/me", headers=h).get_json())
    membres = client.get("/users/", headers=h).get_json()["users"]
    assert any(m["deux_facteurs"] for m in membres)
    assert "secret" not in str(membres)

    # Mot de passe seul : pas de jeton d'accès, un défi à relever.
    r = _login(client)
    assert r.status_code == 200
    data = r.get_json()
    assert data["deux_facteurs_requis"] is True and "access_token" not in data
    # Le jeton intermédiaire n'ouvre aucune route.
    assert client.get("/users/me", headers=_bearer(data["jeton_2fa"])).status_code in (401, 422)

    assert client.post("/auth/login/2fa", json={"jeton_2fa": data["jeton_2fa"], "code": "000000"}).status_code == 401
    code = _code(secret, 1)
    r = client.post("/auth/login/2fa", json={"jeton_2fa": data["jeton_2fa"], "code": code})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["access_token"] and r.get_json()["role"] == "admin"

    # Le même code ne resservira pas (pas de rejeu).
    jeton = _login(client).get_json()["jeton_2fa"]
    assert client.post("/auth/login/2fa", json={"jeton_2fa": jeton, "code": code}).status_code == 401


def test_code_de_secours_a_usage_unique(client, setup_auth):
    h, _, codes = _activer(client)
    jeton = _login(client).get_json()["jeton_2fa"]
    r = client.post("/auth/login/2fa", json={"jeton_2fa": jeton, "code": codes[0].lower()})
    assert r.status_code == 200
    jeton = _login(client).get_json()["jeton_2fa"]
    assert client.post("/auth/login/2fa", json={"jeton_2fa": jeton, "code": codes[0]}).status_code == 401
    assert client.get("/auth/2fa", headers=h).get_json()["codes_secours_restants"] == 9


def test_jeton_falsifie_refuse(client, setup_auth):
    _activer(client)
    r = client.post("/auth/login/2fa", json={"jeton_2fa": "abc.def.ghi", "code": "123456"})
    assert r.status_code == 401


def test_trop_d_essais_bloque(client, setup_auth):
    _activer(client)
    jeton = _login(client).get_json()["jeton_2fa"]
    statuts = [client.post("/auth/login/2fa", json={"jeton_2fa": jeton, "code": "000000"}).status_code
               for _ in range(6)]
    assert statuts[-1] == 429


def test_desactivation_demande_mot_de_passe_et_code(client, setup_auth):
    h, secret, codes = _activer(client)
    assert client.post("/auth/2fa/desactiver", json={"password": "faux", "code": codes[1]}, headers=h).status_code == 401
    assert client.post("/auth/2fa/desactiver", json={"password": MOT_DE_PASSE, "code": "000000"}, headers=h).status_code == 401
    r = client.post("/auth/2fa/desactiver", json={"password": MOT_DE_PASSE, "code": codes[1]}, headers=h)
    assert r.status_code == 200 and r.get_json()["actif"] is False
    assert "access_token" in _login(client).get_json()


def test_admin_reinitialise_la_2fa_d_un_membre(client, app, setup_auth):
    # Le staff active la sienne, puis la perd : un autre compte autorisé la retire.
    _activer(client)
    staff = client.post("/auth/login", json={"email": "staff@test.com", "password": MOT_DE_PASSE}).get_json()
    r = client.delete(f"/users/{setup_auth['admin_id']}/deux-facteurs", headers=_bearer(staff["access_token"]))
    assert r.status_code == 200, r.get_json()
    assert "access_token" in _login(client).get_json()
    with app.app_context():
        assert db.tables.deux_facteurs.find_one({}) is None
