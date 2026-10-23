# services/sms_service.py
"""Envoi de SMS par un fournisseur configuré dans le .env de la plateforme.

Fournisseurs pris en charge (variable SMS_PROVIDER) :
- ``orange``  : API SMS d'Orange Developer (Cameroun, Côte d'Ivoire, Sénégal…),
                achat de forfaits SMS sur developer.orange.com ;
- ``twilio``  : Twilio, couverture internationale ;
- ``console`` : simulation (développement) — rien n'est envoyé, le message est
                écrit dans les journaux du serveur ;
- vide        : SMS désactivés.

Chaque envoi retourne ``(ok, reference, erreur)`` : ``reference`` est
l'identifiant du message chez le fournisseur (pour le suivi), ``erreur`` un
message lisible en cas d'échec.
"""
import base64
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

FOURNISSEURS = ("orange", "twilio", "console")
DELAI_HTTP = 15  # secondes

# Alphabet GSM 03.38 (un SMS de 160 caractères) ; tout autre caractère
# (emoji, ê, â, ô…) fait passer le SMS en Unicode (70 caractères).
_GSM_BASE = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM_ETENDU = set("^{}\\[~]|€")  # comptent pour 2 caractères


def compter_sms(texte):
    """Nombre de caractères et de SMS facturés pour ``texte``."""
    texte = texte or ""
    if all(c in _GSM_BASE or c in _GSM_ETENDU for c in texte):
        longueur = sum(2 if c in _GSM_ETENDU else 1 for c in texte)
        parts = 1 if longueur <= 160 else -(-longueur // 153)
        return {"caracteres": longueur, "sms": parts, "encodage": "GSM"}
    longueur = len(texte)
    parts = 1 if longueur <= 70 else -(-longueur // 67)
    return {"caracteres": longueur, "sms": parts, "encodage": "Unicode"}


def numero_sms_valide(numero):
    """Numéro international (+ suivi de 8 à 15 chiffres), hors numéro fictif."""
    return bool(numero) and re.fullmatch(r"\+[1-9]\d{7,14}", numero) is not None


def fournisseur():
    nom = (current_app.config.get("SMS_PROVIDER") or "").strip().lower()
    return nom if nom in FOURNISSEURS else None


def etat_sms():
    """État de la configuration SMS, affiché dans les paramètres du magasin."""
    nom = fournisseur()
    cfg = current_app.config
    manquant = []
    if nom == "orange":
        manquant = [k for k in ("ORANGE_CLIENT_ID", "ORANGE_CLIENT_SECRET") if not cfg.get(k)]
    elif nom == "twilio":
        manquant = [k for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN") if not cfg.get(k)]
        if not cfg.get("TWILIO_FROM") and not cfg.get("TWILIO_MESSAGING_SERVICE_SID"):
            manquant.append("TWILIO_FROM")
    return {
        "fournisseur": nom,
        "configure": bool(nom) and not manquant,
        "simulation": nom == "console",
        "manquant": manquant,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _requete(url, data=None, headers=None, methode="POST"):
    """Requête HTTP ; retourne (code, corps décodé en JSON ou texte)."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=DELAI_HTTP) as rep:
            brut = rep.read().decode("utf-8", "replace")
            code = rep.status
    except urllib.error.HTTPError as e:
        brut = e.read().decode("utf-8", "replace")
        code = e.code
    try:
        return code, json.loads(brut) if brut else {}
    except ValueError:
        return code, brut


def _message_erreur(corps, defaut):
    if isinstance(corps, dict):
        for cle in ("message", "error_description", "description", "detail"):
            if corps.get(cle):
                return str(corps[cle])
        req = corps.get("requestError") or {}
        for bloc in req.values() if isinstance(req, dict) else []:
            if isinstance(bloc, dict) and bloc.get("text"):
                return str(bloc["text"])
    return defaut


# ---------------------------------------------------------------------------
# Orange (developer.orange.com — API « SMS »)
# ---------------------------------------------------------------------------
_jeton_orange = {"valeur": None, "expire": 0.0}
_verrou_orange = threading.Lock()


def _jeton_orange_valide(forcer=False):
    with _verrou_orange:
        if not forcer and _jeton_orange["valeur"] and time.time() < _jeton_orange["expire"] - 60:
            return _jeton_orange["valeur"]
        cfg = current_app.config
        identifiants = f"{cfg['ORANGE_CLIENT_ID']}:{cfg['ORANGE_CLIENT_SECRET']}".encode("utf-8")
        code, corps = _requete(
            "https://api.orange.com/oauth/v3/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": "Basic " + base64.b64encode(identifiants).decode("ascii"),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        if code != 200 or not isinstance(corps, dict) or not corps.get("access_token"):
            raise RuntimeError(_message_erreur(corps, f"Authentification Orange refusée (HTTP {code})."))
        _jeton_orange["valeur"] = corps["access_token"]
        _jeton_orange["expire"] = time.time() + int(corps.get("expires_in", 3600))
        return _jeton_orange["valeur"]


def _envoyer_orange(numero, texte):
    cfg = current_app.config
    expediteur = (cfg.get("ORANGE_SENDER_NUMBER") or "+2370000").strip()
    if not expediteur.startswith("+"):
        expediteur = "+" + expediteur
    requete = {
        "address": f"tel:{numero}",
        "senderAddress": f"tel:{expediteur}",
        "outboundSMSTextMessage": {"message": texte},
    }
    if cfg.get("SMS_SENDER_NAME"):
        requete["senderName"] = cfg["SMS_SENDER_NAME"]
    url = ("https://api.orange.com/smsmessaging/v1/outbound/"
           f"{urllib.parse.quote('tel:' + expediteur, safe='')}/requests")
    corps_json = json.dumps({"outboundSMSMessageRequest": requete}).encode("utf-8")

    for tentative in range(2):
        jeton = _jeton_orange_valide(forcer=tentative > 0)
        code, corps = _requete(url, data=corps_json, headers={
            "Authorization": f"Bearer {jeton}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        if code == 401 and tentative == 0:
            continue  # jeton expiré : on en redemande un
        break
    if code in (200, 201):
        ref = ""
        if isinstance(corps, dict):
            ref = (corps.get("outboundSMSMessageRequest") or {}).get("resourceURL", "")
        return True, ref.rsplit("/", 1)[-1] if ref else "", None
    return False, None, _message_erreur(corps, f"Envoi refusé par Orange (HTTP {code}).")


# ---------------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------------
def _envoyer_twilio(numero, texte):
    cfg = current_app.config
    sid = cfg["TWILIO_ACCOUNT_SID"]
    champs = {"To": numero, "Body": texte}
    if cfg.get("TWILIO_MESSAGING_SERVICE_SID"):
        champs["MessagingServiceSid"] = cfg["TWILIO_MESSAGING_SERVICE_SID"]
    else:
        champs["From"] = cfg["TWILIO_FROM"]
    identifiants = f"{sid}:{cfg['TWILIO_AUTH_TOKEN']}".encode("utf-8")
    code, corps = _requete(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=urllib.parse.urlencode(champs).encode("utf-8"),
        headers={
            "Authorization": "Basic " + base64.b64encode(identifiants).decode("ascii"),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    if code in (200, 201) and isinstance(corps, dict):
        return True, corps.get("sid", ""), None
    return False, None, _message_erreur(corps, f"Envoi refusé par Twilio (HTTP {code}).")


# ---------------------------------------------------------------------------
def envoyer_sms(numero, texte):
    """Envoie un SMS. Retourne (ok, reference, erreur)."""
    etat = etat_sms()
    if not etat["configure"]:
        return False, None, "Les SMS ne sont pas configurés sur la plateforme."
    if not numero_sms_valide(numero):
        return False, None, "Numéro de téléphone invalide."
    try:
        if etat["fournisseur"] == "console":
            current_app.logger.info("[SMS simulé] à %s : %s", numero, texte)
            return True, "simulation", None
        if etat["fournisseur"] == "orange":
            return _envoyer_orange(numero, texte)
        return _envoyer_twilio(numero, texte)
    except Exception as e:  # réseau, authentification…
        current_app.logger.error("Erreur d'envoi SMS à %s : %s", numero, e)
        return False, None, str(e) or "Erreur réseau lors de l'envoi du SMS."


def solde_orange():
    """SMS restants sur les forfaits Orange (None si indisponible)."""
    if fournisseur() != "orange" or not etat_sms()["configure"]:
        return None
    try:
        code, corps = _requete("https://api.orange.com/sms/admin/v1/contracts", methode="GET",
                               headers={"Authorization": f"Bearer {_jeton_orange_valide()}",
                                        "Accept": "application/json"})
    except Exception:
        return None
    if code != 200 or not isinstance(corps, list):
        return None
    return [{"pays": c.get("country"), "sms_restants": c.get("availableUnits"),
             "expiration": c.get("expirationDate"), "statut": c.get("status")} for c in corps]
