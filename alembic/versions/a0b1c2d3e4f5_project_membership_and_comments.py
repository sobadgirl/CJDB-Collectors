"""project membership settings and normalized comments

Revision ID: a0b1c2d3e4f5
Revises: 9d0e1f2a3b4c
Create Date: 2026-08-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a0b1c2d3e4f5"
down_revision: str | None = "9d0e1f2a3b4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_batch_temp_table(table_name: str) -> None:
    op.execute(sa.text(f'DROP TABLE IF EXISTS "_alembic_tmp_{table_name}"'))


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())

    if "project_awemes" in table_names:
        _drop_batch_temp_table("project_awemes")
        columns = {column["name"] for column in inspector.get_columns("project_awemes")}
        with op.batch_alter_table("project_awemes") as batch_op:
            if "group_id" in columns and "project_id" not in columns:
                batch_op.alter_column("group_id", new_column_name="project_id")
            if "collect_comments_enabled" not in columns:
                batch_op.add_column(
                    sa.Column(
                        "collect_comments_enabled",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )
            if "comment_limit" not in columns:
                batch_op.add_column(sa.Column("comment_limit", sa.Integer(), nullable=True))
            if "download_video_enabled" not in columns:
                batch_op.add_column(
                    sa.Column(
                        "download_video_enabled",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )
            if "transcribe_enabled" not in columns:
                batch_op.add_column(
                    sa.Column(
                        "transcribe_enabled",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )
            if "extra_json" not in columns:
                batch_op.add_column(
                    sa.Column("extra_json", sa.JSON(), nullable=False, server_default="{}")
                )
            if "updated_at" not in columns:
                batch_op.add_column(
                    sa.Column(
                        "updated_at",
                        sa.DateTime(timezone=True),
                        nullable=False,
                        server_default=sa.text("CURRENT_TIMESTAMP"),
                    )
                )

    if "project_accounts" in table_names:
        _drop_batch_temp_table("project_accounts")
        columns = {column["name"] for column in inspector.get_columns("project_accounts")}
        with op.batch_alter_table("project_accounts") as batch_op:
            if "group_id" in columns and "project_id" not in columns:
                batch_op.alter_column("group_id", new_column_name="project_id")
            if "extra_json" not in columns:
                batch_op.add_column(
                    sa.Column("extra_json", sa.JSON(), nullable=False, server_default="{}")
                )
            if "updated_at" not in columns:
                batch_op.add_column(
                    sa.Column(
                        "updated_at",
                        sa.DateTime(timezone=True),
                        nullable=False,
                        server_default=sa.text("CURRENT_TIMESTAMP"),
                    )
                )

    if "project_data_storers" in table_names:
        _drop_batch_temp_table("project_data_storers")
        columns = {
            column["name"] for column in inspector.get_columns("project_data_storers")
        }
        if "group_id" in columns and "project_id" not in columns:
            with op.batch_alter_table("project_data_storers") as batch_op:
                batch_op.alter_column("group_id", new_column_name="project_id")

    if "project_video_transcriptions" not in table_names:
        op.create_table(
            "project_video_transcriptions",
            sa.Column("project_id", sa.Uuid(), nullable=False),
            sa.Column("video_transcription_id", sa.Uuid(), nullable=False),
            sa.Column("extra_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["video_transcription_id"], ["video_transcriptions.id"]),
            sa.PrimaryKeyConstraint("project_id", "video_transcription_id"),
            sa.UniqueConstraint(
                "project_id",
                "video_transcription_id",
                name="uq_project_video_transcriptions_pair",
            ),
        )

    if "comments" not in table_names:
        op.create_table(
            "comments",
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("aweme_id", sa.Uuid(), nullable=False),
            sa.Column("parent_comment_id", sa.Uuid(), nullable=True),
            sa.Column("reply_to_comment_id", sa.Uuid(), nullable=True),
            sa.Column("provider_namespace", sa.String(length=64), nullable=False),
            sa.Column("platform_comment_id", sa.String(length=255), nullable=False),
            sa.Column("kind", sa.String(length=7), nullable=False),
            sa.Column("author_id", sa.String(length=255), nullable=True),
            sa.Column("author_name", sa.String(length=500), nullable=True),
            sa.Column("author_avatar_url", sa.String(), nullable=True),
            sa.Column("text", sa.String(), nullable=True),
            sa.Column("like_count", sa.Integer(), nullable=True),
            sa.Column("reply_count", sa.Integer(), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("raw_json", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(["aweme_id"], ["awemes.id"]),
            sa.ForeignKeyConstraint(["parent_comment_id"], ["comments.id"]),
            sa.ForeignKeyConstraint(["reply_to_comment_id"], ["comments.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "aweme_id",
                "provider_namespace",
                "platform_comment_id",
                name="uq_comments_aweme_provider_external_id",
            ),
        )
        op.create_index(
            "ix_comments_aweme_kind_order",
            "comments",
            ["aweme_id", "kind", "sort_order"],
        )
        op.create_index(
            "ix_comments_parent_order",
            "comments",
            ["parent_comment_id", "sort_order"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())

    if "comments" in table_names:
        op.drop_index("ix_comments_parent_order", table_name="comments")
        op.drop_index("ix_comments_aweme_kind_order", table_name="comments")
        op.drop_table("comments")
    if "project_video_transcriptions" in table_names:
        op.drop_table("project_video_transcriptions")
