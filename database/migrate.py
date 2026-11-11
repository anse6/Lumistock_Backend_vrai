"""Initialisation et vérification du schéma SQL de Lumistock.

Le backend conserve volontairement les noms historiques de l'API, notamment
``_id`` et les références en ``ObjectId``. Cette migration ne renomme donc
aucune colonne métier et ne transforme pas ``_id`` en ``id``.
"""

import re
from datetime import datetime

from sqlalchemy import create_engine, inspect, text, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateColumn

from database.backend import database_url
from database.schema import metadata, migrations, tables


EXPECTED_TABLES = [*tables, "schema_migrations"]
MIGRATION_VERSION = "008_messages_contact"


def settings_from_config(config_class):
    """Construit un dictionnaire indépendant d'une instance Flask."""
    names = (
        "DATABASE_URL", "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER",
        "MYSQL_PASSWORD", "MYSQL_DATABASE", "AUTO_CREATE_DATABASE",
    )
    return {name: getattr(config_class, name, None) for name in names}


def _engine_options(url):
    options = {"pool_pre_ping": True, "pool_recycle": 1800}
    if url.get_backend_name() not in ("sqlite", "sqlite+pysqlite"):
        options["connect_args"] = {"connect_timeout": 5}
    return options


def ensure_mysql_database(url):
    """Crée la base MySQL/MariaDB si elle n'existe pas encore."""
    parsed = make_url(url)
    if parsed.get_backend_name() not in ("mysql", "mysql+pymysql", "mariadb"):
        return
    if not parsed.database:
        raise RuntimeError("MYSQL_DATABASE/DATABASE_URL doit contenir le nom de la base")
    if not re.fullmatch(r"[A-Za-z0-9_]+", parsed.database):
        raise RuntimeError("Nom de base invalide : seuls lettres, chiffres et _ sont autorisés")

    # ``URL.set(database=None)`` ne retire PAS le nom de la base (None veut
    # dire « inchangé » en SQLAlchemy 2) : la connexion visait alors la base
    # à créer et échouait (« Unknown database »).
    server_url = parsed._replace(database=None)
    engine = create_engine(server_url, **_engine_options(server_url))
    try:
        quoted_name = engine.dialect.identifier_preparer.quote(parsed.database)
        with engine.begin() as connection:
            connection.execute(text(
                f"CREATE DATABASE IF NOT EXISTS {quoted_name} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            ))
    finally:
        engine.dispose()


def migrate(config, create_database=True):
    """Crée le schéma et vérifie toutes les tables attendues.

    La fonction est idempotente : elle peut être relancée sans supprimer les
    données existantes.
    """
    url = make_url(database_url(config))
    if create_database and config.get("AUTO_CREATE_DATABASE", True):
        ensure_mysql_database(url)

    engine = create_engine(url, **_engine_options(url))
    try:
        metadata.create_all(engine, checkfirst=True)
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        missing = [name for name in EXPECTED_TABLES if name not in existing]
        if missing:
            raise RuntimeError(f"Tables manquantes après migration : {', '.join(missing)}")

        with engine.begin() as connection:
            # ``create_all`` ne modifie jamais une table existante. Cette étape
            # ajoute donc les colonnes déclarées après la première installation,
            # notamment le profil international des magasins, sans perte de données.
            for table_name, table in tables.items():
                existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
                missing_columns = [column for column in table.columns if column.name not in existing_columns]
                if not missing_columns:
                    continue
                quoted_table = engine.dialect.identifier_preparer.quote(table_name)
                for column in missing_columns:
                    column_ddl = str(CreateColumn(column).compile(dialect=engine.dialect))
                    connection.execute(text(f"ALTER TABLE {quoted_table} ADD COLUMN {column_ddl}"))

            # Index déclarés après la création de la table (create_all ne les
            # ajoute pas sur une table existante).
            for table_name, table in tables.items():
                existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
                for index in table.indexes:
                    if index.name not in existing_indexes:
                        index.create(connection)

            already_applied = connection.execute(
                select(migrations.c.version).where(migrations.c.version == MIGRATION_VERSION)
            ).first()
            if not already_applied:
                connection.execute(migrations.insert().values(
                    version=MIGRATION_VERSION,
                    applied_at=datetime.utcnow(),
                    details={
                        "tables": EXPECTED_TABLES,
                        "id_contract": "_id preserved; ObjectId values encoded losslessly",
                        "store_profile": "international company and contact fields added additively",
                        "comptabilite": "supplier invoices, bank statements and closed fiscal years",
                        "caisse": "cashier role and cash register sessions (sessions_caisse)",
                        "dettes_ouverture": "customer debts recorded before Lumistock (dettes_ouverture)",
                        "deux_facteurs": "optional two-factor authentication (TOTP secret, recovery codes)",
                        "messages_contact": "public contact form messages handled by staff",
                    },
                ))

        return {"database": url.database, "tables": EXPECTED_TABLES, "created": True}
    finally:
        engine.dispose()


def verify(config):
    """Vérifie le schéma sans le modifier."""
    url = make_url(database_url(config))
    engine = create_engine(url, **_engine_options(url))
    try:
        existing = set(inspect(engine).get_table_names())
        return {
            "database": url.database,
            "present": sorted(existing.intersection(EXPECTED_TABLES)),
            "missing": [name for name in EXPECTED_TABLES if name not in existing],
        }
    finally:
        engine.dispose()
