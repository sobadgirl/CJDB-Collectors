"""add video transcription text summary

Revision ID: 6a7b8c9d0e1f
Revises: d43c851c442f
Create Date: 2026-08-01 22:40:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "6a7b8c9d0e1f"
down_revision = "d43c851c442f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "video_transcriptions" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("video_transcriptions")}
    if "text_summary" in columns:
        return
    with op.batch_alter_table("video_transcriptions") as batch_op:
        batch_op.add_column(sa.Column("text_summary", sa.String(length=1000)))


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "video_transcriptions" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("video_transcriptions")}
    if "text_summary" not in columns:
        return
    with op.batch_alter_table("video_transcriptions") as batch_op:
        batch_op.drop_column("text_summary")
