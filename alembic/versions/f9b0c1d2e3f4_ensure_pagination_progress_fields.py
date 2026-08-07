"""Ensure pagination progress fields exist on upgraded databases."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "f8a9b0c1d2e3"
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
    pass
