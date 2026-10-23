# utils/pieces_jointes.py
"""Pièces justificatives (factures, reçus) jointes aux charges et aux factures
fournisseurs.

Les fichiers sont rangés hors du dossier public ``uploads`` (servi tel quel
par nginx) : ils ne sont lisibles que par la route protégée de leur magasin.
"""
import os
import uuid
from datetime import datetime

from flask import current_app
from werkzeug.utils import secure_filename

TAILLE_MAX = 10 * 1024 * 1024  # 10 Mo
NOMBRE_MAX = 10

# Le contenu fait foi, pas l'extension ni le type annoncé par le navigateur.
_SIGNATURES = (
    (b"%PDF-", "pdf", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
)


def dossier():
    chemin = current_app.config.get("PIECES_JOINTES_FOLDER") or os.path.join(os.getcwd(), "private", "pieces_jointes")
    os.makedirs(chemin, exist_ok=True)
    return chemin


def _format(entete):
    for signature, extension, mime in _SIGNATURES:
        if entete.startswith(signature):
            return extension, mime
    if entete[:4] == b"RIFF" and entete[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def enregistrer(fichier, par=None):
    """Enregistre le fichier et retourne sa description (à stocker dans le document)."""
    if not fichier or not fichier.filename:
        raise ValueError("Aucun fichier reçu.")
    entete = fichier.stream.read(16)
    fichier.stream.seek(0, os.SEEK_END)
    taille = fichier.stream.tell()
    fichier.stream.seek(0)
    detecte = _format(entete)
    if not detecte:
        raise ValueError("Format non autorisé : envoyez un PDF ou une photo (PNG, JPG, WEBP).")
    if taille > TAILLE_MAX:
        raise ValueError("Fichier trop lourd (10 Mo maximum).")
    extension, mime = detecte
    identifiant = uuid.uuid4().hex
    nom_disque = f"{identifiant}.{extension}"
    fichier.save(os.path.join(dossier(), nom_disque))
    return {
        "id": identifiant,
        "nom": secure_filename(fichier.filename) or f"piece.{extension}",
        "fichier": nom_disque,
        "type": mime,
        "taille": taille,
        "date": datetime.utcnow().isoformat(),
        "par": par,
    }


def chemin(piece):
    return os.path.join(dossier(), os.path.basename(piece["fichier"]))


def supprimer(piece):
    try:
        os.remove(chemin(piece))
    except OSError:
        pass
