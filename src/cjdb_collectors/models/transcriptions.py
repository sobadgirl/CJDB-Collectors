from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, DateTime, Index
from pydantic import computed_field
from sqlmodel import Field

from .base import TimestampMixin, enum_column
from .enums import TaskStatus, display_task_status


class VideoTranscription(TimestampMixin, table=True):
    __tablename__ = "video_transcriptions"
    __table_args__ = (
        Index("ix_video_transcriptions_schedule", "status", "next_run_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    aweme_id: UUID | None = Field(default=None, foreign_key="awemes.id", index=True)
    source_url: str | None = None
    video_path: str | None = None
    video_sha256: str | None = Field(default=None, index=True, max_length=64)
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        sa_column=enum_column(TaskStatus, index=True),
    )
    progress: float = Field(default=0, ge=0, le=1)
    attempt_count: int = Field(default=0, ge=0)
    next_run_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    duration_seconds: float | None = Field(default=None, ge=0)
    heartbeat_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    run_token: str | None = Field(default=None, max_length=64, index=True)
    error_message: str | None = None
    text: str | None = None
    normalized_text: str | None = None
    text_summary: str | None = Field(default=None, max_length=1000)
    segments_json: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    is_current: bool = Field(default=True, index=True)

    @computed_field
    @property
    def status_display(self) -> str:
        return display_task_status(self.status)


__all__ = ["VideoTranscription"]
