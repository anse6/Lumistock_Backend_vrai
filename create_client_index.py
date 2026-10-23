"""Ancienne commande conservée comme alias de migration SQL."""

from create_index import migrate, settings_from_config
from config import Config


if __name__ == "__main__":
    migrate(settings_from_config(Config))
    print("Schéma SQL Lumistock vérifié pour les clients.")
