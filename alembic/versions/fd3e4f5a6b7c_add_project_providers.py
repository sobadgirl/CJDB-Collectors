"""Bind reusable Provider instances to Projects."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fd3e4f5a6b7c"
down_revision: str | Sequence[str] | None = "fc2d3e4f5a6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    provider_columns = {
        column["name"] for column in inspector.get_columns("providers")
    }
    if "name" not in provider_columns:
        with op.batch_alter_table("providers") as batch:
            batch.add_column(
                sa.Column("name", sa.String(length=255), nullable=True)
            )
        op.execute(
            sa.text(
                "UPDATE providers SET name = namespace "
                "WHERE name IS NULL OR name = ''"
            )
        )
        with op.batch_alter_table("providers") as batch:
            batch.alter_column("name", existing_type=sa.String(255), nullable=False)
            batch.create_index("ix_providers_name", ["name"])

    inspector = sa.inspect(op.get_bind())
    if "project_providers" not in inspector.get_table_names():
        op.create_table(
            "project_providers",
            sa.Column("project_id", sa.Uuid(), nullable=False),
            sa.Column("provider_id", sa.Uuid(), nullable=False),
            sa.Column("selected", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
            sa.PrimaryKeyConstraint("project_id", "provider_id"),
            sa.UniqueConstraint(
                "project_id", "provider_id", name="uq_project_providers_pair"
            ),
        )
        op.create_index(
            "ix_project_providers_selected",
            "project_providers",
            ["selected"],
        )

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if {"project_data_storers", "data_storers"}.issubset(tables):
        op.execute(
            sa.text(
                """
                INSERT INTO project_providers (
                    project_id, provider_id, selected, created_at
                )
                SELECT
                    project_data_storers.project_id,
                    providers.id,
                    1,
                    MIN(project_data_storers.created_at)
                FROM project_data_storers
                JOIN data_storers
                  ON data_storers.id = project_data_storers.data_storer_id
                JOIN providers
                  ON providers.namespace = data_storers.type
                GROUP BY project_data_storers.project_id, providers.id
                """
            )
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "project_providers" in inspector.get_table_names():
        op.drop_table("project_providers")
    provider_columns = {
        column["name"] for column in inspector.get_columns("providers")
    }
    if "name" in provider_columns:
        with op.batch_alter_table("providers") as batch:
            batch.drop_index("ix_providers_name")
            batch.drop_column("name")
