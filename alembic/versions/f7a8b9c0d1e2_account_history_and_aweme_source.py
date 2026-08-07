"""Add account history progress and aweme data source."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    aweme_columns = _column_names("awemes")
    if aweme_columns:
        with op.batch_alter_table("awemes") as batch:
            if "data_source" not in aweme_columns:
                batch.add_column(
                    sa.Column(
                        "data_source",
                        sa.String(length=255),
                        nullable=False,
                        server_default="direct_provider",
                    )
                )
                batch.create_index("ix_awemes_data_source", ["data_source"])
            if "comment_history_progress_json" not in aweme_columns:
                batch.add_column(
                    sa.Column(
                        "comment_history_progress_json",
                        sa.JSON(),
                        nullable=False,
                        server_default="{}",
                    )
                )
            if "comment_latest_progress_json" not in aweme_columns:
                batch.add_column(
                    sa.Column(
                        "comment_latest_progress_json",
                        sa.JSON(),
                        nullable=False,
                        server_default="{}",
                    )
                )

    account_columns = _column_names("accounts")
    if account_columns:
        with op.batch_alter_table("accounts") as batch:
            if "history_status" not in account_columns:
                batch.add_column(
                    sa.Column(
                        "history_status",
                        sa.String(length=255),
                        nullable=False,
                        server_default="not_requested",
                    )
                )
                batch.create_index("ix_accounts_history_status", ["history_status"])
            if "history_cursor" not in account_columns:
                batch.add_column(sa.Column("history_cursor", sa.String(), nullable=True))
            if "history_attempt_count" not in account_columns:
                batch.add_column(
                    sa.Column(
                        "history_attempt_count",
                        sa.Integer(),
                        nullable=False,
                        server_default="0",
                    )
                )
            if "history_next_run_at" not in account_columns:
                batch.add_column(
                    sa.Column("history_next_run_at", sa.DateTime(), nullable=True)
                )
            if "history_started_at" not in account_columns:
                batch.add_column(
                    sa.Column("history_started_at", sa.DateTime(), nullable=True)
                )
            if "history_finished_at" not in account_columns:
                batch.add_column(
                    sa.Column("history_finished_at", sa.DateTime(), nullable=True)
                )
            if "history_heartbeat_at" not in account_columns:
                batch.add_column(
                    sa.Column("history_heartbeat_at", sa.DateTime(), nullable=True)
                )
            if "history_run_token" not in account_columns:
                batch.add_column(
                    sa.Column("history_run_token", sa.String(length=64), nullable=True)
                )
                batch.create_index("ix_accounts_history_run_token", ["history_run_token"])
            if "history_has_more" not in account_columns:
                batch.add_column(
                    sa.Column(
                        "history_has_more",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.true(),
                    )
                )
            if "history_fetched_count" not in account_columns:
                batch.add_column(
                    sa.Column(
                        "history_fetched_count",
                        sa.Integer(),
                        nullable=False,
                        server_default="0",
                    )
                )
            if "history_last_fetched_at" not in account_columns:
                batch.add_column(
                    sa.Column("history_last_fetched_at", sa.DateTime(), nullable=True)
                )
            if "history_error" not in account_columns:
                batch.add_column(sa.Column("history_error", sa.String(), nullable=True))
            if "history_backfill_progress_json" not in account_columns:
                batch.add_column(
                    sa.Column(
                        "history_backfill_progress_json",
                        sa.JSON(),
                        nullable=False,
                        server_default="{}",
                    )
                )
            if "history_latest_progress_json" not in account_columns:
                batch.add_column(
                    sa.Column(
                        "history_latest_progress_json",
                        sa.JSON(),
                        nullable=False,
                        server_default="{}",
                    )
                )
            if "history_request_json" not in account_columns:
                batch.add_column(
                    sa.Column(
                        "history_request_json",
                        sa.JSON(),
                        nullable=False,
                        server_default="{}",
                    )
                )


def downgrade() -> None:
    account_columns = _column_names("accounts")
    with op.batch_alter_table("accounts") as batch:
        if "history_error" in account_columns:
            batch.drop_column("history_error")
        if "history_request_json" in account_columns:
            batch.drop_column("history_request_json")
        if "history_latest_progress_json" in account_columns:
            batch.drop_column("history_latest_progress_json")
        if "history_backfill_progress_json" in account_columns:
            batch.drop_column("history_backfill_progress_json")
        if "history_last_fetched_at" in account_columns:
            batch.drop_column("history_last_fetched_at")
        if "history_fetched_count" in account_columns:
            batch.drop_column("history_fetched_count")
        if "history_has_more" in account_columns:
            batch.drop_column("history_has_more")
        if "history_cursor" in account_columns:
            batch.drop_column("history_cursor")
        if "history_run_token" in account_columns:
            batch.drop_index("ix_accounts_history_run_token")
            batch.drop_column("history_run_token")
        if "history_heartbeat_at" in account_columns:
            batch.drop_column("history_heartbeat_at")
        if "history_finished_at" in account_columns:
            batch.drop_column("history_finished_at")
        if "history_started_at" in account_columns:
            batch.drop_column("history_started_at")
        if "history_next_run_at" in account_columns:
            batch.drop_column("history_next_run_at")
        if "history_attempt_count" in account_columns:
            batch.drop_column("history_attempt_count")
        if "history_status" in account_columns:
            batch.drop_index("ix_accounts_history_status")
            batch.drop_column("history_status")

    aweme_columns = _column_names("awemes")
    with op.batch_alter_table("awemes") as batch:
        if "data_source" in aweme_columns:
            batch.drop_index("ix_awemes_data_source")
            batch.drop_column("data_source")
        if "comment_latest_progress_json" in aweme_columns:
            batch.drop_column("comment_latest_progress_json")
        if "comment_history_progress_json" in aweme_columns:
            batch.drop_column("comment_history_progress_json")
