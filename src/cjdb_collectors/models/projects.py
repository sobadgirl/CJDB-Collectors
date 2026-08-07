from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from .base import TimestampMixin, enum_column, utc_now
from .enums import CommentKind, ProjectStatus


class Project(TimestampMixin, table=True):
    __tablename__ = "projects"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=255)
    description: str | None = None
    color: str | None = Field(default=None, max_length=32)
    sort_order: int = Field(default=0, index=True)
    status: ProjectStatus = Field(
        default=ProjectStatus.ACTIVE,
        sa_column=enum_column(ProjectStatus, index=True),
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )


class ProjectAweme(SQLModel, table=True):
    __tablename__ = "project_awemes"
    __table_args__ = (
        UniqueConstraint("project_id", "aweme_id", name="uq_project_awemes_pair"),
    )

    project_id: UUID = Field(foreign_key="projects.id", primary_key=True)
    aweme_id: UUID = Field(foreign_key="awemes.id", primary_key=True)
    collect_comments_enabled: bool = Field(default=False, index=True)
    comment_limit: int | None = Field(default=None, ge=1)
    download_video_enabled: bool = Field(default=False, index=True)
    transcribe_enabled: bool = Field(default=False, index=True)
    extra_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=utc_now),
    )


class ProjectAccount(SQLModel, table=True):
    __tablename__ = "project_accounts"
    __table_args__ = (
        UniqueConstraint("project_id", "account_id", name="uq_project_accounts_pair"),
    )

    project_id: UUID = Field(foreign_key="projects.id", primary_key=True)
    account_id: UUID = Field(foreign_key="accounts.id", primary_key=True)
    extra_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=utc_now),
    )


class ProjectProvider(SQLModel, table=True):
    __tablename__ = "project_providers"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "provider_id", name="uq_project_providers_pair"
        ),
    )

    project_id: UUID = Field(foreign_key="projects.id", primary_key=True)
    provider_id: UUID = Field(foreign_key="providers.id", primary_key=True)
    # Kept for migration compatibility. Project routing uses
    # ProjectProviderSelection exclusively.
    selected: bool = Field(default=True, index=True)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class ProjectProviderSelection(SQLModel, table=True):
    __tablename__ = "project_provider_selections"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "provider_type",
            "provider_id",
            name="uq_project_provider_selections_triplet",
        ),
        Index(
            "ix_project_provider_selections_lookup",
            "project_id",
            "provider_type",
        ),
    )

    project_id: UUID = Field(foreign_key="projects.id", primary_key=True)
    provider_type: str = Field(primary_key=True, max_length=120)
    provider_id: UUID = Field(foreign_key="providers.id", primary_key=True)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class ProjectVideoTranscription(SQLModel, table=True):
    __tablename__ = "project_video_transcriptions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "video_transcription_id",
            name="uq_project_video_transcriptions_pair",
        ),
    )

    project_id: UUID = Field(foreign_key="projects.id", primary_key=True)
    video_transcription_id: UUID = Field(
        foreign_key="video_transcriptions.id",
        primary_key=True,
    )
    extra_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=utc_now),
    )


class Comment(TimestampMixin, table=True):
    __tablename__ = "comments"
    __table_args__ = (
        UniqueConstraint(
            "aweme_id",
            "provider_namespace",
            "platform_comment_id",
            name="uq_comments_aweme_provider_external_id",
        ),
        Index("ix_comments_aweme_kind_order", "aweme_id", "kind", "sort_order"),
        Index("ix_comments_parent_order", "parent_comment_id", "sort_order"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    aweme_id: UUID = Field(foreign_key="awemes.id", index=True)
    parent_comment_id: UUID | None = Field(
        default=None,
        foreign_key="comments.id",
        index=True,
    )
    reply_to_comment_id: UUID | None = Field(
        default=None,
        foreign_key="comments.id",
        index=True,
    )
    provider_namespace: str = Field(max_length=64, index=True)
    platform_comment_id: str = Field(max_length=255, index=True)
    kind: CommentKind = Field(
        default=CommentKind.COMMENT,
        sa_column=enum_column(CommentKind, index=True),
    )
    author_id: str | None = Field(default=None, max_length=255)
    author_name: str | None = Field(default=None, max_length=500)
    author_avatar_url: str | None = None
    text: str | None = None
    like_count: int | None = Field(default=None, ge=0)
    reply_count: int | None = Field(default=None, ge=0)
    published_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    sort_order: int = Field(default=0, index=True)
    raw_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )


__all__ = [
    "Comment",
    "CommentKind",
    "Project",
    "ProjectAccount",
    "ProjectAweme",
    "ProjectProvider",
    "ProjectStatus",
    "ProjectVideoTranscription",
]
