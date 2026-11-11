"""Assistant IA de Lumistock : analyse du magasin et conversation.

Les données envoyées au modèle viennent de services.ia_contexte (mêmes
calculs que le tableau de bord, taille limitée). Les erreurs remontent sous
forme d'exceptions IANonConfiguree / IAErreur, traduites en messages clairs
par les routes.
"""

import time as horloge
from datetime import datetime

from models.chat_conversation_model import ChatConversationModel
from models.chat_message_model import ChatMessageModel
from services.ia_contexte import (
    IAErreur, IANonConfiguree, appeler_ia, client_ia, date_locale_texte, faits_en_texte, faits_utilisateur,
)

DUREE_CACHE_ANALYSE = 15 * 60  # secondes : une analyse coûte, on ne la refait pas à chaque clic
HISTORIQUE_MAX = 20  # messages gardés dans une conversation

CONSIGNES_ANALYSE = """Tu es le conseiller commercial de Lumistock, un logiciel de gestion de stock et de ventes pour des commerces en Afrique centrale.
Tu rédiges une analyse pour le responsable du magasin, à partir UNIQUEMENT des chiffres fournis.

Règles :
- N'invente aucun chiffre, aucun article, aucun client. Si une donnée manque, dis-le simplement.
- Écris chaque montant en entier, tel qu'il figure dans les données, avec un espace entre les milliers, suivi de la devise (ex. 20 283 512 FCFA ; 620 000 FCFA). N'abrège jamais : pas de « M », « k », « millions » ni « mille ».
- Ton professionnel, factuel et bienveillant ; pas d'alarmisme ni d'exagération.
- Chaque recommandation est une action concrète, faisable cette semaine, avec l'article ou le client concerné.
- Classe les priorités par impact sur la trésorerie et la marge.
- Reste cohérent : une remise baisse la marge ; pour l'améliorer, propose une hausse de prix ou un meilleur prix d'achat.
- Emploie « stock qui dort » uniquement pour les articles listés comme tels ; un stock important n'est pas forcément dormant.
- N'attribue pas de retour à des clients précis : les données ne disent pas qui a retourné quoi.
- Réponds en français, en Markdown, avec exactement ces titres de niveau 2 :

## Synthèse
(3 phrases maximum : la situation du magasin en clair)
## Priorités de la semaine
(3 à 5 actions numérotées, la plus importante d'abord)
## Points de vigilance
(risques à surveiller : marge, créances, ruptures, stock qui dort, retours)
## Opportunités
(articles ou créneaux à exploiter)

Reste concis : 300 mots maximum."""

CONSIGNES_GERANT = """Tu es le conseiller de vente de Lumistock. Tu t'adresses à un gérant (vendeur sur le terrain) à partir UNIQUEMENT des chiffres fournis.
N'invente rien. Écris chaque montant en entier, tel qu'il figure dans les données, avec un espace entre les milliers, suivi de la devise (ex. 20 283 512 FCFA ; 620 000 FCFA). N'abrège jamais : pas de « M », « k », « millions » ni « mille ».
Ton encourageant et professionnel. Réponds en français, en Markdown, avec ces titres de niveau 2 :
## Mes chiffres
## À faire aujourd'hui
(clients à relancer, articles à proposer, stock à écouler)
250 mots maximum."""

CONSIGNES_CAISSIER = """Tu es le conseiller de vente de Lumistock. Tu t'adresses à un caissier (vente au comptoir) à partir UNIQUEMENT des chiffres fournis : ses propres ventes, sa caisse et ses clients à crédit.
N'invente rien. Ne parle jamais de marge ni de prix d'achat (le caissier n'y a pas accès). Écris chaque montant en entier, tel qu'il figure dans les données, avec un espace entre les milliers, suivi de la devise (ex. 20 283 512 FCFA ; 620 000 FCFA). N'abrège jamais : pas de « M », « k », « millions » ni « mille ».
Ton encourageant et professionnel. Réponds en français, en Markdown, avec ces titres de niveau 2 :
## Ma journée
## Ma caisse
(fond, espèces attendues, conseils pour une fermeture sans écart)
## À faire
(clients à relancer, articles à proposer)
250 mots maximum."""

