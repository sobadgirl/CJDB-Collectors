"""Route each Project ProviderType to explicit Provider instances."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ff5a6b7c8d9e"
down_revision: str | Sequence[str] | None = "fe4f5a6b7c8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_provider_selections",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("provider_type", sa.String(length=120), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.PrimaryKeyConstraint("project_id", "provider_type", "provider_id"),
        sa.UniqueConstraint(
            "project_id",
            "provider_type",
            "provider_id",
            name="uq_project_provider_selections_triplet",
        ),
    )
    op.create_index(
        "ix_project_provider_selections_lookup",
        "project_provider_selections",
        ["project_id", "provider_type"],
    )
def downgrade() -> None:
    op.drop_index(
        "ix_project_provider_selections_lookup",
        table_name="project_provider_selections",
    )
    op.drop_table("project_provider_selections")
