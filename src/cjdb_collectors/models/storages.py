from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Index, UniqueConstraint
from sqlmodel import Field

from .base import TimestampMixin, enum_type
from .enums import SyncObjectType, TaskStatus


class ProviderSync(TimestampMixin, table=True):
    __tablename__ = "provider_syncs"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "object_type",
            "object_id",
            name="uq_provider_syncs_target",
        ),
        Index("ix_provider_syncs_schedule", "status", "next_run_at", "enabled"),
        Index("ix_provider_syncs_object", "object_type", "object_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    provider_id: UUID = Field(foreign_key="providers.id", index=True)
    object_type: SyncObjectType = Field(
        sa_type=enum_type(SyncObjectType),
        nullable=False,
        index=True,
    )
    object_id: UUID = Field(index=True)
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        sa_type=enum_type(TaskStatus),
        nullable=False,
        index=True,
    )
    enabled: bool = Field(default=True, index=True)
    remote_url: str | None = None
    remote_attachment_json: dict[str, Any] = Field(
        default_factory=dict, sa_type=JSON, nullable=False
    )
    success_payload_json: dict[str, Any] = Field(
        default_factory=dict, sa_type=JSON, nullable=False
    )
    last_synced_hash: str | None = Field(default=None, max_length=128)
    last_synced_at: datetime | None = Field(default=None)
    attempt_count: int = Field(default=0, ge=0)
    next_run_at: datetime | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)
    heartbeat_at: datetime | None = Field(default=None)
    run_token: str | None = Field(default=None, max_length=64, index=True)
    error_message: str | None = None


__all__ = ["ProviderSync"]
