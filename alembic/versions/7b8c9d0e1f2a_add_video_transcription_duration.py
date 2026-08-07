"""add video transcription duration

Revision ID: 7b8c9d0e1f2a
Revises: 6a7b8c9d0e1f
Create Date: 2026-08-01 22:55:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "7b8c9d0e1f2a"
down_revision = "6a7b8c9d0e1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "video_transcriptions" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("video_transcriptions")}
    if "duration_seconds" in columns:
        return
    with op.batch_alter_table("video_transcriptions") as batch_op:
        batch_op.add_column(sa.Column("duration_seconds", sa.Float()))


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "video_transcriptions" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("video_transcriptions")}
    if "duration_seconds" not in columns:
        return
    with op.batch_alter_table("video_transcriptions") as batch_op:
        batch_op.drop_column("duration_seconds")
