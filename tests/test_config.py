# test/test_config.py
from datetime import timedelta 


class TestConfig:
    """
    Configuration de test pour l'application Flask.
    """
    TESTING = True
    DEBUG = True
    SECRET_KEY = "test-secret-key-distincte-du-jwt-32-caracteres-min"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=30)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=7)
    
    
    # Isolated SQL database; never connect tests to the XAMPP application DB.
    DATABASE_URL = "sqlite+pysqlite:///:memory:"
    JWT_SECRET_KEY = "test-jwt-secret-with-at-least-32-characters"
    
    WFT_CSRF_ENABLED = False  # Désactiver CSRF pour les tests
    MAIL_SUPPRESS_END = True  # Supprimer les emails envoyés pendant les tests
    
    UPLOAD_FOLDER = "/tmp/uploads/test"  # Dossier temporaire pour les fichiers téléchargés
    STAFF_IMAGE_FOLDER = "/tmp/uploads/test/staff"  # Dossier temporaire pour les images du personnel
    ALLOWED_ORIGINS = "*"
