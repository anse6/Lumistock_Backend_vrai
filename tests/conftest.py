# tests/conftest.py
import sys
import os


"""from routes.auth import auth_bp
from routes.user_routes import user_bp
from routes.staff_routes import staff_bp
"""
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from app import _fixer_fuseau, create_app
from config import Config
from extensions import db

# Même heure locale que l'application dès le chargement des tests (les dates
# « aujourd'hui » des modules de test sont calculées à l'import).
_fixer_fuseau(Config.APP_TIMEZONE)

from tests.test_config import TestConfig

@pytest.fixture
def app():
    """
    Fixture to create a Flask application instance for testing.
    """
    app = create_app(TestConfig)
    # Le limiteur de tentatives est en mémoire : chaque test repart de zéro.
    from utils.rate_limit import tout_reinitialiser
    tout_reinitialiser()
    with app.app_context():
        # Initialize the database
        db.create_all()
        yield app
        db.engine.dispose()
        
@pytest.fixture
def client(app):
    """
    Fixture to create a test client for the Flask application.
    """
    return app.test_client()

@pytest.fixture
def clear_db(app):
    """
    Fixture to clear the database before each test.
    """
    with app.app_context():
        collections = db.tables.list_collection_names()
        for collection in collections:
            db.tables.drop_collection(collection)
    
