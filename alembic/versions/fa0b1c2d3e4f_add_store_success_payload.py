"""Add Provider-owned payload from the latest successful store call."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fa0b1c2d3e4f"
down_revision: str | Sequence[str] | None = "f9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SYNC_TABLES = (
    "aweme_data_storer_syncs",
    "account_data_storer_syncs",
    "video_transcription_data_storer_syncs",
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = set(inspector.get_table_names())
    for table_name in _SYNC_TABLES:
        if table_name not in table_names:
            continue
        column_names = {column["name"] for column in inspector.get_columns(table_name)}
        if "success_payload_json" in column_names:
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(
                sa.Column(
                    "success_payload_json",
                    sa.JSON(),
                    nullable=False,
                    server_default="{}",
                )
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = set(inspector.get_table_names())
    for table_name in reversed(_SYNC_TABLES):
        if table_name not in table_names:
            continue
        column_names = {column["name"] for column in inspector.get_columns(table_name)}
        if "success_payload_json" not in column_names:
            continue
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column("success_payload_json")
