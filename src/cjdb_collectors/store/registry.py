from __future__ import annotations

from .base import (
    AccountStoreProviderMixin,
    AwemeStoreProviderMixin,
    BaseStoreProvider,
    StoreConfigurationError,
)


class StoreProviderRegistry:
    def __init__(self, providers: list[BaseStoreProvider]) -> None:
        self._providers: dict[str, BaseStoreProvider] = {}
        for provider in providers:
            key = provider.type.strip().lower()
            if not key:
                raise StoreConfigurationError("store provider type cannot be empty")
            if key in self._providers:
                raise StoreConfigurationError(
                    f"duplicate store provider: {provider.type}"
                )
            if provider.capabilities.get("aweme") and not isinstance(
                provider, AwemeStoreProviderMixin
            ):
                raise StoreConfigurationError(
                    f"store provider {provider.type} declares aweme support "
                    "but does not implement AwemeStoreProviderMixin"
                )
            if provider.capabilities.get("account") and not isinstance(
                provider, AccountStoreProviderMixin
            ):
                raise StoreConfigurationError(
                    f"store provider {provider.type} declares account support "
                    "but does not implement AccountStoreProviderMixin"
                )
            self._providers[key] = provider

    def get(self, provider_type: str) -> BaseStoreProvider:
        try:
            return self._providers[provider_type.strip().lower()]
        except KeyError as exc:
            raise StoreConfigurationError(
                f"unsupported store provider: {provider_type}"
            ) from exc

    def list(self) -> list[dict]:
        return [
            provider.metadata().model_dump()
            for _, provider in sorted(self._providers.items())
        ]

    def close(self) -> None:
        for provider in self._providers.values():
            provider.close()
