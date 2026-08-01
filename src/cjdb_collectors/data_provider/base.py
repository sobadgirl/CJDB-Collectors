from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import psutil

from cjdb_collectors.exceptions import InvalidOperationError
from cjdb_collectors.config_fields import clean_config_values

from .types import (
    AccountData,
    AwemeData,
    CommentPage,
    DataProviderType,
    FetchAccountRequest,
    FetchAwemeRequest,
    FetchCommentsRequest,
    ProviderMetadata,
    ProviderParameter,
    ProviderSetupResult,
    ProviderStatus,
    ResolvedMedia,
    ResolveVideoRequest,
    TranscriptionRequest,
    TranscriptionResult,
)


class BaseDataProvider(ABC):
    namespace: str
    name: str
    supported_types: tuple[DataProviderType, ...]
    parameters: tuple[ProviderParameter, ...] = ()
    platforms_by_type: dict[DataProviderType, set] = {}
    status_refresh_seconds = 30

    def __init__(self) -> None:
        self.parameter_values: dict[str, Any] = {}
        self._current_status: ProviderStatus | None = None

    def metadata(
        self,
        provider_type: DataProviderType | str | None = None,
    ) -> ProviderMetadata:
        selected_type = self._metadata_type(provider_type)
        platforms = self.platforms_by_type.get(selected_type, set())
        return ProviderMetadata(
            namespace=self.namespace,
            name=self.name,
            type=selected_type,
            platforms=[
                platform.value
                for platform in sorted(platforms, key=lambda item: item.value)
            ],
            parameters=[parameter.model_dump() for parameter in self.parameters],
        )

    def set_status(self, status: ProviderStatus) -> ProviderStatus:
        if status.checked_at is None:
            status = replace(status, checked_at=datetime.now(timezone.utc))
        self._current_status = status
        return status

    def get_status(self) -> ProviderStatus | None:
        return self._current_status

    def status(self) -> ProviderStatus:
        current = self.get_status()
        if current is not None and not self._status_needs_refresh(current):
            return current
        return self.set_status(self.refresh_status())

    def refresh_status(self) -> ProviderStatus:
        values = dict(self.parameter_values)
        missing = [
            parameter.key
            for parameter in self.parameters
            if parameter.required
            and not values.get(parameter.key, parameter.default)
        ]
        if missing:
            return ProviderStatus(
                status="unconfigured",
                message=f"缺少必填参数：{', '.join(missing)}",
            )
        return ProviderStatus(status="ready")

    def setup(self) -> ProviderSetupResult:
        return ProviderSetupResult(
            status=self.refresh_status(),
            logs=[f"已保存 Provider 配置：{self.namespace}"],
        )

    def setup_values(
        self,
        values: dict[str, Any],
        *,
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._setup_values(values, current=current)

    def _metadata_type(
        self,
        provider_type: DataProviderType | str | None = None,
    ) -> DataProviderType:
        if provider_type is not None:
            selected_type = DataProviderType(provider_type)
            if selected_type not in self.supported_types:
                raise InvalidOperationError(
                    f"provider {self.namespace} does not support {selected_type.value}"
                )
            return selected_type
        return self.supported_types[0]

    def _setup_values(
        self,
        values: dict[str, Any],
        *,
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return clean_config_values(
            self.parameters,
            values,
            current=current,
            unknown_message="unknown provider parameters: {keys}",
            required_message="provider parameter is required: {key}",
            error_type=InvalidOperationError,
        )

    def _status_needs_refresh(self, status: ProviderStatus) -> bool:
        if status.status == "setting_up":
            return not self._status_process_alive(status)
        checked_at = status.checked_at
        if checked_at is None:
            return True
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - checked_at).total_seconds()
        return age > self.status_refresh_seconds

    @staticmethod
    def _status_process_alive(status: ProviderStatus) -> bool:
        if status.setup_pid is None:
            return False
        try:
            process = psutil.Process(status.setup_pid)
            checked_at = status.checked_at
            if checked_at is not None and checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            if (
                checked_at is not None
                and process.create_time() > checked_at.timestamp() + 1
            ):
                return False
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True


class AwemeProviderMixin(ABC):
    @abstractmethod
    def fetch_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        """采集并清洗作品数据。"""

    @abstractmethod
    def resolve_video(self, request: ResolveVideoRequest) -> ResolvedMedia | None:
        """解析作品的可下载视频地址。"""


class CommentProviderMixin(ABC):
    @abstractmethod
    def fetch_comments(self, request: FetchCommentsRequest) -> CommentPage:
        """采集并清洗评论数据。"""


class AccountProviderMixin(ABC):
    @abstractmethod
    def fetch_account(self, request: FetchAccountRequest) -> AccountData:
        """采集并清洗账号数据。"""


class VideoTranscriptionProviderMixin(ABC):
    @abstractmethod
    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """转写视频。"""
