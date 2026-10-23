"""Synthèse vocale des réponses de l'assistant (edge-tts) et ménage des fichiers audio."""

import asyncio
import os
import time
import uuid

DOSSIER_AUDIO = os.path.join("uploads", "audio")
VOIX = "fr-FR-HenriNeural"
DUREE_CONSERVATION = 60 * 60  # secondes : un fichier audio n'est servi qu'une fois


def _nettoyer_anciens_fichiers():
    """Supprime les MP3 de plus d'une heure (ils s'accumulaient sans fin)."""
    limite = time.time() - DUREE_CONSERVATION
    try:
        for nom in os.listdir(DOSSIER_AUDIO):
            chemin = os.path.join(DOSSIER_AUDIO, nom)
            if nom.endswith(".mp3") and os.path.getmtime(chemin) < limite:
                os.remove(chemin)
    except OSError:
        pass


def texte_en_mp3(texte, prefixe):
    """Transforme le texte en fichier MP3 et renvoie son chemin."""
    import edge_tts

    os.makedirs(DOSSIER_AUDIO, exist_ok=True)
    _nettoyer_anciens_fichiers()
    chemin = os.path.join(DOSSIER_AUDIO, f"{prefixe}_{uuid.uuid4().hex}.mp3")
    asyncio.run(edge_tts.Communicate(texte, VOIX).save(chemin))
    return chemin
