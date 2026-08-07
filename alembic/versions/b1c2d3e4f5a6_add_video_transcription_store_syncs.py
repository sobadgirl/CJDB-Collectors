"""add video transcription store syncs

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-08-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())

    if "video_transcription_data_storer_syncs" not in table_names:
        op.create_table(
            "video_transcription_data_storer_syncs",
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=13), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("remote_url", sa.String(), nullable=True),
            sa.Column("remote_attachment_json", sa.JSON(), nullable=False),
            sa.Column("last_synced_hash", sa.String(length=128), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("run_token", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.String(), nullable=True),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("video_transcription_id", sa.Uuid(), nullable=False),
            sa.Column("data_storer_id", sa.Uuid(), nullable=False),
            sa.ForeignKeyConstraint(["data_storer_id"], ["data_storers.id"]),
            sa.ForeignKeyConstraint(
                ["video_transcription_id"],
                ["video_transcriptions.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "video_transcription_id",
                "data_storer_id",
                name="uq_video_transcription_data_storer_syncs_pair",
            ),
        )
        op.create_index(
            "ix_video_transcription_sync_schedule",
            "video_transcription_data_storer_syncs",
            ["status", "next_run_at", "enabled"],
        )
        op.create_index(
            op.f("ix_video_transcription_data_storer_syncs_data_storer_id"),
            "video_transcription_data_storer_syncs",
            ["data_storer_id"],
        )
        op.create_index(
            op.f("ix_video_transcription_data_storer_syncs_run_token"),
            "video_transcription_data_storer_syncs",
            ["run_token"],
        )
        op.create_index(
            op.f("ix_video_transcription_data_storer_syncs_status"),
            "video_transcription_data_storer_syncs",
            ["status"],
        )
        op.create_index(
            op.f("ix_video_transcription_data_storer_syncs_video_transcription_id"),
            "video_transcription_data_storer_syncs",
            ["video_transcription_id"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())

    if "video_transcription_data_storer_syncs" in table_names:
        op.drop_index(
            op.f("ix_video_transcription_data_storer_syncs_video_transcription_id"),
            table_name="video_transcription_data_storer_syncs",
        )
        op.drop_index(
            op.f("ix_video_transcription_data_storer_syncs_status"),
            table_name="video_transcription_data_storer_syncs",
        )
        op.drop_index(
            op.f("ix_video_transcription_data_storer_syncs_run_token"),
            table_name="video_transcription_data_storer_syncs",
        )
        op.drop_index(
            op.f("ix_video_transcription_data_storer_syncs_data_storer_id"),
            table_name="video_transcription_data_storer_syncs",
        )
        op.drop_index(
            "ix_video_transcription_sync_schedule",
            table_name="video_transcription_data_storer_syncs",
        )
        op.drop_table("video_transcription_data_storer_syncs")
