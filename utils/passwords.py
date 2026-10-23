# utils/passwords.py
"""Génération de mots de passe temporaires."""

import secrets
import string


def generate_password(length=12):
    """Mot de passe aléatoire (module `secrets`) contenant au moins une
    minuscule, une majuscule et un chiffre. Les caractères ambigus à la
    lecture d'un email (0/O, 1/l/I) sont exclus."""
    lower = "abcdefghijkmnopqrstuvwxyz"
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    digits = "23456789"
    alphabet = lower + upper + digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c in lower for c in password)
                and any(c in upper for c in password)
                and any(c in digits for c in password)):
            return password


LONGUEUR_MIN = 8


def erreur_robustesse(password, email=None):
    """Règle commune à tous les mots de passe choisis par un utilisateur.

    :return: message d'erreur, ou None si le mot de passe est accepté.
    """
    if not isinstance(password, str) or len(password) < LONGUEUR_MIN:
        return f"Le mot de passe doit contenir au moins {LONGUEUR_MIN} caractères."
    if len(password) > 128:
        return "Le mot de passe ne doit pas dépasser 128 caractères."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Le mot de passe doit contenir au moins une lettre et un chiffre."
    if email and password.strip().lower() == str(email).strip().lower():
        return "Le mot de passe ne doit pas être identique à l'adresse email."
    return None
