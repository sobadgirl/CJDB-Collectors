from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from cjdb_collectors.models import Provider
from cjdb_collectors.domains.store import (
    BaseStoreProvider,
    StoreProviderRegistry,
    StoreResult,
)
from cjdb_collectors.domains.store.providers import NotionStoreProvider

from .base import NotFoundError, SessionFactory, as_uuid, now_utc

_REGISTERED_STORE_NAMESPACES: set[str] = set()


def registered_store_namespaces() -> set[str]:
    return set(_REGISTERED_STORE_NAMESPACES)


class StoreProviderService:
    def __init__(
        self,
        session_factory: SessionFactory,
        registry: StoreProviderRegistry,
    ) -> None:
        self._session = session_factory
        self.registry = registry
        _REGISTERED_STORE_NAMESPACES.update(registry._provider_classes)

    def list(self, provider_types: tuple[str, ...] | list[str] | None = None) -> list[dict]:
        return self.registry.list(provider_types)

    def get_provider(self, provider_id: UUID | str) -> BaseStoreProvider:
        with self._session() as session:
            item = session.get(Provider, as_uuid(provider_id))
            if not item:
                raise NotFoundError("provider not found")
            return self.registry.get(item.namespace, item.setup_payload_json)

    def status(self, provider_id: UUID | str) -> dict[str, Any]:
        with self._session() as session:
            item = session.get(Provider, as_uuid(provider_id))
            if not item:
                raise NotFoundError("provider not found")
            provider = self.registry.get(item.namespace, item.setup_payload_json)
        result = provider.status()
        with self._session() as session:
            current = session.get(Provider, item.id)
            if current is not None:
                current.status = result.status
                current.status_message = result.message or current.status_message
                current.status_payload_json = dict(result.details or {})
                current.last_checked_at = result.checked_at or now_utc()
                current.next_check_at = current.last_checked_at + timedelta(
                    seconds=provider.status_refresh_seconds
                )
                session.add(current)
        return {
            "provider": {
                "id": str(item.id),
                "name": item.name,
                "namespace": item.namespace,
            },
            **result.model_dump(),
        }

    def setup_payload(self, provider_id: UUID | str) -> dict[str, Any]:
        with self._session() as session:
            item = session.get(Provider, as_uuid(provider_id))
            if item is None:
                raise NotFoundError("provider not found")
            return dict(item.setup_payload_json or {})

    def persist_setup_result(
        self,
        provider_id: UUID | str,
        setup_payload: dict[str, Any],
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._session() as session:
            item = session.get(Provider, as_uuid(provider_id))
            if item is None:
                raise NotFoundError("provider not found")
            item.setup_payload_json = dict(setup_payload)
            item.status = "ready"
            item.status_message = message or "Store Provider 初始化完成"
            item.status_payload_json = dict(details or {})
            session.add(item)

    def persist_setup_failure(self, provider_id: UUID | str, message: str) -> None:
        with self._session() as session:
            current = session.get(Provider, as_uuid(provider_id))
            if current is not None:
                current.status = "error"
                current.status_message = message
                session.add(current)

    def is_ready(self, provider_id: UUID | str) -> bool:
        with self._session() as session:
            item = session.get(Provider, as_uuid(provider_id))
            if item is None:
                return False
            return item.status == "ready"

    def get_visit_url(
        self,
        provider_id: UUID | str,
        result: StoreResult,
    ) -> str | None:
        return self.get_provider(provider_id).get_visit_url(result)

def build_store_provider_service(
    session_factory: SessionFactory,
) -> StoreProviderService:
    return StoreProviderService(
        session_factory,
        StoreProviderRegistry([NotionStoreProvider]),
    )
