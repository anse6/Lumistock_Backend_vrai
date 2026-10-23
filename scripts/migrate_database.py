"""Commande d'initialisation/vérification de la base Lumistock.

Usage:
    python -m scripts.migrate_database
    python -m scripts.migrate_database --check
"""

import argparse
import json

from config import Config
from database.migrate import migrate, settings_from_config, verify


def main():
    parser = argparse.ArgumentParser(description="Initialise le schéma SQL Lumistock")
    parser.add_argument("--check", action="store_true", help="vérifie sans modifier la base")
    args = parser.parse_args()
    config = settings_from_config(Config)
    result = verify(config) if args.check else migrate(config)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("missing"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
