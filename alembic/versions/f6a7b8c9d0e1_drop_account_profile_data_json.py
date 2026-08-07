"""drop legacy account profile_data_json

Revision ID: f6a7b8c9d0e1
Revises: f5a6b7c8d9e0
Create Date: 2026-08-04 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "accounts" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("accounts")}
    if "profile_data_json" in columns:
        with op.batch_alter_table("accounts") as batch_op:
            batch_op.drop_column("profile_data_json")


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "accounts" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("accounts")}
    if "profile_data_json" not in columns:
        with op.batch_alter_table("accounts") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "profile_data_json",
                    sa.JSON(),
                    nullable=False,
                    server_default="{}",
                )
            )
