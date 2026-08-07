from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from .settings import PROJECT_ROOT, Settings, load_settings


def create_db_engine(database_path: str | Path | None = None) -> Engine:
    selected = Path(database_path or load_settings().app.database_path)
    is_memory = str(selected) == ":memory:"
    if not is_memory:
        selected = selected.expanduser().resolve()
        selected.parent.mkdir(parents=True, exist_ok=True)
    url = "sqlite:///:memory:" if is_memory else f"sqlite:///{selected}"
    connect_args = {"check_same_thread": False}
    kwargs = {
        "connect_args": connect_args,
        "pool_pre_ping": True,
    }
    if is_memory:
        kwargs["poolclass"] = StaticPool
    db_engine = create_engine(url, **kwargs)

    @event.listens_for(db_engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return db_engine


_default_engine: Engine | None = None


def get_default_engine() -> Engine:
    global _default_engine
    if _default_engine is None:
        _default_engine = create_db_engine()
    return _default_engine


def init_db(db_engine: Engine | None = None) -> None:
    """Create tables for local/bootstrap use.

    Production schema changes must still be applied through Alembic.
    """
    # Importing models registers all table metadata.
    from . import models as _models  # noqa: F401

    SQLModel.metadata.create_all(db_engine or get_default_engine())


def migrate_database(settings: Settings | None = None) -> None:
    """Upgrade the configured database to the current Alembic head."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    selected = settings or load_settings()
    alembic_config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.attributes["settings"] = selected
    alembic_config.attributes["configure_logger"] = False
    command.upgrade(alembic_config, "head")
    selected_engine = create_db_engine(selected.app.database_path)
    init_db(selected_engine)
    with selected_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS task_runners"))


def assert_database_current(settings: Settings | None = None) -> None:
    """Fail fast when the configured database is not at the Alembic head."""
    from alembic.config import Config as AlembicConfig
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    selected = settings or load_settings()
    alembic_config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(alembic_config)
    expected_heads = set(script.get_heads())
    engine = create_db_engine(selected.app.database_path)
    with engine.connect() as connection:
        current_heads = set(MigrationContext.configure(connection).get_current_heads())
    if current_heads != expected_heads:
        raise RuntimeError(
            "database schema is not current: "
            f"current={sorted(current_heads) or ['<none>']} "
            f"expected={sorted(expected_heads)}. Run `cjdb db migrate` first."
        )


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transaction-scoped SQLModel session."""
    with Session(get_default_engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


@contextmanager
def session_scope(db_engine: Engine | None = None) -> Iterator[Session]:
    """Context manager for CLI, Services, and WorkerTask callers."""
    with Session(db_engine or get_default_engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
