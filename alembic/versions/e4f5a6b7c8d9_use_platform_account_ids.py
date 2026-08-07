"""use platform account ids on awemes

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-08-04 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    table_names = inspector.get_table_names()

    if "awemes" in table_names:
        aweme_columns = {column["name"] for column in inspector.get_columns("awemes")}
        aweme_indexes = {index["name"] for index in inspector.get_indexes("awemes")}
        if "platform_account_id" not in aweme_columns:
            with op.batch_alter_table("awemes") as batch_op:
                batch_op.add_column(
                    sa.Column("platform_account_id", sa.String(length=255))
                )
            aweme_columns.add("platform_account_id")
        if "ix_awemes_platform_account_id" not in aweme_indexes:
            with op.batch_alter_table("awemes") as batch_op:
                batch_op.create_index(
                    "ix_awemes_platform_account_id",
                    ["platform_account_id"],
                )

        if "account_id" in aweme_columns and "accounts" in table_names:
            connection.execute(
                sa.text(
                    """
                    UPDATE awemes
                    SET platform_account_id = (
                        SELECT accounts.platform_account_id
                        FROM accounts
                        WHERE accounts.id = awemes.account_id
                    )
                    WHERE platform_account_id IS NULL
                      AND account_id IS NOT NULL
                    """
                )
            )

        if "account_id" in aweme_columns:
            with op.batch_alter_table("awemes") as batch_op:
                if "ix_awemes_account_id" in aweme_indexes:
                    batch_op.drop_index("ix_awemes_account_id")
                batch_op.drop_column("account_id")


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    table_names = inspector.get_table_names()

    if "awemes" in table_names:
        aweme_columns = {column["name"] for column in inspector.get_columns("awemes")}
        aweme_indexes = {index["name"] for index in inspector.get_indexes("awemes")}
        if "account_id" not in aweme_columns:
            with op.batch_alter_table("awemes") as batch_op:
                batch_op.add_column(sa.Column("account_id", sa.String(length=32)))
        if "ix_awemes_account_id" not in aweme_indexes:
            with op.batch_alter_table("awemes") as batch_op:
                batch_op.create_index("ix_awemes_account_id", ["account_id"])
        if "platform_account_id" in aweme_columns:
            with op.batch_alter_table("awemes") as batch_op:
                if "ix_awemes_platform_account_id" in aweme_indexes:
                    batch_op.drop_index("ix_awemes_platform_account_id")
                batch_op.drop_column("platform_account_id")
