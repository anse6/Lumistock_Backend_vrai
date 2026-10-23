"""Réponse vocale à une question posée à l'assistant."""

from models.user_model import UserModel
from services.ia_contexte import IAErreur, IANonConfiguree, appeler_ia, date_locale_texte, faits_en_texte, faits_utilisateur
from services.voix_ia import texte_en_mp3

CONSIGNES = """Tu es l'assistant vocal de Lumistock. {nom} ({role}) te pose une question sur son magasin.
Réponds directement, en 80 mots maximum, uniquement à partir des données fournies. Si la réponse n'y figure pas, dis-le simplement.
Texte destiné à être lu à voix haute : phrases simples, montants arrondis, aucune liste, aucun symbole, aucun Markdown. Français."""


class InteractiveVoiceService:
    @staticmethod
    def get_answer_text(user_id, magasin_id, question):
        user = UserModel.get_user_by_id(user_id)
        if not user or not user.get("magasin_id"):
            return "Je n'ai pas trouvé les informations de votre compte."
        nom = (user.get("first_name") or user.get("name") or "").strip() or "Bonjour"
        role = {"admin": "administrateur", "manager": "manager"}.get(user.get("role"), "gérant")
        try:
            return appeler_ia(
                [
                    {"role": "system", "content": CONSIGNES.format(nom=nom, role=role)},
                    {"role": "user", "content": f"Nous sommes le {date_locale_texte()}.\n\nDONNÉES :\n{faits_en_texte(faits_utilisateur(user))}\n\nQUESTION : {question}"},
                ],
                temperature=0.4,
                max_tokens=350,
            )
        except IANonConfiguree:
            return "L'assistant vocal n'est pas encore configuré sur ce serveur."
        except IAErreur:
            return "Désolé, je ne peux pas répondre pour le moment. Réessayez dans quelques instants."

    @staticmethod
    def generate_mp3(user_id, magasin_id, question):
        return texte_en_mp3(InteractiveVoiceService.get_answer_text(user_id, magasin_id, question), "reponse")


interactive_voice_service = InteractiveVoiceService()
