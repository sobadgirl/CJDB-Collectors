from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cjdb_collectors.config_fields import (
    ConfigParameter,
    ConfigParameterType,
    local_path_param,
    multi_select_param,
    number_param,
    password_param,
    single_select_param,
    text_param,
)
from cjdb_collectors.models import ContentType, Platform
from cjdb_collectors.domains.types import SetupResult
from cjdb_collectors.domains.provider import ProviderType


DataProviderType = ProviderType


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
            "ready": self.status == "ready",
            "message": self.message,
            "details": self.details,
            "checked_at": checked_at.isoformat(),
            "setup_pid": self.setup_pid,
        }


class ProviderDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AwemeData(ProviderDataModel):
    platform_aweme_id: str
    platform_account_id: str | None = None
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


class PageStopPolicy(ProviderDataModel):
    max_count: int | None = Field(default=None, ge=1)
    max_pages: int | None = Field(default=None, ge=1)
    earliest_date: datetime | None = None


class AccountAwemePage(ProviderDataModel):
    awemes: list[AwemeData] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    total_count: int | None = None
    done: bool = True
    request: dict[str, Any] = Field(default_factory=dict)
    progress_payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def items(self) -> list[AwemeData]:
        return self.awemes


class AccountData(ProviderDataModel):
    platform_account_id: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    signature: str | None = None
    location: str | None = None
    ip_location: str | None = None
    gender: str | None = None
    verified: bool | None = None
    follower_count: int | None = None
    following_count: int | None = None
    work_count: int | None = None
    like_count: int | None = None
    collect_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None
    total_favorited: int | None = None
    extra_data_json: dict[str, Any] = Field(default_factory=dict)


class CommentPage(ProviderDataModel):
    comments: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    done: bool = True
    request: dict[str, Any] = Field(default_factory=dict)
    progress_payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.comments


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str


@dataclass(frozen=True, slots=True)
class FetchAwemeRequest:
    platform: Platform
    platform_aweme_id: str | None = None
    content_type: ContentType = ContentType.UNKNOWN
    source_url: str = ""

    @property
    def aweme_id(self) -> str | None:
        return self.platform_aweme_id

    @property
    def aweme_url(self) -> str:
        return self.source_url


@dataclass(frozen=True, slots=True)
class FetchCommentsRequest:
    platform: Platform
    platform_aweme_id: str
    source_url: str = ""
    cursor: str | None = None
    max_comments: int | None = None
    stop_policy: PageStopPolicy | None = None
    progress_payload: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FetchAccountRequest:
    platform: Platform
    profile_url: str
    platform_account_id: str | None = None

    @property
    def account_url(self) -> str:
        return self.profile_url

    @property
    def account_id(self) -> str | None:
        return self.platform_account_id


@dataclass(frozen=True, slots=True)
class FetchAccountAwemesRequest:
    platform: Platform
    profile_url: str = ""
    platform_account_id: str | None = None
    cursor: str | None = None
    page_size: int = 20
    stop_policy: PageStopPolicy | None = None
    progress_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def account_url(self) -> str:
        return self.profile_url

    @property
    def account_id(self) -> str | None:
        return self.platform_account_id


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    video_path: str | Path


__all__ = [
    "AccountData",
    "AccountAwemePage",
    "AwemeData",
    "CommentPage",
    "DataProviderType",
    "FetchAccountAwemesRequest",
    "FetchAccountRequest",
    "FetchAwemeRequest",
    "FetchCommentsRequest",
    "PageStopPolicy",
    "ProviderMetadata",
    "ProviderParameter",
    "ProviderParameterType",
    "SetupResult",
    "ProviderStatus",
    "TranscriptionRequest",
    "TranscriptionResult",
    "local_path_param",
    "multi_select_param",
    "number_param",
    "password_param",
    "single_select_param",
    "text_param",
]
