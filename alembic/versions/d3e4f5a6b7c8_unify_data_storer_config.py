"""unify data storer config

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-08-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "data_storers" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("data_storers")}
    with op.batch_alter_table("data_storers") as batch_op:
        if "config_json" not in columns:
            batch_op.add_column(
                sa.Column(
                    "config_json",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
        for column_name in (
            "secret_ref",
            "connection_config_json",
            "container_config_json",
            "field_mapping_json",
            "attachment_policy_json",
            "conflict_policy",
        ):
            if column_name in columns:
                batch_op.drop_column(column_name)


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "data_storers" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("data_storers")}
    with op.batch_alter_table("data_storers") as batch_op:
        if "secret_ref" not in columns:
            batch_op.add_column(sa.Column("secret_ref", sa.String(length=255)))
        if "connection_config_json" not in columns:
            batch_op.add_column(
                sa.Column(
                    "connection_config_json",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
        if "container_config_json" not in columns:
            batch_op.add_column(
                sa.Column(
                    "container_config_json",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
        if "field_mapping_json" not in columns:
            batch_op.add_column(
                sa.Column(
                    "field_mapping_json",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
        if "attachment_policy_json" not in columns:
            batch_op.add_column(
                sa.Column(
                    "attachment_policy_json",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
        if "conflict_policy" not in columns:
            batch_op.add_column(
                sa.Column(
                    "conflict_policy",
                    sa.String(length=7),
                    nullable=False,
                    server_default="upsert",
                )
            )
        if "config_json" in columns:
            batch_op.drop_column("config_json")
