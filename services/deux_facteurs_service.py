"""Double authentification facultative (TOTP, RFC 6238).

Chaque utilisateur peut l'activer depuis ses paramètres : il scanne un QR code
avec une application d'authentification (Google Authenticator, Microsoft
Authenticator, Authy…), puis saisit à chaque connexion le code à 6 chiffres
affiché par l'application, en plus de son mot de passe.

- Le secret est rangé dans la table `deux_facteurs`, jamais dans le profil :
  il ne peut pas sortir avec /users/me ou la liste de l'équipe.
- Un code déjà utilisé est refusé (pas de rejeu) ; une petite tolérance
  d'horloge (±30 s) est acceptée.
- 10 codes de secours à usage unique sont donnés à l'activation (stockés
  hachés) pour se connecter si le téléphone est perdu.
- Un admin (droit « Gérer l'équipe ») ou le staff peut la désactiver pour un
  membre qui a perdu son téléphone et ses codes.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime
from urllib.parse import quote

from bson import ObjectId

from extensions import db

EMETTEUR = "Lumistock"
PERIODE = 30
CHIFFRES = 6
TOLERANCE = 1  # pas de 30 s acceptés avant/après (décalage d'horloge du téléphone)
NB_CODES_SECOURS = 10


class ErreurDeuxFacteurs(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _table():
    return db.tables.deux_facteurs


# --------------------------------------------------------------------------- TOTP
def nouveau_secret():
    """Secret de 160 bits encodé en base32 (format attendu par les applications)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _cle(secret):
    secret = secret.upper()
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def code_totp(secret, pas):
    """Code à 6 chiffres pour le pas de temps `pas` (HOTP, RFC 4226)."""
    mac = hmac.new(_cle(secret), struct.pack(">Q", pas), hashlib.sha1).digest()
    decalage = mac[-1] & 0x0F
    valeur = struct.unpack(">I", mac[decalage:decalage + 4])[0] & 0x7FFFFFFF
    return str(valeur % (10 ** CHIFFRES)).zfill(CHIFFRES)


