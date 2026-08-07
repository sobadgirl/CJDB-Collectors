"""merge store provider types

Revision ID: 1fd47b65df1a
Revises: ff5a6b7c8d9e
Create Date: 2026-08-05 20:38:35.447962
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1fd47b65df1a"
down_revision: str | Sequence[str] | None = "ff5a6b7c8d9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TYPE_MAPPINGS = {
    "store_douyin_aweme": "store_aweme",
    "store_xiaohongshu_aweme": "store_aweme",
    "store_wechat_channels_aweme": "store_aweme",
    "store_wechat_mp_aweme": "store_aweme",
    "store_douyin_account": "store_account",
    "store_xiaohongshu_account": "store_account",
    "store_wechat_channels_account": "store_account",
    "store_wechat_mp_account": "store_account",
}


def _selections() -> sa.TableClause:
    return sa.table(
        "project_provider_selections",
        sa.column("project_id", sa.Uuid()),
        sa.column("provider_type", sa.String()),
        sa.column("provider_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )


def upgrade() -> None:
    connection = op.get_bind()
    selections = _selections()
    for old_type, new_type in _TYPE_MAPPINGS.items():
        rows = connection.execute(
            sa.select(
                selections.c.project_id,
                selections.c.provider_id,
                selections.c.created_at,
            ).where(selections.c.provider_type == old_type)
        ).all()
        for row in rows:
            exists = connection.execute(
                sa.select(selections.c.provider_id).where(
                    selections.c.project_id == row.project_id,
                    selections.c.provider_type == new_type,
                    selections.c.provider_id == row.provider_id,
                )
            ).first()
            if exists is None:
                connection.execute(
                    selections.insert().values(
                        project_id=row.project_id,
                        provider_type=new_type,
                        provider_id=row.provider_id,
                        created_at=row.created_at,
                    )
                )
        connection.execute(
            selections.delete().where(selections.c.provider_type == old_type)
        )


def downgrade() -> None:
    connection = op.get_bind()
    selections = _selections()
    for new_type in ("store_aweme", "store_account"):
        rows = connection.execute(
            sa.select(
                selections.c.project_id,
                selections.c.provider_id,
                selections.c.created_at,
            ).where(selections.c.provider_type == new_type)
        ).all()
        old_types = [
            old_type
            for old_type, mapped_type in _TYPE_MAPPINGS.items()
            if mapped_type == new_type
        ]
        for row in rows:
            for old_type in old_types:
                connection.execute(
                    selections.insert().values(
                        project_id=row.project_id,
                        provider_type=old_type,
                        provider_id=row.provider_id,
                        created_at=row.created_at,
                    )
                )
        connection.execute(
            selections.delete().where(selections.c.provider_type == new_type)
        )
