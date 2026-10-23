# utils/rate_limit.py
"""Limitation simple du nombre de tentatives (fenêtre glissante, en mémoire).

Elle complète le verrouillage des comptes (stocké en base) : elle freine une
même adresse IP qui essaie beaucoup d'emails différents, ou les demandes
répétées de liens de réinitialisation. Le compteur est propre à chaque
processus : avec plusieurs workers, la limite effective est multipliée par leur
nombre, ce qui reste un frein efficace.
"""
import re
import threading
import time
from collections import defaultdict, deque

_tentatives = defaultdict(deque)
_verrou = threading.Lock()


def limite_atteinte(cle, maximum, fenetre_secondes):
    """Enregistre une tentative pour ``cle``.

    :return: secondes à attendre si la limite est dépassée, sinon 0.
    """
    maintenant = time.monotonic()
    with _verrou:
        file = _tentatives[cle]
        while file and file[0] <= maintenant - fenetre_secondes:
            file.popleft()
        if len(file) >= maximum:
            return int(file[0] + fenetre_secondes - maintenant) + 1
        file.append(maintenant)
        # Nettoyage occasionnel des clés inactives.
        if len(_tentatives) > 10000:
            for ancienne in [k for k, v in _tentatives.items() if not v or v[-1] <= maintenant - fenetre_secondes]:
                _tentatives.pop(ancienne, None)
    return 0


def piege_rempli(valeur):
    """Champ invisible anti-robots rempli ? Une adresse email n'est pas retenue :
    c'est le remplissage automatique du navigateur, pas un robot."""
    valeur = str(valeur or "").strip()
    return bool(valeur) and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", valeur)


def reinitialiser(cle):
    with _verrou:
        _tentatives.pop(cle, None)


def tout_reinitialiser():
    """Pour les tests."""
    with _verrou:
        _tentatives.clear()
