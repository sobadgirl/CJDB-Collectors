"""rename groups to projects

Revision ID: 9d0e1f2a3b4c
Revises: 8c9d0e1f2a3b
Create Date: 2026-08-01 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "9d0e1f2a3b4c"
down_revision: str | None = "8c9d0e1f2a3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _rename_if_present("groups", "projects")
    _rename_if_present("group_awemes", "project_awemes")
    _rename_if_present("group_accounts", "project_accounts")
    _rename_if_present("group_data_storers", "project_data_storers")


def downgrade() -> None:
    _rename_if_present("project_data_storers", "group_data_storers")
    _rename_if_present("project_accounts", "group_accounts")
    _rename_if_present("project_awemes", "group_awemes")
    _rename_if_present("projects", "groups")


def _rename_if_present(old_name: str, new_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = set(inspector.get_table_names())
    if old_name in table_names and new_name not in table_names:
        op.rename_table(old_name, new_name)
