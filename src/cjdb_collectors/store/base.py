from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from cjdb_collectors.config_fields import clean_config_values

from .types import (
    AccountStorePayload,
    AwemeStorePayload,
    StoreParameter,
    StoreProviderMetadata,
    StoreResult,
    StoreStatus,
    StorerIdentity,
)


class StoreProviderError(RuntimeError):
    code = "store_provider_error"


class StoreConfigurationError(StoreProviderError):
    code = "invalid_configuration"


class StoreAuthenticationError(StoreProviderError):
    code = "authentication_failed"


class StoreUnavailableError(StoreProviderError):
    code = "temporarily_unavailable"


class StoreSchemaError(StoreProviderError):
    code = "schema_mismatch"


class BaseStoreProvider(ABC):
    type: str
    name: str
    parameters: tuple[StoreParameter, ...] = ()
    capabilities: dict[str, bool] = {}

    def metadata(self) -> StoreProviderMetadata:
        return StoreProviderMetadata(
            type=self.type,
            name=self.name,
            capabilities=dict(self.capabilities),
            parameters=[parameter.model_dump() for parameter in self.parameters],
        )

    def setup(
        self,
        values: Mapping[str, Any],
        *,
        current: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return clean_config_values(
            self.parameters,
            dict(values),
            current=dict(current or {}),
            unknown_message="unknown store parameters: {keys}",
            required_message="store parameter is required: {key}",
            error_type=StoreConfigurationError,
        )

    @abstractmethod
    def status(self, config: Mapping[str, Any]) -> StoreStatus:
        """检查一个已配置 Storer 是否可用。"""

    def close(self) -> None:
        pass


class AwemeStoreProviderMixin(ABC):
    @abstractmethod
    def store_aweme(
        self,
        payload: AwemeStorePayload,
        config: Mapping[str, Any],
        remote_record_id: str | None = None,
    ) -> StoreResult:
        """创建或更新一个作品。"""


class AccountStoreProviderMixin(ABC):
    @abstractmethod
    def store_account(
        self,
        payload: AccountStorePayload,
        config: Mapping[str, Any],
        remote_record_id: str | None = None,
    ) -> StoreResult:
        """创建或更新一个账号。"""


class Storer:
    def __init__(
        self,
        identity: StorerIdentity,
        provider: BaseStoreProvider,
        config: Mapping[str, Any],
    ) -> None:
        self.identity = identity
        self.provider = provider
        self.config = dict(config)

    @property
    def id(self):
        return self.identity.id

    @property
    def name(self) -> str:
        return self.identity.name

    @property
    def type(self) -> str:
        return self.identity.provider_type

    def status(self) -> StoreStatus:
        return self.provider.status(self.config)

    def store_aweme(
        self,
        payload: AwemeStorePayload,
        remote_record_id: str | None = None,
    ) -> StoreResult:
        if not isinstance(self.provider, AwemeStoreProviderMixin):
            raise StoreConfigurationError(
                f"store provider {self.type} does not support awemes"
            )
        return self.provider.store_aweme(payload, self.config, remote_record_id)

    def store_account(
        self,
        payload: AccountStorePayload,
        remote_record_id: str | None = None,
    ) -> StoreResult:
        if not isinstance(self.provider, AccountStoreProviderMixin):
            raise StoreConfigurationError(
                f"store provider {self.type} does not support accounts"
            )
        return self.provider.store_account(payload, self.config, remote_record_id)
