"""initial schema

Revision ID: d43c851c442f
Revises:
Create Date: 2026-07-24 00:16:37.606277
"""

from __future__ import annotations


revision = "d43c851c442f"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Schema bootstrap is handled by SQLModel create_all in migrate_database."""


def downgrade() -> None:
    pass
