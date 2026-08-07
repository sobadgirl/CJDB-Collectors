from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from cjdb_collectors.settings import load_settings
from cjdb_collectors import models  # noqa: F401


config = context.config
if (
    config.config_file_name is not None
    and config.attributes.get("configure_logger", True)
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

runtime_settings = config.attributes.get("settings") or load_settings()
config.set_main_option(
    "sqlalchemy.url", f"sqlite:///{runtime_settings.app.database_path}"
)
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        # SQLAlchemy 2 starts a transaction for the PRAGMA. Commit it so
        # Alembic owns the migration transaction and persists version stamps.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
