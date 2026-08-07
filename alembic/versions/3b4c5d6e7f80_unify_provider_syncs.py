"""Unify provider sync relations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3b4c5d6e7f80"
down_revision: str | Sequence[str] | None = "2a3b4c5d6e7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COMMON_COLUMNS = (
    "created_at",
    "updated_at",
    "status",
    "enabled",
    "remote_url",
    "remote_attachment_json",
    "success_payload_json",
    "last_synced_hash",
    "last_synced_at",
    "attempt_count",
    "next_run_at",
    "started_at",
    "finished_at",
    "heartbeat_at",
    "run_token",
    "error_message",
    "id",
    "provider_id",
)

_LEGACY_TABLES = (
    ("aweme_provider_syncs", "aweme", "aweme_id"),
    ("account_provider_syncs", "account", "account_id"),
    (
        "video_transcription_provider_syncs",
        "video_transcription",
        "video_transcription_id",
    ),
)


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _create_provider_syncs() -> None:
    op.create_table(
        "provider_syncs",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("object_type", sa.String(length=32), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("remote_url", sa.String(), nullable=True),
        sa.Column("remote_attachment_json", sa.JSON(), nullable=False),
        sa.Column("success_payload_json", sa.JSON(), nullable=False),
        sa.Column("last_synced_hash", sa.String(length=128), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_token", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_id",
            "object_type",
            "object_id",
            name="uq_provider_syncs_target",
        ),
    )
    op.create_index("ix_provider_syncs_provider_id", "provider_syncs", ["provider_id"])
    op.create_index("ix_provider_syncs_object_id", "provider_syncs", ["object_id"])
    op.create_index("ix_provider_syncs_object_type", "provider_syncs", ["object_type"])
    op.create_index("ix_provider_syncs_status", "provider_syncs", ["status"])
    op.create_index("ix_provider_syncs_enabled", "provider_syncs", ["enabled"])
    op.create_index("ix_provider_syncs_run_token", "provider_syncs", ["run_token"])
    op.create_index(
        "ix_provider_syncs_schedule",
        "provider_syncs",
        ["status", "next_run_at", "enabled"],
    )
    op.create_index(
        "ix_provider_syncs_object",
        "provider_syncs",
        ["object_type", "object_id"],
    )


def _create_legacy_table(table: str, owner_table: str, owner_column: str) -> None:
    op.create_table(
        table,
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("remote_url", sa.String(), nullable=True),
        sa.Column("remote_attachment_json", sa.JSON(), nullable=False),
        sa.Column("success_payload_json", sa.JSON(), nullable=False),
        sa.Column("last_synced_hash", sa.String(length=128), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_token", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(owner_column, sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint([owner_column], [f"{owner_table}.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def upgrade() -> None:
    tables = _tables()
    if "provider_syncs" not in tables:
        _create_provider_syncs()
        tables.add("provider_syncs")

    insert_columns = ", ".join((*_COMMON_COLUMNS, "object_type", "object_id"))
    select_columns = ", ".join(_COMMON_COLUMNS)
    for table, object_type, owner_column in _LEGACY_TABLES:
        if table not in tables:
            continue
        op.execute(
            sa.text(
                f"""
                INSERT OR IGNORE INTO provider_syncs ({insert_columns})
                SELECT {select_columns}, :object_type, {owner_column}
                FROM {table}
                """
            ).bindparams(object_type=object_type)
        )
        op.drop_table(table)


def downgrade() -> None:
    tables = _tables()
    if "provider_syncs" not in tables:
        return

    for table, object_type, owner_column in _LEGACY_TABLES:
        if table not in tables:
            owner_table = (
                "awemes"
                if object_type == "aweme"
                else "accounts"
                if object_type == "account"
                else "video_transcriptions"
            )
            _create_legacy_table(table, owner_table, owner_column)
        insert_columns = ", ".join((*_COMMON_COLUMNS, owner_column))
        select_columns = ", ".join(_COMMON_COLUMNS)
        op.execute(
            sa.text(
                f"""
                INSERT INTO {table} ({insert_columns})
                SELECT {select_columns}, object_id
                FROM provider_syncs
                WHERE object_type = :object_type
                """
            ).bindparams(object_type=object_type)
        )

    op.drop_table("provider_syncs")
