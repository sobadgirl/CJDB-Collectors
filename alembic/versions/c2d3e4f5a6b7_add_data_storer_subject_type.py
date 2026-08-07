"""add data storer subject type

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "data_storers" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("data_storers")}

    if "subject_type" not in columns:
        op.add_column(
            "data_storers",
            sa.Column(
                "subject_type",
                sa.String(length=64),
                nullable=False,
                server_default="aweme",
            ),
        )
        op.create_index(
            op.f("ix_data_storers_subject_type"),
            "data_storers",
            ["subject_type"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "data_storers" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("data_storers")}
    indexes = {index["name"] for index in inspector.get_indexes("data_storers")}

    if "ix_data_storers_subject_type" in indexes:
        op.drop_index(op.f("ix_data_storers_subject_type"), table_name="data_storers")
    if "subject_type" in columns:
        op.drop_column("data_storers", "subject_type")
