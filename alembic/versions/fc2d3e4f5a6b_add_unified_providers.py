"""Add the unified Provider instance table."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fc2d3e4f5a6b"
down_revision: str | Sequence[str] | None = "fb1c2d3e4f5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "providers" in inspector.get_table_names():
        return
    op.create_table(
        "providers",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace", sa.String(length=120), nullable=False),
        sa.Column("setup_payload_json", sa.JSON(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("status_message", sa.String(), nullable=True),
        sa.Column("status_payload_json", sa.JSON(), nullable=False),
        sa.Column("setup_pid", sa.Integer(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_providers_namespace", "providers", ["namespace"])
    op.create_index("ix_providers_selected", "providers", ["selected"])
    op.create_index("ix_providers_status", "providers", ["status"])
    op.create_index("ix_providers_next_check_at", "providers", ["next_check_at"])
    op.create_index(
        "ix_providers_selected_namespace",
        "providers",
        ["selected", "namespace"],
    )
    inspector = sa.inspect(op.get_bind())
    if "data_storers" in inspector.get_table_names():
        data_storer_columns = {
            column["name"] for column in inspector.get_columns("data_storers")
        }
        if "setup_payload_json" in data_storer_columns:
            op.execute(
                sa.text(
                    """
                    INSERT INTO providers (
                        created_at,
                        updated_at,
                        id,
                        namespace,
                        setup_payload_json,
                        selected,
                        status,
                        status_message,
                        status_payload_json,
                        setup_pid,
                        last_checked_at,
                        next_check_at
                    )
                    SELECT
                        MIN(created_at),
                        MAX(updated_at),
                        MIN(id),
                        type,
                        setup_payload_json,
                        CASE WHEN status = 'active' THEN 1 ELSE 0 END,
                        CASE WHEN status = 'active' THEN 'ready' ELSE 'unavailable' END,
                        validation_error,
                        '{}',
                        NULL,
                        MAX(last_validated_at),
                        NULL
                    FROM data_storers
                    GROUP BY type
                    """
                )
            )
            with op.batch_alter_table("data_storers") as batch:
                batch.drop_column("setup_payload_json")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "providers" in inspector.get_table_names():
        if "data_storers" in inspector.get_table_names():
            columns = {
                column["name"]
                for column in inspector.get_columns("data_storers")
            }
            if "setup_payload_json" not in columns:
                with op.batch_alter_table("data_storers") as batch:
                    batch.add_column(
                        sa.Column(
                            "setup_payload_json",
                            sa.JSON(),
                            nullable=False,
                            server_default="{}",
                        )
                    )
                op.execute(
                    sa.text(
                        """
                        UPDATE data_storers
                        SET setup_payload_json = COALESCE(
                            (
                                SELECT providers.setup_payload_json
                                FROM providers
                                WHERE providers.namespace = data_storers.type
                                ORDER BY providers.selected DESC, providers.created_at
                                LIMIT 1
                            ),
                            '{}'
                        )
                        """
                    )
                )
        op.drop_table("providers")
