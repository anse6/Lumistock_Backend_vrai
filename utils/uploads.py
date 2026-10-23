# utils/uploads.py
"""Enregistrement sécurisé des images envoyées par les utilisateurs."""

import os
import uuid

from flask import current_app
from werkzeug.utils import secure_filename

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# Signatures binaires des formats autorisés : l'extension et le type MIME
# sont fournis par le client, seul le contenu du fichier fait foi.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def _detect_image_format(header):
    for signature, image_format in _IMAGE_SIGNATURES:
        if header.startswith(signature):
            return image_format
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


def save_image(file_storage):
    """Enregistre une image sous un nom généré par le serveur.

    Le nom d'origine n'est jamais réutilisé : deux produits (ou magasins)
    envoyant chacun « photo.jpg » ne peuvent plus s'écraser, et un fichier
    HTML ou script déguisé en image est refusé.

    :return: le nom du fichier enregistré, ou None si aucun fichier n'est fourni
    :raises ValueError: si le fichier n'est pas une image autorisée
    """
    if not file_storage or not file_storage.filename:
        return None

    parts = secure_filename(file_storage.filename).rsplit(".", 1)
    extension = parts[1].lower() if len(parts) == 2 else ""
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Format d'image non autorisé. Utilisez PNG, JPG, GIF ou WEBP.")

    header = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    if _detect_image_format(header) is None:
        raise ValueError("Le fichier envoyé n'est pas une image valide.")

    filename = f"{uuid.uuid4().hex}.{'jpg' if extension == 'jpeg' else extension}"
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_dir, exist_ok=True)
    file_storage.save(os.path.join(upload_dir, filename))
    return filename
