from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from .config import PROJECT_ROOT, Settings, load_settings


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


engine = create_db_engine()


def init_db(db_engine: Engine | None = None) -> None:
    """Create tables for local/bootstrap use.

    Production schema changes must still be applied through Alembic.
    """
    # Importing models registers all table metadata.
    from . import models as _models  # noqa: F401

    SQLModel.metadata.create_all(db_engine or engine)


def migrate_database(settings: Settings | None = None) -> None:
    """Upgrade the configured database to the current Alembic head."""
    from alembic import command
    from alembic.config import Config

    selected = settings or load_settings()
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.attributes["settings"] = selected
    alembic_config.attributes["configure_logger"] = False
    command.upgrade(alembic_config, "head")
    selected_engine = create_db_engine(selected.app.database_path)
    init_db(selected_engine)
    with selected_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS task_runners"))


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transaction-scoped SQLModel session."""
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


@contextmanager
def session_scope(db_engine: Engine | None = None) -> Iterator[Session]:
    """Context manager for CLI, Services, and WorkerTask callers."""
    with Session(db_engine or engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
