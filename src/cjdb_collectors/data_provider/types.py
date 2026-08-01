from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cjdb_collectors.config_fields import ConfigParameter, ConfigParameterType
from cjdb_collectors.models import ContentType, Platform
from enum import StrEnum


class DataProviderType(StrEnum):
    DOUYIN_AWEME_COLLECT = "douyin_aweme_collect"
    XIAOHONGSHU_AWEME_COLLECT = "xiaohongshu_aweme_collect"
    WECHAT_CHANNELS_AWEME_COLLECT = "wechat_channels_aweme_collect"
    WECHAT_MP_AWEME_COLLECT = "wechat_mp_aweme_collect"
    XIAOHONGSHU_COMMENT_COLLECT = "xiaohongshu_comment_collect"
    ACCOUNT_COLLECT = "account_collect"
    VIDEO_TRANSCRIPTION = "video_transcription"


ProviderParameterType = ConfigParameterType
ProviderParameter = ConfigParameter


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    namespace: str
    name: str
    type: DataProviderType
    platforms: list[str]
    parameters: list[dict[str, Any]]

    def model_dump(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "name": self.name,
            "type": self.type.value,
            "platforms": self.platforms,
            "parameters": self.parameters,
        }


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    status: str
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime | None = None
    setup_pid: int | None = None

    def model_dump(self) -> dict[str, Any]:
        checked_at = self.checked_at or datetime.now(timezone.utc)
        return {
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "checked_at": checked_at.isoformat(),
            "setup_pid": self.setup_pid,
        }


@dataclass(frozen=True, slots=True)
class ProviderSetupResult:
    status: ProviderStatus
    logs: list[str]

    def model_dump(self) -> dict[str, Any]:
        return {
            "status": self.status.model_dump(),
            "logs": self.logs,
        }


class ProviderDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AwemeData(ProviderDataModel):
    platform_aweme_id: str
    content_type: ContentType
    title: str | None = None
    description: str | None = None
    published_at: datetime | None = None
    video_url: str | None = None
    cover_url: str | None = None
    photos: list[str] = Field(default_factory=list)
    play_count: int | None = None
    like_count: int | None = None
    collect_count: int | None = None
    share_count: int | None = None
    comment_count: int | None = None
    extra_data_json: dict[str, Any] = Field(default_factory=dict)


class AccountData(ProviderDataModel):
    platform_account_id: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    profile_data_json: dict[str, Any] = Field(default_factory=dict)


class CommentPage(ProviderDataModel):
    comments: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    normalized_text: str | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    url: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FetchAwemeRequest:
    platform: Platform
    platform_aweme_id: str
    content_type: ContentType = ContentType.UNKNOWN
    source_url: str = ""


@dataclass(frozen=True, slots=True)
class FetchCommentsRequest:
    platform: Platform
    platform_aweme_id: str
    source_url: str = ""
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class FetchAccountRequest:
    platform: Platform
    profile_url: str
    platform_account_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolveVideoRequest:
    platform: Platform
    platform_aweme_id: str
    source_url: str = ""
    media_url: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    video_path: str | Path
