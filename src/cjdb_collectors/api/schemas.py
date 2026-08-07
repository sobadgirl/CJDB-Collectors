"""Stable HTTP request schemas.

These are deliberately separate from SQLModel persistence models: the public
API can evolve without leaking database representation into callers.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cjdb_collectors.models import Platform


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pagination(ApiModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class AccountCreate(ApiModel):
    url: str | None = Field(default=None, min_length=1)
    platform: Platform
    platform_account_id: str | None = Field(default=None, min_length=1)
    project_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def url_or_platform_account_id_required(self) -> "AccountCreate":
        if not self.url and not self.platform_account_id:
            raise ValueError("url or platform_account_id is required")
        return self


class AccountUpdate(ApiModel):
    display_name: str | None = None
    profile_url: str | None = None


class AwemeCreate(ApiModel):
    url: str | None = Field(default=None, min_length=1)
    platform: Platform
    platform_aweme_id: str | None = Field(default=None, min_length=1)
    content_type: Literal["unknown", "video", "image", "article", "live"] | None = None
    project_ids: list[str] = Field(default_factory=list)
    download_video: bool = False
    collect_comments: bool = False
    comment_max_count: int | None = Field(default=None, ge=1, le=5000)
    transcribe: bool = False

    @model_validator(mode="after")
    def url_or_platform_aweme_id_required(self) -> "AwemeCreate":
        if not self.url and not self.platform_aweme_id:
            raise ValueError("url or platform_aweme_id is required")
        return self


class AwemeUpdate(ApiModel):
    title: str | None = None
    description: str | None = None
    aweme_url: str | None = None
    video_path: str | None = None
    cover_path: str | None = None
    photos: list[str] | None = None
    photo_paths: list[str | dict[str, str | None]] | None = None
    platform: Platform | None = None
    platform_aweme_id: str | None = None
    content_type: Literal["unknown", "video", "image", "article", "live"] | None = None


class ProjectCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    color: str | None = None
    sort_order: int = 0


class ProjectUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    color: str | None = None
    sort_order: int | None = None
    status: Literal["active", "disabled"] | None = None


class IdList(ApiModel):
    ids: list[str] = Field(default_factory=list)


class ProjectMembersUpdate(ApiModel):
    aweme_ids: list[str] | None = None
    account_ids: list[str] | None = None


class TranscriptionCreate(ApiModel):
    video_path: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def exactly_one_input(self) -> "TranscriptionCreate":
        if bool(self.video_path) == bool(self.url):
            raise ValueError("exactly one of video_path or url is required")
        return self


class SyncQuery(ApiModel):
    aweme_id: str | None = None
    account_id: str | None = None
    status: str | None = None


class ConfigValue(ApiModel):
    value: Any


class SettingsSet(ApiModel):
    key: str = Field(min_length=1)
    value: Any


class SettingsGetMany(ApiModel):
    keys: list[str] = Field(min_length=1)


class SettingsPatch(ApiModel):
    values: dict[str, Any] = Field(min_length=1)


class ProviderSetup(ApiModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ProviderSelection(ApiModel):
    type: str = Field(min_length=1)
    namespace: str | None = Field(default=None, min_length=1)
    provider_id: str | None = None
    project_id: str | None = Field(default=None, min_length=1)
    selected: bool = True


class ProviderCreate(ApiModel):
    namespace: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=255)
    project_id: str = Field(min_length=1)
    provider_type: str | None = Field(default=None, min_length=1)
    values: dict[str, Any] = Field(default_factory=dict)


class ProviderUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, min_length=1, max_length=64)
