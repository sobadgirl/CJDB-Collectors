"""Drop remote_record_id from sync relations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2a3b4c5d6e7f"
down_revision: str | Sequence[str] | None = "1fd47b65df1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SYNC_TABLES = (
    "aweme_provider_syncs",
    "account_provider_syncs",
    "video_transcription_provider_syncs",
    "video_transcription_data_storer_syncs",
)


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table in inspector.get_table_names() and column in {
        item["name"] for item in inspector.get_columns(table)
    }


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    for table in _SYNC_TABLES:
        if _has_column(table, "remote_record_id"):
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column("remote_record_id")


def downgrade() -> None:
    for table in _SYNC_TABLES:
        if _has_table(table) and not _has_column(table, "remote_record_id"):
            with op.batch_alter_table(table) as batch_op:
                batch_op.add_column(
                    sa.Column("remote_record_id", sa.String(length=500), nullable=True)
                )
