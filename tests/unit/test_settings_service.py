from __future__ import annotations

from pathlib import Path

import yaml

from cjdb_collectors.settings import init_settings_file, load_settings
from cjdb_collectors.services.base import InvalidOperationError
from cjdb_collectors.services.settings import SettingsService


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
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_get_many_reads_yaml_once(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    config = load_settings(config_path, force_reload=True)
    service = SettingsService(config)
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
            "app.logs_dir",
            "services.tikhub.base_url",
            "services.transcription.active_model",
        ]
    )

    assert values == {
        "app.logs_dir": "logs",
        "services.tikhub.base_url": "https://api.tikhub.dev",
        "services.transcription.active_model": "turbo",
    }
    assert calls == 1

    service.get_many(
        [
            "app.data_dir",
            "app.database_path",
        ]
    )

    assert calls == 1


def test_patch_validates_and_persists_multiple_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    config = load_settings(config_path, force_reload=True)
    service = SettingsService(config)

    service.patch(
        {
            "app.logs_dir": "runtime-logs",
            "services.tikhub.timeout_seconds": "45",
        }
    )

    config = load_settings(config_path, force_reload=True)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config.app.logs_dir == (config_path.parent / "runtime-logs").resolve()
    assert config.services.tikhub.timeout_seconds == 45
    assert raw["app"]["logs_dir"] == "runtime-logs"
    assert raw["services"]["tikhub"]["timeout_seconds"] == 45


def test_business_settings_properties_map_to_config_paths(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    config = load_settings(config_path, force_reload=True)
    service = SettingsService(config)
    business_settings = service.business_settings()

    business_settings.logs_dir = "business-logs"
    business_settings.transcription_model = "small"

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert raw["app"]["logs_dir"] == "business-logs"
    assert raw["services"]["transcription"]["active_model"] == "small"
    assert str(business_settings.logs_dir).endswith("business-logs")
    assert business_settings.transcription_model == "small"


def test_business_settings_reject_unknown_property(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    config = load_settings(config_path, force_reload=True)
    service = SettingsService(config)

    try:
        service.business_settings().unknown_setting = "value"
    except InvalidOperationError as exc:
        assert "unknown setting" in str(exc)
    else:
        raise AssertionError("unknown setting should fail")


def test_legacy_provider_config_is_ignored(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    config = load_settings(config_path, force_reload=True)
    service = SettingsService(config)

    assert config.providers.selected == {}
    shown = service.show()
    assert shown["providers"]["selected"] == {}


def test_provider_config_paths_are_no_longer_settings_paths(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    config = load_settings(config_path, force_reload=True)
    service = SettingsService(config)

    try:
        service.patch({"providers.custom_provider.endpoint": "http://localhost:9000"})
    except InvalidOperationError as exc:
        assert "unknown settings path" in str(exc)
    else:
        raise AssertionError("provider config path should fail")


def test_init_settings_file_creates_default_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"

    created = init_settings_file(config_path)

    assert created == config_path.resolve()
    config = load_settings(config_path, force_reload=True)
    assert config.config_path == config_path.resolve()
    assert config.web.host == "127.0.0.1"
    assert config.providers.selected == {}
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "providers" not in raw


def test_init_settings_file_refuses_to_overwrite_existing_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    try:
        init_settings_file(config_path)
    except FileExistsError as exc:
        assert str(config_path.resolve()) in str(exc)
    else:
        raise AssertionError("existing config should require --force")

    assert config_path.read_text(encoding="utf-8") == "version: 1\n"
