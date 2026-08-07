"""Rename persisted Store config to Provider setup payload."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fb1c2d3e4f5a"
down_revision: str | Sequence[str] | None = "fa0b1c2d3e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "data_storers" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("data_storers")}
    if "config_json" in columns and "setup_payload_json" not in columns:
        with op.batch_alter_table("data_storers") as batch:
            batch.alter_column(
                "config_json",
                new_column_name="setup_payload_json",
                existing_type=sa.JSON(),
                existing_nullable=False,
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "data_storers" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("data_storers")}
    if "setup_payload_json" in columns and "config_json" not in columns:
        with op.batch_alter_table("data_storers") as batch:
            batch.alter_column(
                "setup_payload_json",
                new_column_name="config_json",
                existing_type=sa.JSON(),
                existing_nullable=False,
            )
