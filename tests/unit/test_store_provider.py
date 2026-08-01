from collections.abc import Mapping
from typing import Any

from cjdb_collectors.store import (
    BaseStoreProvider,
    StoreConfigurationError,
    StoreProviderRegistry,
    StoreStatus,
)
from cjdb_collectors.store.providers import NotionStoreProvider


def test_store_provider_catalog_exposes_setup_contract() -> None:
    registry = StoreProviderRegistry([NotionStoreProvider()])

    metadata = registry.list()[0]

    assert metadata["type"] == "notion"
    assert metadata["name"] == "Notion"
    assert metadata["capabilities"] == {
        "aweme": True,
        "account": True,
        "attachments": False,
    }
    assert [item["type"] for item in metadata["parameters"]] == [
        "password",
        "text",
        "text",
    ]
    registry.close()


def test_store_registry_rejects_declared_capability_without_mixin() -> None:
    class BrokenStoreProvider(BaseStoreProvider):
        type = "broken"
        name = "未完整实现"
        capabilities = {"aweme": True}

        def status(self, config: Mapping[str, Any]) -> StoreStatus:
            return StoreStatus(status="ready", ready=True)

    try:
        StoreProviderRegistry([BrokenStoreProvider()])
    except StoreConfigurationError as exc:
        assert "AwemeStoreProviderMixin" in str(exc)
    else:
        raise AssertionError("declared capabilities must have matching mixins")
