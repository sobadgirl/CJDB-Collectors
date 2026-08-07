from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from typing import Any, Mapping

from cjdb_collectors.config_fields import clean_config_values
from cjdb_collectors.exceptions import CJDBError
from cjdb_collectors.domains.provider import BaseProvider

from .types import (
    AccountStorePayload,
    AwemeStorePayload,
    StoreParameter,
    StoreProviderMetadata,
    StoreResult,
    StoreStatus,
    SetupResult,
    TranscriptionStorePayload,
)


class BaseStoreProvider(BaseProvider, ABC):
    type: str
    namespace: str
    name: str
    supported_types: tuple[str, ...] = ()
    parameters: tuple[StoreParameter, ...] = ()
    capabilities: dict[str, bool] = {}

    def __init__(
        self,
        setup_payload: Mapping[str, Any] | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(setup_payload, logger=logger)

    def metadata(self) -> StoreProviderMetadata:
        return StoreProviderMetadata(
            type=self.type,
            name=self.name,
            capabilities=dict(self.capabilities),
            parameters=[parameter.model_dump() for parameter in self.parameters],
        )

    def parse_setup_params(
        self,
        values: Mapping[str, Any],
        *,
        current: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return clean_config_values(
                self.parameters,
                dict(values),
                current=dict(current or self.setup_payload),
                unknown_message="unknown store parameters: {keys}",
                required_message="store parameter is required: {key}",
                error_type=ValueError,
            )
        except ValueError as exc:
            raise CJDBError(
                str(exc),
                code="invalid_store_configuration",
            ) from exc

    @abstractmethod
    def setup(self, params: Mapping[str, Any]) -> SetupResult:
        """根据本次临时参数初始化，并返回需要持久化的 payload。"""

    @abstractmethod
    def status(self) -> StoreStatus:
        """检查当前已配置的 Store 是否可用。"""

    def get_visit_url(self, result: StoreResult) -> str | None:
        return None


class AwemeStoreProviderMixin(ABC):
    @abstractmethod
    def store_aweme(
        self,
        payload: AwemeStorePayload,
        last_store_result: StoreResult | None,
    ) -> StoreResult:
        """创建或更新一个作品。"""


class AccountStoreProviderMixin(ABC):
    @abstractmethod
    def store_account(
        self,
        payload: AccountStorePayload,
        last_store_result: StoreResult | None,
    ) -> StoreResult:
        """创建或更新一个账号。"""


class TranscriptionStoreProviderMixin(ABC):
    @abstractmethod
    def store_transcription(
        self,
        payload: TranscriptionStorePayload,
        last_store_result: StoreResult | None,
    ) -> StoreResult:
        """创建或更新一个视频转写结果。"""
