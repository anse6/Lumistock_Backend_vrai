"""Compatibilité avec l'ancien nom de commande MongoDB."""

from database.migrate import migrate
from config import Config
from scripts.migrate_database import settings_from_config


if __name__ == "__main__":
    migrate(settings_from_config(Config))
    print("Schéma SQL Lumistock initialisé.")
