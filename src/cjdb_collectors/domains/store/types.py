from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from cjdb_collectors.config_fields import (
    ConfigParameter,
    ConfigParameterType,
    checkbox_param,
    local_path_param,
    multi_select_param,
    number_param,
    password_param,
    single_select_param,
    text_param,
)
from cjdb_collectors.domains.types import SetupResult

StoreParameterType = ConfigParameterType
StoreParameter = ConfigParameter


@dataclass(frozen=True, slots=True)
class StoreProviderMetadata:
    type: str
    name: str
    capabilities: dict[str, bool]
    parameters: list[dict[str, Any]]

    def model_dump(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "capabilities": self.capabilities,
            "parameters": self.parameters,
        }


@dataclass(frozen=True, slots=True)
class StoreStatus:
    status: str
    ready: bool
    message: str | None = None
    details: dict[str, Any] | None = None
    checked_at: datetime | None = None

    def model_dump(self) -> dict[str, Any]:
        checked_at = self.checked_at or datetime.now(timezone.utc)
        return {
            "status": self.status,
            "ready": self.ready,
            "message": self.message,
            "details": self.details or {},
            "checked_at": checked_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class AwemeStorePayload:
    local_id: str
    platform: str
    platform_aweme_id: str | None
    aweme_url: str | None
    source_url: str
    title: str | None = None
    description: str | None = None
    published_at: datetime | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    comments: list[Mapping[str, Any]] = field(default_factory=list)
    video_path: str | None = None
    photo_paths: list[str | Mapping[str, Any]] = field(default_factory=list)
    transcription_text: str | None = None


@dataclass(frozen=True, slots=True)
class AccountStorePayload:
    local_id: str
    platform: str
    platform_account_id: str | None
    profile_url: str
    display_name: str | None = None
    profile_data: Mapping[str, Any] = field(default_factory=dict)
    avatar_path: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionStorePayload:
    local_id: str
    aweme_id: str | None
    source_url: str | None
    video_path: str | None
    status: str
    text: str | None = None
    normalized_text: str | None = None
    text_summary: str | None = None
    duration_seconds: float | None = None
    segments: list[Mapping[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StoreResult:
    success: bool
    message: str | None = None
    success_payload: Mapping[str, Any] = field(default_factory=dict)


__all__ = [
    "AccountStorePayload",
    "AwemeStorePayload",
    "StoreParameter",
    "StoreParameterType",
    "StoreProviderMetadata",
    "StoreResult",
    "StoreStatus",
    "SetupResult",
    "TranscriptionStorePayload",
    "checkbox_param",
    "local_path_param",
    "multi_select_param",
    "number_param",
    "password_param",
    "single_select_param",
    "text_param",
]
