"""Assistant IA : analyse du magasin, conversation, erreurs (le fournisseur d'IA est simulé)."""

from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from extensions import db
from services import ai_service as module_ai
from services import ia_contexte
from utils.jwt_token import generate_token


@pytest.fixture(autouse=True)
def caches_vides():
    ia_contexte.vider_cache()
    module_ai.vider_cache_analyses()
    yield
    ia_contexte.vider_cache()
    module_ai.vider_cache_analyses()


@pytest.fixture
def magasin(app, clear_db):
    now = datetime.utcnow()
    mag, admin, gerant, client = ObjectId(), ObjectId(), ObjectId(), ObjectId()
    t = db.tables
    t.magasin.insert_one({"_id": mag, "denomination": "Boutique Test", "devise": "XAF", "is_active": True, "created_at": now})
    t.users.insert_many([
        {"_id": admin, "role": "admin", "first_name": "Ada", "name": "Admin", "email": "a@t.com", "magasin_id": mag, "is_active": True},
        {"_id": gerant, "role": "gerant", "first_name": "Gil", "name": "Gerant", "email": "g@t.com", "magasin_id": mag, "is_active": True},
    ])
    t.clients.insert_one({"_id": client, "nom": "Client Fidèle", "magasin_id": mag, "created_at": now})
    # Catalogue de 300 produits (l'ancien code les envoyait tous à l'IA) + un produit vendu sous son prix d'achat.
    t.produits.insert_many([{"nom": f"Article {i}", "quantite": 3, "prix": 1000, "prix_achat": 600, "magasin_id": mag,
                             "created_at": now} for i in range(300)])
    t.produits.insert_one({"nom": "Produit bradé", "quantite": 4, "prix": 500, "prix_achat": 800, "magasin_id": mag, "created_at": now})
    t.livraisons.insert_one({
        "magasin_id": mag, "client_id": client, "livreur_id": gerant, "statut_livraison": "livrée",
        "date_livraison": now - timedelta(days=1), "created_at": now - timedelta(days=1),
        "montant_produits": 3000.0, "montant_total": 3000.0, "montant_paye": 1000.0, "statut_paiement": "partiellement payé",
        "produits": [{"produit_id": ObjectId(), "nom": "Article 1", "quantite": 3, "prix": 1000, "prix_achat": 600}],
    })
    return {
        "admin": {"Authorization": f"Bearer {generate_token(str(admin), 'admin')}"},
        "gerant": {"Authorization": f"Bearer {generate_token(str(gerant), 'gerant')}"},
    }


@pytest.fixture
def ia_simulee(monkeypatch):
    """Remplace l'appel au fournisseur : enregistre les messages reçus, renvoie un texte fixe."""
    appels = []

    def fausse_ia(messages, temperature=0.4, max_tokens=1200):
        appels.append(messages)
        return "## Synthèse\nTout va bien."

    monkeypatch.setattr(module_ai, "appeler_ia", fausse_ia)
    return appels


