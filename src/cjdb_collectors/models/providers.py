from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, String
from sqlmodel import Field

from .base import TimestampMixin


class Provider(TimestampMixin, table=True):
    __tablename__ = "providers"
    __table_args__ = (
        Index("ix_providers_selected_namespace", "selected", "namespace"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    namespace: str = Field(index=True, max_length=120)
    name: str = Field(default="", index=True, max_length=255)
    setup_payload_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    selected: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, index=True),
    )
    status: str = Field(
        default="unconfigured",
        sa_column=Column(String(64), nullable=False, index=True),
    )
    status_message: str | None = None
    status_payload_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    setup_pid: int | None = None
    last_checked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    next_check_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )


__all__ = ["Provider"]
