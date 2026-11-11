"""SQL repositories exposing the small dictionary API used by existing models.

All persistence uses SQLAlchemy and MySQL. The dictionary API keeps HTTP
contracts and business services stable during the database transition.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from types import SimpleNamespace

from bson import ObjectId
from flask import current_app, g, request
from sqlalchemy import create_engine, select, and_, inspect
from sqlalchemy.engine import URL
from sqlalchemy.pool import StaticPool

from database.schema import metadata, tables
from database.codec import to_row, from_row
from database.query import (
    matches, get_path, set_path, unset_path, sort_documents, project, aggregate,
)


def sans_transaction(fn):
    """Exclut une route de la transaction automatique des requêtes d'écriture.

    Réservé aux routes dont certaines écritures doivent être conservées même
    quand la réponse est une erreur, par exemple le compteur de mots de passe
    erronés de la connexion (réponse 401). Chaque écriture y est alors
    validée immédiatement.
    """
    fn._sans_transaction = True
    return fn


def database_url(config):
    if config.get("DATABASE_URL"):
        return config["DATABASE_URL"]
    return URL.create(
        "mysql+pymysql", username=config.get("MYSQL_USER", "root"),
        password=config.get("MYSQL_PASSWORD", ""),
        host=config.get("MYSQL_HOST", "127.0.0.1"),
        port=int(config.get("MYSQL_PORT", 3306)),
        database=config.get("MYSQL_DATABASE", "Lumistock_bd"),
        query={"charset": "utf8mb4"},
    )


class SQLDatabase:
    def __init__(self):
        self.tables = Repositories(self)
        self._active = ContextVar(f"sql_connection_{id(self)}", default=None)

    def init_app(self, app):
        url = database_url(app.config)
        options = {"pool_pre_ping": True, "pool_recycle": 1800}
        if str(url).startswith("sqlite"):
            if not app.config.get("TESTING"):
                raise ValueError("SQLite is reserved for isolated tests; configure MySQL for the application")
            options.update(poolclass=StaticPool, connect_args={"check_same_thread": False})
        else:
            options["connect_args"] = {"connect_timeout": 5}
        engine = create_engine(url, **options)
        if engine.dialect.name not in ("mysql", "mariadb", "sqlite"):
            raise ValueError("Lumistock requires MySQL/MariaDB")
        app.extensions["lumistock_sql"] = engine

        # Idempotent : seules les tables absentes sont créées. La commande de
        # migration reste nécessaire pour créer une base MySQL inexistante.
        if app.config.get("AUTO_CREATE_SCHEMA", False):
            metadata.create_all(engine)

        @app.before_request
        def begin_write():
            vue = app.view_functions.get(request.endpoint)
            if getattr(vue, "_sans_transaction", False):
                return
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                context = self.transaction()
                context.__enter__()
                g.sql_transaction = context

        @app.after_request
        def finish_write(response):
            context = g.pop("sql_transaction", None)
            if context:
                if response.status_code >= 400:
                    error = RuntimeError("Request rejected; rollback database changes")
                    context.__exit__(type(error), error, None)
                else:
                    context.__exit__(None, None, None)
            return response

        @app.teardown_request
        def rollback_unfinished(error):
            context = g.pop("sql_transaction", None)
            if context:
                error = error or RuntimeError("Request did not finish")
                context.__exit__(type(error), error, error.__traceback__)

    @property
    def engine(self):
        return current_app.extensions["lumistock_sql"]

    @contextmanager
    def transaction(self):
        active = self._active.get()
        if active is not None:
            if active.engine is not self.engine:
                raise RuntimeError("Cannot reuse a transaction across applications")
            yield active
            return
        with self.engine.begin() as connection:
            token = self._active.set(connection)
            try:
                yield connection
            finally:
                self._active.reset(token)

    @contextmanager
    def connection(self, write=False):
        active = self._active.get()
        if active is not None:
            if active.engine is not self.engine:
                raise RuntimeError("Cannot reuse a connection across applications")
            yield active
        elif write:
            with self.transaction() as connection:
                yield connection
        else:
            with self.engine.connect() as connection:
                yield connection

    def create_all(self):
        """Crée les tables manquantes sans modifier le contrat API historique."""
        metadata.create_all(self.engine)

    def schema_tables(self):
        """Retourne toutes les tables attendues par le backend."""
        return [*tables, "schema_migrations"]


class Repositories:
    # Compatibilité avec les anciens appels Mongo/legacy du backend.
    # Le nom SQL canonique reste `stocks_livreur` (défini dans schema.py).
    ALIASES = {
        "stock_livreur": "stocks_livreur",
    }

    def __init__(self, database):
        self.database = database

    def __getitem__(self, name):
        name = self.ALIASES.get(name, name)
        if name not in tables:
            raise KeyError(f"Unknown SQL table: {name}")
        return Repository(self.database, tables[name])

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def list_collection_names(self):
        return [name for name in inspect(self.database.engine).get_table_names() if name in tables]

    def drop_collection(self, name):
        self[name].drop()


class Cursor:
    def __init__(self, repository, query, projection):
        self.repository, self.query, self.projection = repository, query, projection
        self.order, self.maximum, self.offset = [], 0, 0

    def sort(self, key, direction=1):
        self.order = [(key, direction)] if isinstance(key, str) else list(key)
        return self

    def limit(self, count):
        self.maximum = abs(count)
        return self

    def skip(self, count):
        self.offset = count
        return self

    def __iter__(self):
        documents = self.repository._read(self.query)
        sort_documents(documents, self.order)
        documents = documents[self.offset:]
        if self.maximum:
            documents = documents[:self.maximum]
        return iter(project(document, self.projection) for document in documents)

    def __getitem__(self, index):
        return list(self)[index]


class Repository:
    def __init__(self, database, table):
        self.database, self.table = database, table

    def _predicates(self, query):
        predicates = []
        # IDs are always stored in VARCHAR columns, even when the API uses ObjectId.
        # Other filters are rechecked in Python for legacy missing/null semantics.
        for field, value in query.items():
            if field not in self.table.c or not (field == "_id" or field.endswith("_id")):
                continue
            column = self.table.c[field]
            if isinstance(value, (ObjectId, str)):
                predicates.append(column == str(value))
            elif isinstance(value, dict) and set(value) == {"$in"} and all(isinstance(v, (ObjectId, str)) for v in value["$in"]):
                predicates.append(column.in_([str(v) for v in value["$in"]]))
        return predicates

    def _where(self, query):
        predicates = self._predicates(query)
        return and_(*predicates) if predicates else True

    def _read(self, query, connection=None, lock=False):
        """Documents correspondant à ``query``.

        Verrouillage (SELECT … FOR UPDATE) dans une transaction :
        - une recherche ciblée par identifiant (_id, magasin_id, client_id…)
          verrouille les lignes visées, comme avant : un lire-puis-écrire
          (stock, crédit client, règlements) reste protégé ;
        - une recherche sans identifiant (email, nom…) n'est plus verrouillée :
          avant, elle verrouillait TOUTE la table, tous magasins confondus ;
        - une modification sans identifiant (``lock=True``) cherche d'abord les
          lignes sans verrou, puis verrouille uniquement celles-ci et relit
          leur état actuel avant de les modifier.
        """
        predicates = self._predicates(query)
        statement = select(self.table)
        if predicates:
            statement = statement.where(and_(*predicates))
        en_transaction = self.database._active.get() is not None
        if predicates and (lock or en_transaction):
            statement = statement.with_for_update()

        def lire(connection):
            rows = connection.execute(statement).mappings().all()
            documents = [document for document in (from_row(self.table, row) for row in rows)
                         if matches(document, query)]
            if lock and not predicates and documents:
                cibles = select(self.table).where(
                    self.table.c._id.in_([str(document["_id"]) for document in documents])
                ).with_for_update()
                rows = connection.execute(cibles).mappings().all()
                documents = [document for document in (from_row(self.table, row) for row in rows)
                             if matches(document, query)]
            return documents

        if connection is None:
            with self.database.connection() as connection:
                return lire(connection)
        return lire(connection)

    def find(self, query=None, projection=None):
        return Cursor(self, query or {}, projection)

    def find_one(self, query=None, projection=None, sort=None):
        cursor = self.find(query, projection)
        if sort:
            cursor.sort(sort)
        return next(iter(cursor.limit(1)), None)

    def insert_one(self, document):
        document = deepcopy(document)
        document.setdefault("_id", ObjectId())
        if not ObjectId.is_valid(str(document["_id"])):
            raise ValueError("Identifiers must be 24 hexadecimal characters")
        with self.database.connection(write=True) as connection:
            connection.execute(self.table.insert().values(**to_row(self.table, document)))
        return SimpleNamespace(inserted_id=document["_id"], acknowledged=True)

    def insert_many(self, documents):
        with self.database.transaction():
            ids = [self.insert_one(doc).inserted_id for doc in documents]
        return SimpleNamespace(inserted_ids=ids, acknowledged=True)

    def count_documents(self, query):
        return len(self._read(query))

    def _updated(self, original, update, inserting=False):
        result = deepcopy(original)
        for op, changes in update.items():
            if op == "$setOnInsert" and not inserting:
                continue
            if op not in ("$set", "$setOnInsert", "$unset", "$inc", "$push"):
                raise NotImplementedError(f"Unsupported update: {op}")
            for field, value in changes.items():
                if field == "_id":
                    raise ValueError("Cannot change an identifier")
                if op in ("$set", "$setOnInsert"):
                    set_path(result, field, deepcopy(value))
                elif op == "$unset":
                    unset_path(result, field)
                elif op == "$inc":
                    set_path(result, field, get_path(result, field, 0) + value)
                elif op == "$push":
                    values = list(get_path(result, field, []))
                    values.extend(value["$each"] if isinstance(value, dict) and "$each" in value else [value])
                    set_path(result, field, values)
        return result

    def _update(self, query, update, upsert=False, many=False, return_document=False):
        with self.database.connection(write=True) as connection:
            documents = self._read(query, connection, lock=True)
            if not many:
                documents = documents[:1]
            modified, returned = 0, None
            for original in documents:
                updated = self._updated(original, update)
                if updated != original:
                    connection.execute(self.table.update().where(self.table.c._id == str(original["_id"])).values(**to_row(self.table, updated)))
                    modified += 1
                returned = updated if return_document else original
            upserted = None
            if not documents and upsert:
                seed = {k: v for k, v in query.items() if not k.startswith("$") and not isinstance(v, dict)}
                seed.setdefault("_id", ObjectId())
                updated = self._updated(seed, update, inserting=True)
                connection.execute(self.table.insert().values(**to_row(self.table, updated)))
                upserted = updated["_id"]
                returned = updated if return_document else None
        return SimpleNamespace(matched_count=len(documents), modified_count=modified, upserted_id=upserted, acknowledged=True), returned

    def update_one(self, query, update, upsert=False):
        return self._update(query, update, upsert=upsert)[0]

    def update_many(self, query, update, upsert=False):
        return self._update(query, update, upsert=upsert, many=True)[0]

    def find_one_and_update(self, query, update, return_document=False, upsert=False):
        return self._update(query, update, upsert=upsert, return_document=return_document)[1]

    def _delete(self, query, many=False):
        with self.database.connection(write=True) as connection:
            documents = self._read(query, connection, lock=True)
            if not many:
                documents = documents[:1]
            if documents:
                connection.execute(self.table.delete().where(self.table.c._id.in_([str(doc["_id"]) for doc in documents])))
        return documents

    def delete_one(self, query):
        return SimpleNamespace(deleted_count=len(self._delete(query)), acknowledged=True)

    def delete_many(self, query):
        return SimpleNamespace(deleted_count=len(self._delete(query, many=True)), acknowledged=True)

    def find_one_and_delete(self, query):
        documents = self._delete(query)
        return documents[0] if documents else None

    def aggregate(self, pipeline):
        return aggregate(self, pipeline)

    def drop(self):
        if not current_app.config.get("TESTING"):
            raise RuntimeError("Dropping tables is only permitted in isolated tests")
        self.delete_many({})