CONSIGNES_CHAT = """Tu es l'assistant de Lumistock, logiciel de gestion de stock et de ventes. Tu aides {role} du magasin à comprendre ses données et à décider.
Règles : réponds en français, de façon concise et professionnelle ; appuie-toi uniquement sur les données ci-dessous ;
si la réponse n'y figure pas, dis-le et indique où la trouver dans l'application (Tableau de bord, Articles, Clients, Rapports, Comptabilité).
N'invente jamais de chiffre. Écris chaque montant en entier, tel qu'il figure dans les données, avec un espace entre les milliers, suivi de la devise (ex. 20 283 512 FCFA ; 620 000 FCFA). N'abrège jamais : pas de « M », « k », « millions » ni « mille ».
Utilise le Markdown pour les listes et les tableaux courts.

Nous sommes le {date}.

DONNÉES DU MAGASIN :
{faits}"""

_cache_analyses = {}


class AIService:
    @property
    def client(self):
        """Client Groq (compatibilité avec les services vocaux), ou None si non configuré."""
        return client_ia()

    # ------------------------------------------------------------------ analyse
    def get_business_insights(self, user, forcer=False):
        """Analyse du magasin (admin/manager) ou du portefeuille d'un gérant.

        :raises IANonConfiguree, IAErreur:
        """
        cle = (str(user["_id"]), user.get("role"))
        maintenant = horloge.monotonic()
        en_cache = _cache_analyses.get(cle)
        if en_cache and not forcer and maintenant - en_cache[0] < DUREE_CACHE_ANALYSE:
            return {**en_cache[1], "depuis_cache": True}

        faits = faits_utilisateur(user)
        role = user.get("role")
        consignes = (CONSIGNES_ANALYSE if role in ("admin", "manager")
                     else CONSIGNES_CAISSIER if role == "caissier" else CONSIGNES_GERANT)
        texte = appeler_ia(
            [
                {"role": "system", "content": consignes},
                {"role": "user", "content": f"Nous sommes le {date_locale_texte()}.\n\n{faits_en_texte(faits)}"},
            ],
            temperature=0.3,
            max_tokens=1300,  # montants écrits en entier : réponse plus longue, ne pas la couper
        )
        resultat = {"insights": texte, "genere_le": datetime.utcnow().isoformat() + "Z", "periode": faits.get("periode")}
        _cache_analyses[cle] = (maintenant, resultat)
        return {**resultat, "depuis_cache": False}

    # ------------------------------------------------------------------ conversation
    def chat(self, message, user, conversation_id=None):
        """Répond à un message et enregistre l'échange.

        La conversation et les messages ne sont enregistrés qu'une fois la
        réponse obtenue : un échec ne laisse pas de conversation vide.

        :raises LookupError: conversation introuvable pour cet utilisateur
        :raises IANonConfiguree, IAErreur:
        """
        user_id, magasin_id = str(user["_id"]), str(user["magasin_id"])
        historique = []
        if conversation_id:
            if not ChatConversationModel.verify_ownership(conversation_id, user_id, magasin_id):
                raise LookupError("Conversation introuvable.")
            historique = ChatMessageModel.get_conversation_history(conversation_id, limit=HISTORIQUE_MAX)

        role = {"admin": "l'administrateur", "manager": "le manager", "caissier": "un caissier"}.get(user.get("role"), "un gérant")
        consignes = CONSIGNES_CHAT.format(role=role, date=date_locale_texte(), faits=faits_en_texte(faits_utilisateur(user)))
        reponse = appeler_ia(
            [{"role": "system", "content": consignes}, *historique, {"role": "user", "content": message}],
            temperature=0.4,
            max_tokens=1024,
        )

        if not conversation_id:
            titre = message if len(message) <= 50 else message[:50].rstrip() + "…"
            conversation_id = str(ChatConversationModel.create(user_id, magasin_id, titre)["_id"])
        ChatMessageModel.create(conversation_id, "user", message)
        message_ia = ChatMessageModel.create(conversation_id, "assistant", reponse)
        ChatConversationModel.update_timestamp(conversation_id)
        return {"response": reponse, "conversation_id": conversation_id, "message_id": str(message_ia["_id"])}


def vider_cache_analyses():
    _cache_analyses.clear()


ai_service = AIService()

__all__ = ["ai_service", "AIService", "IAErreur", "IANonConfiguree", "vider_cache_analyses"]
