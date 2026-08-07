from __future__ import annotations

from inspect import isabstract

from cjdb_collectors.exceptions import CJDBError

from .base import BaseProvider
from .types import ProviderType


class ProviderRegistry:
    def __init__(self, providers: list[type[BaseProvider]]) -> None:
        self._by_namespace: dict[str, type[BaseProvider]] = {}
        self._by_type: dict[ProviderType, list[type[BaseProvider]]] = {
            provider_type: [] for provider_type in ProviderType
        }
        for provider_class in providers:
            self.register(provider_class)

    def register(self, provider_class: type[BaseProvider]) -> None:
        if isabstract(provider_class):
            raise CJDBError(
                f"provider {provider_class.__name__} has abstract methods",
                code="invalid_provider",
            )
        namespace_value = getattr(
            provider_class,
            "namespace",
            getattr(provider_class, "type", ""),
        )
        namespace = str(namespace_value).strip().lower()
        if not namespace:
            raise CJDBError("provider namespace is required", code="invalid_provider")
        existing = self._by_namespace.get(namespace)
        if existing is not None and existing is not provider_class:
            raise CJDBError(
                f"provider namespace conflict: {namespace}",
                code="provider_namespace_conflict",
            )
        self._by_namespace[namespace] = provider_class
        if not getattr(provider_class, "namespace", None):
            provider_class.namespace = namespace
        for value in provider_class.supported_types:
            provider_type = ProviderType(value)
            if provider_class not in self._by_type[provider_type]:
                self._by_type[provider_type].append(provider_class)

    def get(self, namespace: str) -> type[BaseProvider]:
        try:
            return self._by_namespace[namespace.strip().lower()]
        except KeyError as exc:
            raise CJDBError(
                f"unknown provider namespace: {namespace}",
                code="unknown_provider",
            ) from exc

    def get_by_type(
        self,
        provider_type: ProviderType | str,
    ) -> tuple[type[BaseProvider], ...]:
        return tuple(self._by_type[ProviderType(provider_type)])

    def classes(self) -> tuple[type[BaseProvider], ...]:
        return tuple(self._by_namespace.values())


__all__ = ["ProviderRegistry"]
