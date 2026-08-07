"""add explicit account profile fields

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-08-04 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("signature", sa.Text(), nullable=True),
    sa.Column("location", sa.String(length=255), nullable=True),
    sa.Column("ip_location", sa.String(length=255), nullable=True),
    sa.Column("gender", sa.String(length=50), nullable=True),
    sa.Column("verified", sa.Boolean(), nullable=True),
    sa.Column("follower_count", sa.Integer(), nullable=True),
    sa.Column("following_count", sa.Integer(), nullable=True),
    sa.Column("work_count", sa.Integer(), nullable=True),
    sa.Column("like_count", sa.Integer(), nullable=True),
    sa.Column("collect_count", sa.Integer(), nullable=True),
    sa.Column("comment_count", sa.Integer(), nullable=True),
    sa.Column("share_count", sa.Integer(), nullable=True),
    sa.Column("total_favorited", sa.Integer(), nullable=True),
    sa.Column("extra_data_json", sa.JSON(), nullable=False, server_default="{}"),
)


def _to_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "accounts" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("accounts")}
    with op.batch_alter_table("accounts") as batch_op:
        for column in _COLUMNS:
            if column.name not in existing:
                batch_op.add_column(column.copy())

    if "profile_data_json" not in existing:
        return

    rows = connection.execute(
        sa.text("SELECT id, profile_data_json FROM accounts")
    ).mappings()
    for row in rows:
        try:
            profile = json.loads(row["profile_data_json"] or "{}")
        except (TypeError, ValueError):
            profile = {}
        if not isinstance(profile, dict):
            profile = {}
        updates = {
            "signature": profile.get("signature") or profile.get("desc"),
            "location": profile.get("location"),
            "ip_location": profile.get("ip_location"),
            "verified": profile.get("verified"),
            "follower_count": _to_int(
                profile.get("follower_count")
                or profile.get("fans_count")
                or profile.get("fans")
                or profile.get("followers")
            ),
            "following_count": _to_int(
                profile.get("following_count")
                or profile.get("follows")
                or profile.get("following")
            ),
            "work_count": _to_int(
                profile.get("work_count")
                or profile.get("aweme_count")
                or profile.get("note_count")
                or profile.get("video_count")
                or profile.get("article_count")
            ),
            "like_count": _to_int(
                profile.get("like_count")
                or profile.get("liked_count")
                or profile.get("liked")
            ),
            "collect_count": _to_int(
                profile.get("collect_count")
                or profile.get("collected_count")
                or profile.get("collected")
            ),
            "comment_count": _to_int(profile.get("comment_count")),
            "share_count": _to_int(profile.get("share_count")),
            "total_favorited": _to_int(profile.get("total_favorited")),
            "extra_data_json": json.dumps(profile, ensure_ascii=False),
            "id": row["id"],
        }
        connection.execute(
            sa.text(
                """
                UPDATE accounts
                SET signature = COALESCE(signature, :signature),
                    location = COALESCE(location, :location),
                    ip_location = COALESCE(ip_location, :ip_location),
                    verified = COALESCE(verified, :verified),
                    follower_count = COALESCE(follower_count, :follower_count),
                    following_count = COALESCE(following_count, :following_count),
                    work_count = COALESCE(work_count, :work_count),
                    like_count = COALESCE(like_count, :like_count),
                    collect_count = COALESCE(collect_count, :collect_count),
                    comment_count = COALESCE(comment_count, :comment_count),
                    share_count = COALESCE(share_count, :share_count),
                    total_favorited = COALESCE(total_favorited, :total_favorited),
                    extra_data_json = :extra_data_json
                WHERE id = :id
                """
            ),
            updates,
        )

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.alter_column("extra_data_json", server_default=None)


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "accounts" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("accounts")}
    with op.batch_alter_table("accounts") as batch_op:
        for column in reversed(_COLUMNS):
            if column.name in existing:
                batch_op.drop_column(column.name)
