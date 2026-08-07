from __future__ import annotations

import textwrap

from pydantic import SecretStr

from cjdb_collectors.settings import load_settings
from cjdb_collectors.domains.store.providers import NotionStoreProvider


def test_config_yaml_secrets_are_mapped_and_masked(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            version: 1
            services:
              collector:
                enabled: true
                base_url: http://localhost:8001
                timeout_seconds: 10
              tikhub:
                enabled: true
                base_url: https://api.tikhub.io
                timeout_seconds: 30
            secrets:
              tikhub_api_key: tikhub-secret
              collector_api_key: collector-secret
            """
        ),
        encoding="utf-8",
    )

    config = load_settings(config_path, force_reload=True)

    assert isinstance(config.services.tikhub.api_key, SecretStr)
    assert config.services.tikhub.api_key.get_secret_value() == "tikhub-secret"
    assert config.services.collector.api_key.get_secret_value() == "collector-secret"
    dumped = config.model_dump(mode="json")
    assert dumped["services"]["tikhub"]["api_key"] == "**********"


def test_notion_store_provider_uses_its_runtime_config_only() -> None:
    provider = NotionStoreProvider({"token": "config-secret"})

    assert provider._token() == "config-secret"
