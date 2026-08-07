from __future__ import annotations

from inspect import isabstract
import logging
from typing import Any, Mapping

from cjdb_collectors.exceptions import CJDBError
from cjdb_collectors.domains.provider import ProviderRegistry

from .base import (
    AccountStoreProviderMixin,
    AwemeStoreProviderMixin,
    BaseStoreProvider,
    TranscriptionStoreProviderMixin,
)


class StoreProviderRegistry:
    def __init__(self, providers: list[type[BaseStoreProvider]]) -> None:
        self._registry = ProviderRegistry(providers)
        self._provider_classes: dict[str, type[BaseStoreProvider]] = {}
        for provider_class in providers:
            if not isinstance(provider_class, type) or not issubclass(
                provider_class,
                BaseStoreProvider,
            ):
                raise CJDBError(
                    "registered store provider must inherit BaseStoreProvider",
                    code="invalid_store_provider",
                )
            if isabstract(provider_class):
                raise CJDBError(
                    f"store provider {provider_class.__name__} has "
                    "unimplemented abstract methods",
                    code="invalid_store_provider",
                )
            key = provider_class.type.strip().lower()
            if not key:
                raise CJDBError(
                    "store provider type cannot be empty",
                    code="invalid_store_provider",
                )
            if key in self._provider_classes:
                raise CJDBError(
                    f"duplicate store provider: {provider_class.type}",
                    code="duplicate_store_provider",
                )
            capabilities = provider_class.capabilities
            self._validate_capability(
                provider_class,
                capabilities,
                "aweme",
                AwemeStoreProviderMixin,
            )
            self._validate_capability(
                provider_class,
                capabilities,
                "account",
                AccountStoreProviderMixin,
            )
            self._validate_capability(
                provider_class,
                capabilities,
                "transcription",
                TranscriptionStoreProviderMixin,
            )
            self._provider_classes[key] = provider_class

    @staticmethod
    def _validate_capability(
        provider_class: type[BaseStoreProvider],
        capabilities: Mapping[str, bool],
        capability: str,
        mixin: type,
    ) -> None:
        if capabilities.get(capability) and not issubclass(provider_class, mixin):
            raise CJDBError(
                f"store provider {provider_class.type} declares {capability} support "
                f"but does not implement {mixin.__name__}",
                code="invalid_store_provider",
            )

    def get(
        self,
        provider_type: str,
        setup_payload: Mapping[str, Any] | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> BaseStoreProvider:
        try:
            provider_class = self._registry.get(provider_type)
        except CJDBError as exc:
            raise CJDBError(
                f"unsupported store provider: {provider_type}",
                code="unsupported_store_provider",
            ) from exc
        return provider_class(setup_payload=setup_payload, logger=logger)

    def list(self, provider_types: tuple[str, ...] | list[str] | None = None) -> list[dict]:
        allowed = None
        if provider_types is not None:
            allowed = {
                provider_class
                for provider_type in provider_types
                for provider_class in self._registry.get_by_type(provider_type)
            }
        return [
            provider_class().metadata().model_dump()
            for _, provider_class in sorted(self._provider_classes.items())
            if allowed is None or provider_class in allowed
        ]