def test_sans_cle_reponse_claire(client, magasin, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    for r in (client.get("/ai/insights", headers=magasin["admin"]),
              client.post("/ai/chat", headers=magasin["admin"], json={"message": "Bonjour"})):
        assert r.status_code == 503
        assert r.get_json()["code"] == "ia_non_configuree"
        assert "GROQ_API_KEY" in r.get_json()["error"]
    # L'échec ne crée pas de conversation vide.
    assert db.tables.chat_conversations.count_documents({}) == 0


def test_fournisseur_en_erreur(client, magasin, monkeypatch):
    def panne(*args, **kwargs):
        raise ia_contexte.IAErreur()

    monkeypatch.setattr(module_ai, "appeler_ia", panne)
    r = client.get("/ai/insights", headers=magasin["admin"])
    assert r.status_code == 502 and r.get_json()["code"] == "ia_indisponible"
    assert "Traceback" not in r.get_json()["error"]


def test_analyse_donnees_justes_et_compactes(client, magasin, ia_simulee):
    r = client.get("/ai/insights", headers=magasin["admin"])
    assert r.status_code == 200 and r.get_json()["insights"].startswith("## Synthèse")
    consignes, donnees = ia_simulee[0][0]["content"], ia_simulee[0][1]["content"]
    assert "ALARMISTE" not in consignes and "N'invente aucun chiffre" in consignes
    assert "Chiffre d'affaires HT : 3 000 FCFA" in donnees
    # Seul le produit vraiment vendu sous son prix d'achat est signalé (avant : tout le catalogue).
    assert "Produit bradé : vendu 500 FCFA, acheté 800 FCFA" in donnees
    assert "Article 2 : vendu" not in donnees
    # Données limitées : pas de liste de 300 produits.
    assert len(donnees) < 6000


def test_analyse_mise_en_cache(client, magasin, ia_simulee):
    premiere = client.get("/ai/insights", headers=magasin["admin"]).get_json()
    seconde = client.get("/ai/insights", headers=magasin["admin"]).get_json()
    assert premiere["depuis_cache"] is False and seconde["depuis_cache"] is True
    assert len(ia_simulee) == 1
    client.get("/ai/insights?forcer=1", headers=magasin["admin"])
    assert len(ia_simulee) == 2


def test_analyse_gerant_sur_ses_donnees(client, magasin, ia_simulee):
    assert client.get("/ai/insights", headers=magasin["gerant"]).status_code == 200
    donnees = ia_simulee[0][1]["content"]
    assert "Mes ventes : 1 vente(s)" in donnees
    assert "Client Fidèle : 2 000 FCFA" in donnees
    assert "Chiffre d'affaires HT" not in donnees  # pas les chiffres de tout le magasin


def test_conversation(client, magasin, ia_simulee):
    r = client.post("/ai/chat", headers=magasin["admin"], json={"message": "Quels produits vont manquer ?"})
    assert r.status_code == 200
    conv = r.get_json()["conversation_id"]
    assert db.tables.chat_messages.count_documents({"conversation_id": ObjectId(conv)}) == 2
    # Le message suivant reprend l'historique.
    client.post("/ai/chat", headers=magasin["admin"], json={"message": "Et les clients ?", "conversation_id": conv})
    assert [m["role"] for m in ia_simulee[1]] == ["system", "user", "assistant", "user"]
    # Conversation d'un autre utilisateur : 404, pas 500.
    r = client.post("/ai/chat", headers=magasin["gerant"], json={"message": "Salut", "conversation_id": conv})
    assert r.status_code == 404
    assert client.post("/ai/chat", headers=magasin["admin"], json={"message": "   "}).status_code == 400


def test_modele_retire_bascule_automatique(app, monkeypatch):
    """Si Groq retire un modèle (erreur 404 model_not_found), le suivant est utilisé et mémorisé."""

    class Retire(Exception):
        status_code = 404

    essais = []

    class FauxClient:
        class chat:
            class completions:
                @staticmethod
                def create(model, **kwargs):
                    essais.append((model, kwargs))
                    if model == "ancien-modele":
                        raise Retire("model_not_found")
                    message = type("M", (), {"content": "Réponse"})()
                    return type("R", (), {"choices": [type("C", (), {"message": message, "finish_reason": "stop"})()]})()

    monkeypatch.setenv("GROQ_MODEL", "ancien-modele")
    monkeypatch.setattr(ia_contexte, "client_ia", lambda: FauxClient())
    monkeypatch.setattr(ia_contexte, "_modele_actif", None)
    assert ia_contexte.appeler_ia([{"role": "user", "content": "test"}]) == "Réponse"
    assert [m for m, _ in essais] == ["ancien-modele", "openai/gpt-oss-120b"]
    assert essais[1][1]["reasoning_effort"] == "low"
    assert ia_contexte.modele() == "openai/gpt-oss-120b"  # mémorisé pour les appels suivants
