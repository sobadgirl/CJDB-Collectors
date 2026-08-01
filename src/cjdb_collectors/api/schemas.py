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
    url: str = Field(min_length=1)
    platform: Platform
    group_ids: list[str] = Field(default_factory=list)


class AccountUpdate(ApiModel):
    display_name: str | None = None
    profile_url: str | None = None


class AwemeCreate(ApiModel):
    url: str = Field(min_length=1)
    platform: Platform
    content_type: Literal["unknown", "video", "image", "article", "live"] | None = None
    group_ids: list[str] = Field(default_factory=list)
    download_video: bool = False
    collect_comments: bool = False
    transcribe: bool = False


class AwemeUpdate(ApiModel):
    title: str | None = None
    description: str | None = None
    aweme_url: str | None = None
    video_path: str | None = None
    cover_path: str | None = None
    photos: list[str] | None = None
    photo_paths: list[str] | None = None
    platform: Platform | None = None
    platform_aweme_id: str | None = None
    content_type: Literal["unknown", "video", "image", "article", "live"] | None = None


class GroupCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    color: str | None = None
    sort_order: int = 0


class GroupUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    color: str | None = None
    sort_order: int | None = None
    status: Literal["active", "disabled"] | None = None


class IdList(ApiModel):
    ids: list[str] = Field(default_factory=list)


class GroupMembersUpdate(ApiModel):
    aweme_ids: list[str] | None = None
    account_ids: list[str] | None = None


class DataStorerCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    type: str = "notion"
    secret_ref: str
    connection_config: dict[str, Any] = Field(default_factory=dict)
    container_config: dict[str, Any] = Field(default_factory=dict)
    field_mapping: dict[str, Any] = Field(default_factory=dict)
    attachment_policy: dict[str, Any] = Field(default_factory=dict)
    conflict_policy: str = "upsert"


class DataStorerUpdate(ApiModel):
    name: str | None = None
    secret_ref: str | None = None
    connection_config: dict[str, Any] | None = None
    container_config: dict[str, Any] | None = None
    field_mapping: dict[str, Any] | None = None
    attachment_policy: dict[str, Any] | None = None
    conflict_policy: str | None = None
    status: Literal["active", "needs_attention", "disabled"] | None = None


class StoreCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    type: str = Field(min_length=1)
    values: dict[str, Any] = Field(default_factory=dict)
    default: bool = False


class StoreUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "needs_attention", "disabled"] | None = None


class StoreSetup(ApiModel):
    values: dict[str, Any] = Field(default_factory=dict)


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


class ConfigSet(ApiModel):
    key: str = Field(min_length=1)
    value: Any


class ConfigGetMany(ApiModel):
    keys: list[str] = Field(min_length=1)


class ConfigPatch(ApiModel):
    values: dict[str, Any] = Field(min_length=1)


class ProviderSetup(ApiModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ProviderSelection(ApiModel):
    type: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
