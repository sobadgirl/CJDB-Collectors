from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint
from pydantic import computed_field
from sqlmodel import Field

from .base import TimestampMixin, enum_column
from .display import (
    display_count,
    display_gender,
    display_location,
    display_registered_at,
)
from .enums import Platform, TaskStatus, display_platform, display_task_status


class Account(TimestampMixin, table=True):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint(
            "platform", "platform_account_id", name="uq_accounts_platform_external_id"
        ),
        Index(
            "ix_accounts_collection_schedule",
            "collection_status",
            "collection_next_run_at",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    platform: Platform = Field(sa_column=enum_column(Platform, index=True))
    # ID from the profile URL: Douyin uses sec_uid, Xiaohongshu uses userid.
    platform_account_id: str | None = Field(default=None, max_length=255)
    profile_url: str
    display_name: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = None
    avatar_path: str | None = None
    signature: str | None = None
    location: str | None = Field(default=None, max_length=255)
    ip_location: str | None = Field(default=None, max_length=255)
    gender: str | None = Field(default=None, max_length=50)
    verified: bool | None = None
    follower_count: int | None = Field(default=None, ge=0)
    following_count: int | None = Field(default=None, ge=0)
    work_count: int | None = Field(default=None, ge=0)
    like_count: int | None = Field(default=None, ge=0)
    collect_count: int | None = Field(default=None, ge=0)
    comment_count: int | None = Field(default=None, ge=0)
    share_count: int | None = Field(default=None, ge=0)
    total_favorited: int | None = Field(default=None, ge=0)
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
    last_collected_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    history_status: TaskStatus = Field(
        default=TaskStatus.NOT_REQUESTED,
        sa_column=enum_column(TaskStatus, index=True),
    )
    history_attempt_count: int = Field(default=0, ge=0)
    history_next_run_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    history_started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    history_finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    history_heartbeat_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    history_run_token: str | None = Field(default=None, max_length=64, index=True)
    history_cursor: str | None = None
    history_has_more: bool = True
    history_fetched_count: int = Field(default=0, ge=0)
    history_last_fetched_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    history_error: str | None = None
    history_backfill_progress_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    history_latest_progress_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    history_request_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
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
    def collection_status_display(self) -> str:
        return display_task_status(self.collection_status)

    @computed_field
    @property
    def history_status_display(self) -> str:
        return display_task_status(self.history_status)

    @computed_field
    @property
    def follower_count_display(self) -> str:
        return display_count(self.follower_count)

    @computed_field
    @property
    def following_count_display(self) -> str:
        return display_count(self.following_count)

    @computed_field
    @property
    def location_display(self) -> str:
        return display_location(self.location, self.ip_location)

    @computed_field
    @property
    def gender_display(self) -> str:
        return display_gender(self.gender)

    @computed_field
    @property
    def registered_at_display(self) -> str:
        return display_registered_at(self.extra_data_json)


__all__ = ["Account"]
