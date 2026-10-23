# config.py
import os
from dotenv import load_dotenv
from datetime import timedelta

load_dotenv()

def parse_seconds(value, default):
    try:
        if isinstance(value, timedelta):
            return int(value.total_seconds())
        elif isinstance(value, str):
            return int(value)
        elif isinstance(value, int):
            return value
        else:
            return default
    except Exception as e:
        print(f"[⚠️ CONFIG] Erreur de conversion JWT_EXPIRES : {e}")
        return default


def parse_bool(value, default=False):
    """Convertit une variable d'environnement en booléen de façon explicite."""
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

class Config:
    """Configuration principale de l'application."""
    SECRET_KEY = os.getenv("SECRET_KEY")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    # Expiration des tokens
    # Une seule durée pour tous les access tokens (connexion et rafraîchissement) :
    # 30 minutes, renouvelées automatiquement par le frontend tant que la
    # session (refresh token, 7 jours) est valide.
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(seconds=parse_seconds(os.getenv("JWT_ACCESS_TOKEN_EXPIRES"), 1800))
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(seconds=parse_seconds(os.getenv("JWT_REFRESH_TOKEN_EXPIRES"), 604800))
    JWT_TOKEN_LOCATION = ['headers']
    # Compatibilité avec les anciens clients qui utilisent une identité objet.
    JWT_VERIFY_SUB = False
    JWT_COOKIE_SECURE = False
    JWT_COOKIE_CSRF_PROTECT = False
    JWT_BLACKLIST_ENABLED = True
    JWT_BLACKLIST_TOKEN_CHECKS = ['access', 'refresh']

    # MySQL / MariaDB (XAMPP). DATABASE_URL optionally overrides these fields.
    DATABASE_URL = os.getenv("DATABASE_URL")
    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "Lumistock_bd")
    # Fuseau des magasins : heure « locale » des rapports, de la comptabilité et
    # du verrouillage des périodes. Les horodatages restent enregistrés en UTC.
    APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Africa/Douala")

    # Le schéma est créé automatiquement si la base existe déjà.
    # La création de la base MySQL elle-même est faite par le script de migration.
    AUTO_CREATE_SCHEMA = parse_bool(os.getenv("AUTO_CREATE_SCHEMA"), True)
    AUTO_CREATE_DATABASE = parse_bool(os.getenv("AUTO_CREATE_DATABASE"), True)

    # SMTP - pour l'envoi de mail
    EMAIL_HOST = os.getenv("EMAIL_HOST")
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
    EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
    MAIL_SENDER = os.getenv("MAIL_SENDER")

    # CORS
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")

    # Adresse du frontend, utilisée dans les liens envoyés par email
    # (invitation, réinitialisation du mot de passe).
    FRONTEND_URL = os.getenv("FRONTEND_URL", "https://lumistock.ansedev.com")

    # Messagerie clients : SMS (Orange, Twilio, ou "console" = simulation).
    SMS_PROVIDER = os.getenv("SMS_PROVIDER", "")
    SMS_SENDER_NAME = os.getenv("SMS_SENDER_NAME", "")  # nom d'expéditeur validé (11 caractères max)
    ORANGE_CLIENT_ID = os.getenv("ORANGE_CLIENT_ID")
    ORANGE_CLIENT_SECRET = os.getenv("ORANGE_CLIENT_SECRET")
    ORANGE_SENDER_NUMBER = os.getenv("ORANGE_SENDER_NUMBER", "+2370000")
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_FROM = os.getenv("TWILIO_FROM")
    TWILIO_MESSAGING_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID")
    # Planificateur intégré (envois programmés, relances automatiques). À
    # désactiver si `flask messagerie-planifier` est lancé par cron.
    MESSAGERIE_PLANIFICATEUR = parse_bool(os.getenv("MESSAGERIE_PLANIFICATEUR"), True)
    # SMS maximum par magasin et par mois (0 = illimité) : maîtrise des coûts.
    SMS_QUOTA_MENSUEL = int(os.getenv("SMS_QUOTA_MENSUEL", "0") or 0)

    # Nombre de proxys de confiance devant l'application (1 derrière nginx).
    TRUSTED_PROXIES = int(os.getenv("TRUSTED_PROXIES", "0") or 0)

    # Dossier pour les images uploadées
    UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads", "images")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 Mo max
    # Durée de vie du token de validation par email (ex: 1 jour)
    VALIDATION_TOKEN_EXPIRES = 86400  # secondes = 24 heures
    STAFF_IMAGE_FOLDER = os.path.join(os.getcwd(), "uploads", "staff_images")
