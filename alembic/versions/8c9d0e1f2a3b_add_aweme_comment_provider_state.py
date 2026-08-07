"""add aweme comment provider state

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
Create Date: 2026-08-01 23:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "8c9d0e1f2a3b"
down_revision = "7b8c9d0e1f2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "awemes" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("awemes")}
    if "comment_provider_state_json" in columns:
        return
    with op.batch_alter_table("awemes") as batch_op:
        batch_op.add_column(
            sa.Column("comment_provider_state_json", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "awemes" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("awemes")}
    if "comment_provider_state_json" not in columns:
        return
    with op.batch_alter_table("awemes") as batch_op:
        batch_op.drop_column("comment_provider_state_json")
