"""Use Provider IDs for project bindings and synchronization."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fe4f5a6b7c8d"
down_revision: str | Sequence[str] | None = "fd3e4f5a6b7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMON_SYNC_COLUMNS = (
    "created_at, updated_at, status, enabled, remote_url, "
    "remote_attachment_json, success_payload_json, last_synced_hash, "
    "last_synced_at, attempt_count, next_run_at, started_at, finished_at, "
    "heartbeat_at, run_token, error_message, id"
)


def _sync_columns() -> list[sa.Column]:
    return [
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
    ]


def _create_sync_table(
    table: str,
    owner_table: str,
    owner_column: str,
) -> None:
    op.create_table(
        table,
        *_sync_columns(),
        sa.Column(owner_column, sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint([owner_column], [f"{owner_table}.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            owner_column,
            "provider_id",
            name=f"uq_{table}_pair",
        ),
    )
    op.create_index(f"ix_{table}_{owner_column}", table, [owner_column])
    op.create_index(f"ix_{table}_provider_id", table, ["provider_id"])
    op.create_index(f"ix_{table}_status", table, ["status"])
    op.create_index(f"ix_{table}_run_token", table, ["run_token"])
    op.create_index(
        f"ix_{table}_schedule",
        table,
        ["status", "next_run_at", "enabled"],
    )


def _copy_sync(old: str, new: str, owner_column: str) -> None:
    columns = f"{_COMMON_SYNC_COLUMNS}, {owner_column}"
    op.execute(
        sa.text(
            f"INSERT INTO {new} ({columns}, provider_id) "
            f"SELECT {columns}, data_storer_id FROM {old}"
        )
    )


def _create_legacy_sync_table(
    table: str,
    owner_table: str,
    owner_column: str,
) -> None:
    op.create_table(
        table,
        *_sync_columns(),
        sa.Column(owner_column, sa.Uuid(), nullable=False),
        sa.Column("data_storer_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint([owner_column], [f"{owner_table}.id"]),
        sa.ForeignKeyConstraint(["data_storer_id"], ["data_storers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            owner_column,
            "data_storer_id",
            name=f"uq_{table}_pair",
        ),
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "data_storers" in tables:
        op.execute(
            sa.text(
                """
                UPDATE providers
                SET name = COALESCE(
                    (SELECT data_storers.name FROM data_storers
                     WHERE data_storers.id = providers.id),
                    providers.name
                )
                """
            )
        )
        op.execute(
            sa.text(
                """
                INSERT INTO providers (
                    created_at, updated_at, id, namespace, name,
                    setup_payload_json, selected, status, status_message,
                    status_payload_json, setup_pid, last_checked_at, next_check_at
                )
                SELECT
                    data_storers.created_at,
                    data_storers.updated_at,
                    data_storers.id,
                    data_storers.type,
                    data_storers.name,
                    COALESCE(
                        (SELECT source.setup_payload_json FROM providers AS source
                         WHERE source.namespace = data_storers.type
                         ORDER BY source.created_at LIMIT 1),
                        '{}'
                    ),
                    CASE WHEN data_storers.status = 'active' THEN 1 ELSE 0 END,
                    CASE WHEN data_storers.status = 'active' THEN 'ready'
                         ELSE 'unavailable' END,
                    data_storers.validation_error,
                    '{}',
                    NULL,
                    data_storers.last_validated_at,
                    NULL
                FROM data_storers
                WHERE NOT EXISTS (
                    SELECT 1 FROM providers WHERE providers.id = data_storers.id
                )
                """
            )
        )

    if {"project_data_storers", "data_storers"}.issubset(tables):
        op.execute(sa.text("DELETE FROM project_providers"))
        op.execute(
            sa.text(
                """
                INSERT INTO project_providers (
                    project_id, provider_id, selected, created_at
                )
                SELECT project_id, data_storer_id, 1, created_at
                FROM project_data_storers
                """
            )
        )

    sync_tables = (
        (
            "aweme_data_storer_syncs",
            "aweme_provider_syncs",
            "awemes",
            "aweme_id",
        ),
        (
            "account_data_storer_syncs",
            "account_provider_syncs",
            "accounts",
            "account_id",
        ),
        (
            "video_transcription_data_storer_syncs",
            "video_transcription_provider_syncs",
            "video_transcriptions",
            "video_transcription_id",
        ),
    )
    for old, new, owner_table, owner_column in sync_tables:
        if new not in tables:
            _create_sync_table(new, owner_table, owner_column)
        if old in tables:
            _copy_sync(old, new, owner_column)
            op.drop_table(old)

    for table in ("default_data_storers", "project_data_storers", "data_storers"):
        if table in tables:
            op.drop_table(table)


def downgrade() -> None:
    op.create_table(
        "data_storers",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_error", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO data_storers (
                created_at, updated_at, id, name, type, subject_type,
                status, last_validated_at, validation_error
            )
            SELECT DISTINCT
                providers.created_at,
                providers.updated_at,
                providers.id,
                providers.name,
                providers.namespace,
                'aweme',
                CASE WHEN providers.status = 'ready' THEN 'active'
                     ELSE 'needs_attention' END,
                providers.last_checked_at,
                providers.status_message
            FROM providers
            WHERE providers.id IN (
                SELECT provider_id FROM project_providers
                UNION SELECT provider_id FROM aweme_provider_syncs
                UNION SELECT provider_id FROM account_provider_syncs
                UNION SELECT provider_id FROM video_transcription_provider_syncs
            )
            """
        )
    )
    op.create_table(
        "default_data_storers",
        sa.Column("data_storer_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["data_storer_id"], ["data_storers.id"]),
        sa.PrimaryKeyConstraint("data_storer_id"),
    )
    op.create_table(
        "project_data_storers",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("data_storer_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["data_storer_id"], ["data_storers.id"]),
        sa.PrimaryKeyConstraint("project_id", "data_storer_id"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO project_data_storers (
                project_id, data_storer_id, created_at
            )
            SELECT project_id, provider_id, created_at FROM project_providers
            """
        )
    )

    sync_tables = (
        (
            "aweme_provider_syncs",
            "aweme_data_storer_syncs",
            "awemes",
            "aweme_id",
        ),
        (
            "account_provider_syncs",
            "account_data_storer_syncs",
            "accounts",
            "account_id",
        ),
        (
            "video_transcription_provider_syncs",
            "video_transcription_data_storer_syncs",
            "video_transcriptions",
            "video_transcription_id",
        ),
    )
    for new, old, owner_table, owner_column in sync_tables:
        _create_legacy_sync_table(old, owner_table, owner_column)
        columns = f"{_COMMON_SYNC_COLUMNS}, {owner_column}"
        op.execute(
            sa.text(
                f"INSERT INTO {old} ({columns}, data_storer_id) "
                f"SELECT {columns}, provider_id FROM {new}"
            )
        )
        op.drop_table(new)
