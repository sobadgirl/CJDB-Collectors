from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enum_type(enum_class: type[Enum]) -> SAEnum:
    return SAEnum(
        enum_class,
        values_callable=lambda values: [item.value for item in values],
        native_enum=False,
        validate_strings=True,
    )


def enum_column(enum_class: type[Enum], *, index: bool = False) -> Column[Any]:
    return Column(
        enum_type(enum_class),
        nullable=False,
        index=index,
    )


class TaskStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ContentType(str, Enum):
    UNKNOWN = "unknown"
    VIDEO = "video"
    IMAGE = "image"
    ARTICLE = "article"
    LIVE = "live"


class Platform(StrEnum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    WECHAT_MP = "wechat_mp"
    WECHAT_CHANNELS = "wechat_channels"


class GroupStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class DataStorerStatus(str, Enum):
    ACTIVE = "active"
    NEEDS_ATTENTION = "needs_attention"
    DISABLED = "disabled"


class ConflictPolicy(str, Enum):
    UPSERT = "upsert"
    SKIP = "skip"
    OVERWRITE = "overwrite"


class WorkerTaskType(str, Enum):
    DATA_COLLECT = "data_collect"
    MEDIA_DOWNLOAD = "media_download"
    VIDEO_TRANSCRIPTION = "video_transcription"
    COMMENT_COLLECT = "comment_collect"
    DATA_SYNC = "data_sync"


class WorkerSubject(str, Enum):
    ACCOUNT = "account"
    AWEME = "aweme"
    VIDEO_TRANSCRIPTION = "video_transcription"
    AWEME_SYNC = "aweme_sync"
    ACCOUNT_SYNC = "account_sync"


class WorkerTaskStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    TIMEOUT = "timeout"


class TimestampMixin(SQLModel):
    # Mixins must let SQLModel create a fresh Column for every concrete table.
    # Reusing a ``sa_column=Column(...)`` instance across subclasses makes
    # SQLAlchemy attach the same Column object to multiple tables.
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        sa_column_kwargs={"onupdate": utc_now},
    )


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
    platform_account_id: str | None = Field(default=None, max_length=255)
    profile_url: str
    display_name: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = None
    avatar_path: str | None = None
    profile_data_json: dict[str, Any] = Field(
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
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
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
    account_id: UUID | None = Field(default=None, foreign_key="accounts.id", index=True)
    platform: Platform = Field(sa_column=enum_column(Platform, index=True))
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
    photo_paths: list[str] = Field(
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


class Group(TimestampMixin, table=True):
    __tablename__ = "groups"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=255)
    description: str | None = None
    color: str | None = Field(default=None, max_length=32)
    sort_order: int = Field(default=0, index=True)
    status: GroupStatus = Field(
        default=GroupStatus.ACTIVE,
        sa_column=enum_column(GroupStatus, index=True),
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )


class DataStorer(TimestampMixin, table=True):
    __tablename__ = "data_storers"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=255)
    type: str = Field(default="notion", index=True, max_length=64)
    secret_ref: str | None = Field(default=None, max_length=255)
    connection_config_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    container_config_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    field_mapping_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    attachment_policy_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    conflict_policy: ConflictPolicy = Field(
        default=ConflictPolicy.UPSERT,
        sa_column=enum_column(ConflictPolicy),
    )
    status: DataStorerStatus = Field(
        default=DataStorerStatus.ACTIVE,
        sa_column=enum_column(DataStorerStatus, index=True),
    )
    last_validated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    validation_error: str | None = None


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
    heartbeat_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    run_token: str | None = Field(default=None, max_length=64, index=True)
    error_message: str | None = None
    text: str | None = None
    normalized_text: str | None = None
    segments_json: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    is_current: bool = Field(default=True, index=True)


class GroupAweme(SQLModel, table=True):
    __tablename__ = "group_awemes"
    __table_args__ = (
        UniqueConstraint("group_id", "aweme_id", name="uq_group_awemes_pair"),
    )

    group_id: UUID = Field(foreign_key="groups.id", primary_key=True)
    aweme_id: UUID = Field(foreign_key="awemes.id", primary_key=True)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class GroupAccount(SQLModel, table=True):
    __tablename__ = "group_accounts"
    __table_args__ = (
        UniqueConstraint("group_id", "account_id", name="uq_group_accounts_pair"),
    )

    group_id: UUID = Field(foreign_key="groups.id", primary_key=True)
    account_id: UUID = Field(foreign_key="accounts.id", primary_key=True)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class GroupDataStorer(SQLModel, table=True):
    __tablename__ = "group_data_storers"
    __table_args__ = (
        UniqueConstraint(
            "group_id", "data_storer_id", name="uq_group_data_storers_pair"
        ),
    )

    group_id: UUID = Field(foreign_key="groups.id", primary_key=True)
    data_storer_id: UUID = Field(foreign_key="data_storers.id", primary_key=True)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class DefaultDataStorer(SQLModel, table=True):
    __tablename__ = "default_data_storers"

    data_storer_id: UUID = Field(
        foreign_key="data_storers.id",
        primary_key=True,
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class SyncMixin(TimestampMixin):
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        sa_type=enum_type(TaskStatus),
        nullable=False,
        index=True,
    )
    enabled: bool = Field(default=True, index=True)
    remote_record_id: str | None = Field(default=None, max_length=500)
    remote_url: str | None = None
    remote_attachment_json: dict[str, Any] = Field(
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


class AwemeDataStorerSync(SyncMixin, table=True):
    __tablename__ = "aweme_data_storer_syncs"
    __table_args__ = (
        UniqueConstraint(
            "aweme_id", "data_storer_id", name="uq_aweme_data_storer_syncs_pair"
        ),
        Index("ix_aweme_sync_schedule", "status", "next_run_at", "enabled"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    aweme_id: UUID = Field(foreign_key="awemes.id", index=True)
    data_storer_id: UUID = Field(foreign_key="data_storers.id", index=True)


class AccountDataStorerSync(SyncMixin, table=True):
    __tablename__ = "account_data_storer_syncs"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "data_storer_id", name="uq_account_data_storer_syncs_pair"
        ),
        Index("ix_account_sync_schedule", "status", "next_run_at", "enabled"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    account_id: UUID = Field(foreign_key="accounts.id", index=True)
    data_storer_id: UUID = Field(foreign_key="data_storers.id", index=True)


class WorkerTask(SQLModel, table=True):
    __tablename__ = "worker_tasks"
    __table_args__ = (
        UniqueConstraint("task_type", "subject_id", name="uq_worker_tasks_claim"),
        Index("ix_worker_tasks_timeout", "status", "timeout_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    task_type: WorkerTaskType = Field(
        sa_column=enum_column(WorkerTaskType, index=True)
    )
    subject_type: WorkerSubject = Field(sa_column=enum_column(WorkerSubject, index=True))
    subject_id: UUID = Field(index=True)
    pid: int | None = Field(default=None, index=True)
    process_group_id: int | None = None
    process_started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    run_token: str = Field(index=True, max_length=64)
    status: WorkerTaskStatus = Field(
        default=WorkerTaskStatus.STARTING,
        sa_column=enum_column(WorkerTaskStatus, index=True),
    )
    started_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    heartbeat_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    timeout_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )
