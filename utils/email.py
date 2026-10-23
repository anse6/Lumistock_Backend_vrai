# utils/email.py
"""Emails transactionnels.

Règle de sécurité : aucun mot de passe n'est jamais envoyé par email. Un compte
créé (magasin, admin, manager, gérant, staff) reçoit un lien d'invitation à
usage unique pour choisir lui-même son mot de passe ; un mot de passe oublié ou
réinitialisé par un administrateur reçoit un lien de réinitialisation.
"""
import smtplib
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app, render_template


# Valeurs d'exemple du .env : à remplacer par le vrai mot de passe d'application.
_MOTS_DE_PASSE_EXEMPLE = {"", "TON_MOT_DE_PASSE_APPLICATION", "votre_mot_de_passe", "changeme"}


def _envoyer(msg, to_email):
    """Envoie un message SMTP. Retourne True si l'email est parti."""
    mot_de_passe = (current_app.config.get("EMAIL_HOST_PASSWORD") or "").strip()
    if mot_de_passe in _MOTS_DE_PASSE_EXEMPLE:
        current_app.logger.error(
            "Email non envoyé à %s : EMAIL_HOST_PASSWORD n'est pas renseigné dans backend/.env "
            "(pour Gmail : mot de passe d'application de 16 caractères, voir myaccount.google.com/apppasswords).",
            to_email)
        return False
    try:
        # Délai maximum : un serveur mail injoignable ne bloque pas la requête.
        with smtplib.SMTP(current_app.config["EMAIL_HOST"], current_app.config["EMAIL_PORT"], timeout=15) as server:
            if current_app.config["EMAIL_USE_TLS"]:
                server.starttls()
            server.login(current_app.config["EMAIL_HOST_USER"], current_app.config["EMAIL_HOST_PASSWORD"])
            server.send_message(msg)
        current_app.logger.info("Email envoyé à %s", to_email)
        return True
    except smtplib.SMTPAuthenticationError as e:
        current_app.logger.error(
            "Email non envoyé à %s : identifiants refusés par le serveur mail (%s). Pour Gmail, utilisez un "
            "mot de passe d'application (validation en deux étapes activée), pas le mot de passe du compte.",
            to_email, e)
        return False
    except Exception as e:
        current_app.logger.error("Erreur d'envoi de mail à %s : %s", to_email, e)
        return False


PAUSES_NOUVEL_ESSAI = (10, 60)  # secondes avant le 2e puis le 3e essai


def envoyer_en_arriere_plan(app, messages):
    """Envoie des emails [(msg, destinataire)] dans un fil à part : la requête n'attend pas le serveur mail.

    Une coupure réseau passagère (Wi-Fi qui se reconnecte, DNS indisponible :
    « Name or service not known ») ne fait pas perdre l'email : chaque envoi
    est réessayé deux fois avant d'abandonner. En test, tout est synchrone.
    """
    def tache():
        with app.app_context():
            for msg, destinataire in messages:
                if _envoyer(msg, destinataire):
                    continue
                for pause in PAUSES_NOUVEL_ESSAI:
                    if not app.config.get("TESTING"):
                        time.sleep(pause)
                    app.logger.info("Nouvel essai d'envoi de l'email à %s", destinataire)
                    if _envoyer(msg, destinataire):
                        break
                else:
                    app.logger.error("Email non envoyé à %s après %d essais.", destinataire, 1 + len(PAUSES_NOUVEL_ESSAI))

    if app.config.get("TESTING"):
        tache()
    else:
        threading.Thread(target=tache, daemon=True).start()


def lien_frontend(chemin):
    """URL absolue d'une page du frontend (FRONTEND_URL dans le .env)."""
    base = (current_app.config.get("FRONTEND_URL") or "").rstrip("/")
    return f"{base}/{chemin.lstrip('/')}"


# Textes des deux types de lien.
_LIENS = {
    "invitation": {
        "sujet": "Activez votre compte Lumistock",
        "titre": "Bienvenue sur Lumistock",
        "introduction": "Un compte {role} a été créé pour vous{magasin}. "
                        "Cliquez sur le bouton ci-dessous pour choisir votre mot de passe et accéder à votre espace.",
        "bouton": "Choisir mon mot de passe",
    },
    "reset": {
        "sujet": "Réinitialisation de votre mot de passe - Lumistock",
        "titre": "Réinitialisation du mot de passe",
        "introduction": "Une réinitialisation de votre mot de passe a été demandée. "
                        "Cliquez sur le bouton ci-dessous pour en choisir un nouveau.",
        "bouton": "Choisir un nouveau mot de passe",
    },
}

ROLES_AFFICHES = {
    "staff": "staff", "admin": "administrateur", "manager": "manager",
    "gerant": "gérant", "livreur": "gérant",
}


def send_lien_compte_email(to_email, type_lien, token, prenom="", role="", magasin="", duree="24 heures"):
    """Envoie un lien d'invitation ou de réinitialisation (jamais de mot de passe).

    :param type_lien: "invitation" ou "reset"
    :return: True si l'email est parti, False sinon.
    """
    textes = _LIENS[type_lien]
    chemin = "definir-mot-de-passe" if type_lien == "invitation" else "resetpassword"
    lien = lien_frontend(f"{chemin}/{token}")
    introduction = textes["introduction"].format(
        role=ROLES_AFFICHES.get(role, role or "utilisateur"),
        magasin=f" dans le magasin « {magasin} »" if magasin else "",
    )
    valeurs = {
        "titre": textes["titre"], "prenom": prenom or "", "introduction": introduction,
        "lien": lien, "bouton": textes["bouton"], "duree": duree,
    }
    try:
        html_content = render_template("email/lien_compte_email.html", **valeurs)
    except Exception as e:
        current_app.logger.error("Erreur de rendu du template de lien : %s", e)
        html_content = None

    msg = MIMEMultipart("alternative")
    msg["Subject"] = textes["sujet"]
    msg["From"] = current_app.config["MAIL_SENDER"]
    msg["To"] = to_email
    plain_text = (
        f"Bonjour {prenom},\n\n{introduction}\n\n{lien}\n\n"
        f"Ce lien est personnel, ne fonctionne qu'une seule fois et expire dans {duree}.\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.\n\n"
        "L’équipe Lumistock."
    )
    msg.attach(MIMEText(plain_text, "plain"))
    if html_content:
        msg.attach(MIMEText(html_content, "html"))
    return _envoyer(msg, to_email)


# Email pour informer que l'inscription est en attente
def send_registration_pending_email(to_email, store_name):
    try:
        html_content = render_template(
            "email/registration_pending_email.html",
            store_name=store_name
        )
    except Exception as e:
        current_app.logger.error("Erreur de rendu du template d'attente : %s", e)
        html_content = f"<p>Votre magasin {store_name} est en attente de validation.</p>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Inscription en attente de validation - Lumistock"
    msg["From"] = current_app.config["MAIL_SENDER"]
    msg["To"] = to_email

    plain_text = f"""
    Bonjour 👋,

    Bonne nouvelle ! Votre magasin "{store_name}" a été créé avec succès sur notre plateforme.

    Votre compte est actuellement en attente d'analyse par notre équipe staff.
    Vous recevrez un nouvel e-mail dès que votre accès sera validé et activé.

    Merci de votre patience.

    L’équipe Lumistock.
    """

    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_content, "html"))
    return _envoyer(msg, to_email)
