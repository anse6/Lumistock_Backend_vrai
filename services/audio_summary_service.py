"""Compte rendu vocal du jour, à partir des chiffres réels du magasin."""

import logging

from models.user_model import UserModel
from services.ia_contexte import IAErreur, IANonConfiguree, appeler_ia, date_locale_texte, faits_en_texte, faits_utilisateur
from services.voix_ia import texte_en_mp3

journal = logging.getLogger(__name__)

CONSIGNES = """Tu es l'assistant vocal de Lumistock. Tu fais un compte rendu oral à {nom}, {role} du magasin.
Commence par « Bonjour {nom} ». En 90 mots environ, donne : les ventes et l'encaissé de la période, les clients qui doivent le plus,
les produits qui vont manquer ou sont en rupture, et une seule action prioritaire.
Utilise uniquement les données fournies, n'invente rien. Texte destiné à être lu à voix haute : phrases simples,
montants arrondis (ex. « 4 millions 360 mille francs »), aucune liste, aucun symbole, aucun Markdown. Français."""


def _nom(user):
    return (user.get("first_name") or user.get("name") or "").strip() or "à vous"


class AudioSummaryService:
    @staticmethod
    def get_summary_text(user_id, magasin_id):
        user = UserModel.get_user_by_id(user_id)
        if not user or not user.get("magasin_id"):
            return "Bonjour. Je n'ai pas trouvé les informations de votre compte."
        nom = _nom(user)
        role = {"admin": "administrateur", "manager": "manager"}.get(user.get("role"), "gérant")
        try:
            return appeler_ia(
                [
                    {"role": "system", "content": CONSIGNES.format(nom=nom, role=role)},
                    {"role": "user", "content": f"Nous sommes le {date_locale_texte()}.\n\n{faits_en_texte(faits_utilisateur(user))}"},
                ],
                temperature=0.5,
                max_tokens=400,
            )
        except IANonConfiguree:
            return f"Bonjour {nom}. L'assistant vocal n'est pas encore configuré sur ce serveur."
        except IAErreur:
            return f"Bonjour {nom}. Le service d'analyse est momentanément indisponible. Réessayez dans quelques instants."

    @staticmethod
    def generate_mp3(user_id, magasin_id):
        return texte_en_mp3(AudioSummaryService.get_summary_text(user_id, magasin_id), "resume")


audio_summary_service = AudioSummaryService()