def pas_courant(instant=None):
    return int((instant if instant is not None else time.time()) // PERIODE)


def verifier_totp(secret, code, dernier_pas=None, instant=None):
    """Pas de temps du code s'il est valide (et pas déjà utilisé), sinon None."""
    code = "".join(c for c in str(code or "") if c.isdigit())
    if len(code) != CHIFFRES or not secret:
        return None
    courant = pas_courant(instant)
    for pas in range(courant - TOLERANCE, courant + TOLERANCE + 1):
        if dernier_pas is not None and pas <= int(dernier_pas):
            continue
        if hmac.compare_digest(code_totp(secret, pas), code):
            return pas
    return None


def uri_otpauth(secret, email):
    """Lien lu par les applications d'authentification (contenu du QR code)."""
    libelle = f"{quote(EMETTEUR)}:{quote(email)}"
    return f"otpauth://totp/{libelle}?secret={secret}&issuer={quote(EMETTEUR)}&algorithm=SHA1&digits={CHIFFRES}&period={PERIODE}"


# --------------------------------------------------------------------------- codes de secours
def _hacher(code):
    return hashlib.sha256(code.replace("-", "").replace(" ", "").upper().encode()).hexdigest()


def _nouveaux_codes():
    """10 codes lisibles du type 7KQ2-M9XD (alphabet sans 0/O ni 1/I)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes = []
    for _ in range(NB_CODES_SECOURS):
        brut = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(f"{brut[:4]}-{brut[4:]}")
    return codes


# --------------------------------------------------------------------------- état d'un compte
def _fiche(user_id):
    return _table().find_one({"user_id": ObjectId(str(user_id))})


def est_active(user_id):
    fiche = _fiche(user_id)
    return bool(fiche and fiche.get("actif"))


def actifs_parmi(user_ids):
    """Ensemble des identifiants (str) qui ont la double authentification active."""
    ids = [ObjectId(str(u)) for u in user_ids if ObjectId.is_valid(str(u))]
    if not ids:
        return set()
    return {str(f["user_id"]) for f in _table().find({"user_id": {"$in": ids}, "actif": True})}


def etat(user_id):
    fiche = _fiche(user_id) or {}
    actif = bool(fiche.get("actif"))
    return {
        "actif": actif,
        "active_le": fiche.get("active_le").isoformat() + "Z" if actif and fiche.get("active_le") else None,
        "codes_secours_restants": len(fiche.get("codes_secours") or []) if actif else 0,
    }


# --------------------------------------------------------------------------- activation
def initialiser(user):
    """Prépare un nouveau secret (pas encore actif) et renvoie de quoi le scanner."""
    fiche = _fiche(user["_id"])
    if fiche and fiche.get("actif"):
        raise ErreurDeuxFacteurs("La double authentification est déjà activée.", 409)
    secret = nouveau_secret()
    if fiche:
        _table().update_one({"_id": fiche["_id"]}, {"$set": {"secret_en_attente": secret}})
    else:
        _table().insert_one({"user_id": ObjectId(str(user["_id"])), "actif": False, "secret_en_attente": secret,
                             "created_at": datetime.utcnow()})
    return {"secret": secret, "uri": uri_otpauth(secret, user.get("email") or "")}


def activer(user, code):
    """Active après vérification d'un premier code ; renvoie les codes de secours (une seule fois)."""
    fiche = _fiche(user["_id"])
    if fiche and fiche.get("actif"):
        raise ErreurDeuxFacteurs("La double authentification est déjà activée.", 409)
    secret = (fiche or {}).get("secret_en_attente")
    if not secret:
        raise ErreurDeuxFacteurs("Recommencez : scannez d'abord le QR code.")
    pas = verifier_totp(secret, code)
    if pas is None:
        raise ErreurDeuxFacteurs("Code incorrect. Vérifiez l'heure de votre téléphone et réessayez.")
    codes = _nouveaux_codes()
    _table().update_one({"_id": fiche["_id"]}, {"$set": {
        "actif": True, "secret": secret, "secret_en_attente": None, "dernier_pas": pas,
        "codes_secours": [_hacher(c) for c in codes], "active_le": datetime.utcnow(),
    }})
    return codes


def desactiver(user_id):
    _table().delete_many({"user_id": ObjectId(str(user_id))})


def regenerer_codes(user_id):
    fiche = _fiche(user_id)
    if not fiche or not fiche.get("actif"):
        raise ErreurDeuxFacteurs("La double authentification n'est pas activée.", 409)
    codes = _nouveaux_codes()
    _table().update_one({"_id": fiche["_id"]}, {"$set": {"codes_secours": [_hacher(c) for c in codes]}})
    return codes


def verifier(user_id, code):
    """Contrôle à la connexion : code de l'application ou code de secours.

    :return: "totp", "secours" ou None si le code est refusé.
    """
    fiche = _fiche(user_id)
    if not fiche or not fiche.get("actif"):
        return None
    texte = str(code or "").strip()
    chiffres = "".join(c for c in texte if c.isdigit())
    if len(chiffres) == CHIFFRES and len(texte.replace(" ", "")) == CHIFFRES:
        pas = verifier_totp(fiche.get("secret"), chiffres, fiche.get("dernier_pas"))
        if pas is None:
            return None
        # Le pas utilisé est mémorisé : ce code (et les précédents) ne
        # pourront plus resservir.
        _table().update_one({"_id": fiche["_id"]}, {"$set": {"dernier_pas": pas}})
        return "totp"
    empreinte = _hacher(texte)
    restants = list(fiche.get("codes_secours") or [])
    if empreinte not in restants:
        return None
    restants.remove(empreinte)
    _table().update_one({"_id": fiche["_id"]}, {"$set": {"codes_secours": restants}})
    return "secours"
