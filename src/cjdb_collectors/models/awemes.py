from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint
from pydantic import computed_field
from sqlmodel import Field

from .base import TimestampMixin, enum_column
from .enums import (
    AwemeDataSource,
    ContentType,
    Platform,
    TaskStatus,
    display_content_type,
    display_platform,
    display_task_status,
)


class Aweme(TimestampMixin, table=True):
    __tablename__ = "awemes"
    __table_args__ = (
        UniqueConstraint(
            "platform", "platform_aweme_id", name="uq_awemes_platform_external_id"
        ),
        Index(
            "ix_awemes_collection_schedule",
            "collection_status",
            "collection_next_run_at",
        ),
        Index(
            "ix_awemes_media_schedule",
            "media_download_status",
            "media_download_next_run_at",
        ),
        Index(
            "ix_awemes_comment_schedule",
            "comment_collection_status",
            "comment_collection_next_run_at",
        ),
        Index(
            "ix_awemes_transcription_schedule",
            "video_transcription_status",
            "video_path",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    platform_account_id: str | None = Field(default=None, max_length=255, index=True)
    platform: Platform = Field(sa_column=enum_column(Platform, index=True))
    data_source: AwemeDataSource = Field(
        default=AwemeDataSource.DIRECT_PROVIDER,
        sa_column=enum_column(AwemeDataSource, index=True),
    )
    content_type: ContentType = Field(
        default=ContentType.UNKNOWN,
        sa_column=enum_column(ContentType, index=True),
    )
    platform_aweme_id: str | None = Field(default=None, max_length=255)
    aweme_url: str | None = None
    source_url: str
    title: str | None = Field(default=None, max_length=1000)
    description: str | None = None
    published_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    play_count: int | None = Field(default=None, ge=0)
    like_count: int | None = Field(default=None, ge=0)
    collect_count: int | None = Field(default=None, ge=0)
    share_count: int | None = Field(default=None, ge=0)
    comment_count: int | None = Field(default=None, ge=0)
    comments_json: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    comments_cursor: str | None = None
    comment_provider_state_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    comment_history_progress_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    comment_latest_progress_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    comments_collected_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    video_url: str | None = None
    video_path: str | None = None
    cover_url: str | None = None
    cover_path: str | None = None
    photos: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    photo_paths: list[Any] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    transcription_text: str | None = None
    transcription_updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    extra_data_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    collection_status: TaskStatus = Field(
        default=TaskStatus.NOT_REQUESTED,
        sa_column=enum_column(TaskStatus, index=True),
    )
    collection_attempt_count: int = Field(default=0, ge=0)
    collection_next_run_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    collection_started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    collection_finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    collection_heartbeat_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    collection_run_token: str | None = Field(default=None, max_length=64, index=True)
    collection_error: str | None = None
    media_download_status: TaskStatus = Field(
        default=TaskStatus.NOT_REQUESTED,
        sa_column=enum_column(TaskStatus, index=True),
    )
    media_download_attempt_count: int = Field(default=0, ge=0)
    media_download_next_run_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    media_download_started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    media_download_finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    media_download_heartbeat_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    media_download_run_token: str | None = Field(
        default=None, max_length=64, index=True
    )
    media_download_error: str | None = None
    comment_collection_status: TaskStatus = Field(
        default=TaskStatus.NOT_REQUESTED,
        sa_column=enum_column(TaskStatus, index=True),
    )
    comment_collection_attempt_count: int = Field(default=0, ge=0)
    comment_collection_next_run_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    comment_collection_started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    comment_collection_finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    comment_collection_heartbeat_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    comment_collection_run_token: str | None = Field(
        default=None, max_length=64, index=True
    )
    comment_collection_error: str | None = None
    video_transcription_status: TaskStatus = Field(
        default=TaskStatus.NOT_REQUESTED,
        sa_column=enum_column(TaskStatus, index=True),
    )
    last_collected_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )

    @computed_field
    @property
    def platform_display(self) -> str:
        return display_platform(self.platform)

    @computed_field
    @property
    def content_type_display(self) -> str:
        return display_content_type(self.content_type)

    @computed_field
    @property
    def collection_status_display(self) -> str:
        return display_task_status(self.collection_status)

    @computed_field
    @property
    def media_download_status_display(self) -> str:
        return display_task_status(self.media_download_status)

    @computed_field
    @property
    def comment_collection_status_display(self) -> str:
        return display_task_status(self.comment_collection_status)

    @computed_field
    @property
    def video_transcription_status_display(self) -> str:
        return display_task_status(self.video_transcription_status)


__all__ = ["Aweme", "AwemeDataSource", "ContentType"]
