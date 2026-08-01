from __future__ import annotations

from pathlib import Path

import yaml

from cjdb_collectors.config import load_settings
from cjdb_collectors.services.base import InvalidOperationError
from cjdb_collectors.services.configuration import ConfigurationService


def _write_config(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "app": {
                    "data_dir": ".data",
                    "database_path": ".data/test.db",
                    "logs_dir": "logs",
                },
                "spider": {
                    "selected": {
                        "douyin": "tikhub",
                        "xiaohongshu": "tikhub",
                        "wechat_mp": "tikhub",
                        "wechat_channels": "tikhub",
                    }
                },
                "providers": {
                    "selected": {
                        "douyin_aweme_collect": "tikhub",
                        "xiaohongshu_aweme_collect": "tikhub",
                        "wechat_channels_aweme_collect": "tikhub",
                        "wechat_mp_aweme_collect": "tikhub",
                        "xiaohongshu_comment_collect": "tikhub",
                        "video_transcription": "faster_whisper",
                    }
                },
                "services": {
                    "collector": {
                        "enabled": True,
                        "base_url": "http://localhost:8001",
                        "timeout_seconds": 10,
                    },
                    "tikhub": {
                        "enabled": True,
                        "base_url": "https://api.tikhub.dev",
                        "timeout_seconds": 30,
                    },
                    "transcription": {
                        "enabled": True,
                        "engine": "faster_whisper",
                        "active_model": "turbo",
                        "model_dir": ".data/models",
                    },
                },
                "secrets": {
                    "tikhub_api_key": "",
                    "collector_api_key": "",
                    "notion_token": "",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_get_many_reads_yaml_once(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    settings = load_settings(config_path, force_reload=True)
    service = ConfigurationService(settings)
    original = Path.read_text
    calls = 0

    def counted_read_text(path: Path, *args, **kwargs):
        nonlocal calls
        if path == config_path:
            calls += 1
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    values = service.get_many(
        [
            "providers.selected.douyin_aweme_collect",
            "providers.selected.xiaohongshu_aweme_collect",
            "services.tikhub.base_url",
        ]
    )

    assert values == {
        "providers.selected.douyin_aweme_collect": "tikhub",
        "providers.selected.xiaohongshu_aweme_collect": "tikhub",
        "services.tikhub.base_url": "https://api.tikhub.dev",
    }
    assert calls == 1

    service.get_many(
        [
            "providers.selected.wechat_mp_aweme_collect",
            "providers.selected.wechat_channels_aweme_collect",
        ]
    )

    assert calls == 1


def test_patch_validates_and_persists_multiple_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    settings = load_settings(config_path, force_reload=True)
    service = ConfigurationService(settings)

    service.patch(
        {
            "providers.selected.douyin_aweme_collect": "local",
            "providers.tikhub.timeout_seconds": "45",
        }
    )

    settings = load_settings(config_path, force_reload=True)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert settings.providers.selected["douyin_aweme_collect"] == "local"
    assert settings.services.tikhub.timeout_seconds == 45
    assert raw["providers"]["selected"]["douyin_aweme_collect"] == "local"
    assert raw["providers"]["tikhub"]["timeout_seconds"] == 45


def test_business_settings_properties_map_to_config_paths(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    settings = load_settings(config_path, force_reload=True)
    service = ConfigurationService(settings)
    business_settings = service.business_settings()

    business_settings.tikhub_api_key = "secret-value"
    business_settings.douyin_data_provider = "local"

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert raw["providers"]["tikhub"]["api_key"] == "secret-value"
    assert raw["providers"]["selected"]["douyin_aweme_collect"] == "local"
    assert business_settings.tikhub_api_key == "secret-value"
    assert business_settings.douyin_data_provider == "local"
    assert business_settings.show()["tikhub_api_key"] == "***configured***"


def test_business_settings_reject_unknown_property(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    settings = load_settings(config_path, force_reload=True)
    service = ConfigurationService(settings)

    try:
        service.business_settings().unknown_setting = "value"
    except InvalidOperationError as exc:
        assert "unknown setting" in str(exc)
    else:
        raise AssertionError("unknown setting should fail")


def test_business_settings_backfills_provider_defaults_for_legacy_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw.pop("providers")
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    settings = load_settings(config_path, force_reload=True)
    service = ConfigurationService(settings)
    business_settings = service.business_settings()

    assert business_settings.douyin_data_provider == "tikhub"

    business_settings.douyin_data_provider = "local"

    updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert updated["providers"]["selected"]["douyin_aweme_collect"] == "local"


def test_custom_provider_namespace_is_persisted_and_masked(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    settings = load_settings(config_path, force_reload=True)
    service = ConfigurationService(settings)

    shown = service.patch(
        {
            "providers.custom_provider.endpoint": "http://localhost:9000",
            "providers.custom_provider.api_key": "provider-secret",
        }
    )

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["providers"]["custom_provider"] == {
        "endpoint": "http://localhost:9000",
        "api_key": "provider-secret",
    }
    assert shown["providers"]["custom_provider"] == {
        "endpoint": "http://localhost:9000",
        "api_key": "***configured***",
    }
