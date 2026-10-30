"""Transactional SQLite development store, migrated using Alembic."""

from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, event, select

from backend.app.gate_state import connection_context

metadata = MetaData()
records = Table(
    "records",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("kind", String(40), nullable=False, index=True),
    Column("owner_id", String(100), nullable=False, index=True),
    Column("domain", String(20), nullable=False),
    Column("body", JSON, nullable=False),
    Column("version", Integer, nullable=False, default=1),
)
replays = Table(
    "idempotency",
    metadata,
    Column("scope", String(400), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("body", JSON, nullable=False),
)


class Store:
    def __init__(self, settings):
        self.settings = settings
        path = settings.path("db/darla.sqlite3")
        self.engine = create_engine(
            "sqlite:///" + path.as_posix(), connect_args={"check_same_thread": False, "timeout": 30}
        )

        @event.listens_for(self.engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")

        cfg = Config()
        cfg.set_main_option("script_location", str(settings.root / "backend/migrations"))
        with self.engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")

    @contextmanager
    def transaction(self):
        parent = connection_context.get()
        if parent is not None and parent.engine is self.engine:
            with parent.begin_nested():
                yield parent
            return
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            token = connection_context.set(connection)
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection_context.reset(token)

    def ready(self):
        with self.engine.connect() as connection:
            return connection.execute(select(records.c.id).limit(1)).fetchall() is not None
