from __future__ import annotations

from typing import Any
from uuid import UUID

from cjdb_collectors.config import SecretSettings
from cjdb_collectors.models import DataStorer, DataStorerStatus
from cjdb_collectors.store import (
    StoreProviderRegistry,
    Storer,
    StorerIdentity,
)
from cjdb_collectors.store.providers import NotionStoreProvider

from .base import NotFoundError, SessionFactory, as_uuid, now_utc


class StoreProviderService:
    def __init__(
        self,
        session_factory: SessionFactory,
        registry: StoreProviderRegistry,
        *,
        config: Any = None,
        secrets: SecretSettings | None = None,
    ) -> None:
        self._session = session_factory
        self.registry = registry
        self.config = config
        self.secrets = secrets or SecretSettings()

    def close(self) -> None:
        self.registry.close()

    def list(self) -> list[dict]:
        return self.registry.list()

    def get_storer(self, store_id: UUID | str) -> Storer:
        with self._session() as session:
            item = session.get(DataStorer, as_uuid(store_id))
            if not item:
                raise NotFoundError("store not found")
            identity = StorerIdentity(
                id=item.id,
                name=item.name,
                provider_type=item.type,
            )
            provider = self.registry.get(item.type)
            values = self._configured_values(item)
        return Storer(identity, provider, values)

    def setup(
        self,
        store_id: UUID | str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        storer = self.get_storer(store_id)
        cleaned = storer.provider.setup(values, current=storer.config)
        if self.config is None:
            raise RuntimeError("store config is not bound")
        self.config.patch(
            {
                f"stores.{storer.id}.{key}": value
                for key, value in cleaned.items()
            }
        )
        configured = self.get_storer(storer.id)
        status = configured.status().model_dump()
        with self._session() as session:
            item = session.get(DataStorer, configured.id)
            if item:
                item.status = (
                    DataStorerStatus.ACTIVE
                    if status["ready"]
                    else DataStorerStatus.NEEDS_ATTENTION
                )
                item.validation_error = status.get("message")
                item.last_validated_at = now_utc()
                session.add(item)
        return {
            "store": {
                "id": str(configured.id),
                "name": configured.name,
                "type": configured.type,
            },
            **status,
        }

    def status(self, store_id: UUID | str) -> dict[str, Any]:
        storer = self.get_storer(store_id)
        return {
            "store": {
                "id": str(storer.id),
                "name": storer.name,
                "type": storer.type,
            },
            **storer.status().model_dump(),
        }

    def _configured_values(self, item: DataStorer) -> dict[str, Any]:
        values = {
            **item.connection_config_json,
            "container": item.container_config_json,
            "field_mapping": item.field_mapping_json,
            "attachment_policy": item.attachment_policy_json,
            "conflict_policy": str(item.conflict_policy.value),
        }
        database_id = item.container_config_json.get("database_id")
        if database_id:
            values["database_id"] = database_id
        if item.secret_ref:
            values["secret_ref"] = item.secret_ref
            secret = self.secrets.resolve(item.secret_ref)
            if secret:
                values["token"] = secret
                values["secret"] = secret
        if self.config is not None:
            try:
                configured = self.config.get(f"stores.{item.id}")
            except Exception:
                configured = {}
            if isinstance(configured, dict):
                values.update(configured)
        return values


def build_store_provider_service(
    session_factory: SessionFactory,
    *,
    config: Any = None,
    secrets: SecretSettings | None = None,
) -> StoreProviderService:
    return StoreProviderService(
        session_factory,
        StoreProviderRegistry([NotionStoreProvider()]),
        config=config,
        secrets=secrets,
    )
