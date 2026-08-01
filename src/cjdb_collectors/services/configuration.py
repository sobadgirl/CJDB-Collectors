from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from cjdb_collectors.config import ProvidersSettings, Settings, load_settings

from .base import InvalidOperationError


class BusinessSettings:
    """Business-facing settings mapped to concrete config.yaml paths."""

    _PROPERTY_PATHS = {
        "tikhub_api_key": "providers.tikhub.api_key",
        "douyin_data_provider": "providers.selected.douyin_aweme_collect",
        "xiaohongshu_data_provider": "providers.selected.xiaohongshu_aweme_collect",
        "wechat_mp_data_provider": "providers.selected.wechat_mp_aweme_collect",
        "wechat_channels_data_provider": "providers.selected.wechat_channels_aweme_collect",
        "xiaohongshu_comment_provider": "providers.selected.xiaohongshu_comment_collect",
        "logs_dir": "app.logs_dir",
        "transcription_model": "services.transcription.active_model",
        "transcription_provider": "providers.selected.video_transcription",
    }
    _SECRET_PROPERTIES = {"tikhub_api_key"}

    def __init__(self, config: "ConfigurationService") -> None:
        object.__setattr__(self, "_config", config)

    def __getattr__(self, name: str) -> Any:
        path = self._path(name)
        if name in self._SECRET_PROPERTIES:
            return self._config.get_secret(path)
        return self._config.get(path)

    def __setattr__(self, name: str, value: Any) -> None:
        self._config.set(self._path(name), value)

    def show(self) -> dict[str, Any]:
        return {
            name: "***configured***" if name in self._SECRET_PROPERTIES and value else value
            for name, value in self.values().items()
        }

    def values(self) -> dict[str, Any]:
        data = self._config._raw()
        return {
            name: (
                self._config._secret_value_at(data, path)
                if name in self._SECRET_PROPERTIES
                else self._config._value_at(data, path)
            )
            for name, path in self._PROPERTY_PATHS.items()
        }

    def patch(self, values: dict[str, Any]) -> dict[str, Any]:
        changes = {self._path(name): value for name, value in values.items()}
        self._config.patch(changes)
        return self.show()

    @classmethod
    def keys(cls) -> list[str]:
        return list(cls._PROPERTY_PATHS)

    @classmethod
    def _path(cls, name: str) -> str:
        try:
            return cls._PROPERTY_PATHS[name]
        except KeyError as exc:
            raise InvalidOperationError(f"unknown setting: {name}") from exc


class ConfigurationService:
    """Manage runtime YAML without putting operational config in SQLite."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._raw_cache: dict[str, Any] | None = None
        self.settings_view = BusinessSettings(self)

    def show(self) -> dict[str, Any]:
        self.refresh()
        settings = self.settings
        value = settings.model_dump(mode="json", exclude={"config_path"})
        for service_name in ("collector", "tikhub"):
            service = getattr(settings.services, service_name)
            value["services"][service_name]["api_key"] = (
                "***configured***" if service.api_key else None
            )
        value["secrets"] = {
            key: "***configured***" if item else None
            for key, item in settings.secrets.model_dump().items()
        }
        for root_key in ("providers", "stores"):
            root_value = value.get(root_key)
            if isinstance(root_value, dict):
                value[root_key] = self._mask_sensitive(root_value)
        return value

    def refresh(self) -> None:
        """Reload config written by another process."""
        self.settings = load_settings(
            self.settings.config_path,
            force_reload=True,
        )
        self._raw_cache = self._with_runtime_defaults(self._read_raw())

    def get(self, dotted_key: str | None = None) -> Any:
        """Read one config path or the full config from one cached YAML load."""
        data = self._raw()
        if not dotted_key:
            return self.show()
        return self._value_at(data, dotted_key)

    def get_many(self, dotted_keys: list[str]) -> dict[str, Any]:
        data = self._raw()
        return {key: self._value_at(data, key) for key in dotted_keys}

    def get_secret(self, dotted_key: str) -> str | None:
        return self._secret_value_at(self._raw(), dotted_key)

    def business_settings(self) -> BusinessSettings:
        return self.settings_view

    def set(self, dotted_key: str, raw_value: Any) -> dict[str, Any]:
        return self.patch({dotted_key: raw_value})

    def patch(self, changes: dict[str, Any]) -> dict[str, Any]:
        if not changes:
            return self.show()
        path = Path(self.settings.config_path)
        data = self._with_runtime_defaults(self._read_raw())
        for dotted_key, raw_value in changes.items():
            self._set_value(data, dotted_key, raw_value)
        # Validate the complete shape before replacing the active file.
        Settings.model_validate({**data, "config_path": path}).resolve_paths()
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        temporary.replace(path)
        self.settings = load_settings(path, force_reload=True)
        self._raw_cache = data
        return self.show()

    def _raw(self) -> dict[str, Any]:
        if self._raw_cache is None:
            self._raw_cache = self._with_runtime_defaults(self._read_raw())
        return self._raw_cache

    def _read_raw(self) -> dict[str, Any]:
        path = Path(self.settings.config_path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise InvalidOperationError("config root must be a mapping")
        return data

    @staticmethod
    def _with_runtime_defaults(data: dict[str, Any]) -> dict[str, Any]:
        data.pop("spider", None)
        providers = data.setdefault("providers", {})
        if not isinstance(providers, dict):
            raise InvalidOperationError("config providers must be a mapping")
        selected = providers.setdefault("selected", {})
        if not isinstance(selected, dict):
            raise InvalidOperationError("config providers.selected must be a mapping")
        for key, value in ProvidersSettings().selected.items():
            selected.setdefault(key, value)
        legacy_config = providers.pop("config", {})
        if legacy_config and not isinstance(legacy_config, dict):
            raise InvalidOperationError("config providers.config must be a mapping")
        for namespace in ("tikhub", "http_collector", "faster_whisper"):
            legacy_values = legacy_config.get(namespace, {}) if legacy_config else {}
            if legacy_values and not isinstance(legacy_values, dict):
                raise InvalidOperationError(
                    f"config providers.config.{namespace} must be a mapping"
                )
            current = providers.setdefault(namespace, legacy_values or {})
            if not isinstance(current, dict):
                raise InvalidOperationError(f"config providers.{namespace} must be a mapping")
        providers["tikhub"].setdefault("base_url", "https://api.tikhub.dev")
        providers["tikhub"].setdefault("timeout_seconds", 30)
        secrets = data.get("secrets", {})
        if (
            isinstance(secrets, dict)
            and secrets.get("tikhub_api_key")
            and not providers["tikhub"].get("api_key")
        ):
            providers["tikhub"]["api_key"] = secrets["tikhub_api_key"]
        services = data.get("services", {})
        collector = services.get("collector", {}) if isinstance(services, dict) else {}
        if not isinstance(collector, dict):
            collector = {}
        providers["http_collector"].setdefault(
            "base_url", collector.get("base_url", "http://localhost:8001")
        )
        providers["http_collector"].setdefault(
            "timeout_seconds", collector.get("timeout_seconds", 10)
        )
        if (
            isinstance(secrets, dict)
            and secrets.get("collector_api_key")
            and not providers["http_collector"].get("api_key")
        ):
            providers["http_collector"]["api_key"] = secrets["collector_api_key"]
        transcription = (
            services.get("transcription", {}) if isinstance(services, dict) else {}
        )
        if not isinstance(transcription, dict):
            transcription = {}
        providers["faster_whisper"].setdefault(
            "model", transcription.get("active_model", "turbo")
        )
        providers["faster_whisper"].setdefault(
            "model_dir", transcription.get("model_dir") or ""
        )
        providers["faster_whisper"].setdefault(
            "device", transcription.get("device", "auto")
        )
        providers["faster_whisper"].setdefault(
            "language", transcription.get("language", "zh")
        )
        stores = data.setdefault("stores", {})
        if not isinstance(stores, dict):
            raise InvalidOperationError("config stores must be a mapping")
        return data

    @staticmethod
    def _parts(dotted_key: str) -> list[str]:
        parts = [part for part in dotted_key.split(".") if part]
        if not parts:
            raise InvalidOperationError("config key is required")
        return parts

    @classmethod
    def _value_at(cls, data: dict[str, Any], dotted_key: str) -> Any:
        cursor: Any = data
        for part in cls._parts(dotted_key):
            if not isinstance(cursor, dict) or part not in cursor:
                raise InvalidOperationError(f"unknown config path: {dotted_key}")
            cursor = cursor[part]
        return cursor

    @classmethod
    def _set_value(cls, data: dict[str, Any], dotted_key: str, raw_value: Any) -> None:
        parts = cls._parts(dotted_key)
        cursor: dict[str, Any] = data
        for index, part in enumerate(parts[:-1]):
            child = cursor.get(part)
            allow_dynamic_namespace = (
                parts[0] == "stores" and index >= 1
            ) or (
                parts[0] == "providers"
                and parts[1] != "selected"
                and index >= 1
            )
            if child is None and allow_dynamic_namespace:
                child = {}
                cursor[part] = child
            if not isinstance(child, dict):
                raise InvalidOperationError(f"unknown config path: {dotted_key}")
            cursor = child
        allow_provider_parameter = (
            len(parts) >= 3 and parts[0] == "providers" and parts[1] != "selected"
        )
        allow_store_parameter = len(parts) >= 3 and parts[0] == "stores"
        if (
            parts[-1] not in cursor
            and not allow_provider_parameter
            and not allow_store_parameter
        ):
            raise InvalidOperationError(f"unknown config path: {dotted_key}")
        cursor[parts[-1]] = (
            yaml.safe_load(raw_value) if isinstance(raw_value, str) else raw_value
        )

    @classmethod
    def _secret_value_at(cls, data: dict[str, Any], dotted_key: str) -> str | None:
        value = cls._value_at(data, dotted_key)
        return str(value) if value else None

    @classmethod
    def _mask_sensitive(cls, value: Any, key: str = "") -> Any:
        normalized_key = key.lower().replace("-", "_")
        sensitive = (
            normalized_key
            and not normalized_key.endswith("_ref")
            and any(
                marker in normalized_key
                for marker in (
                    "password",
                    "token",
                    "api_key",
                    "secret",
                    "credential",
                )
            )
        )
        if sensitive:
            return "***configured***" if value else value
        if isinstance(value, dict):
            return {
                child_key: cls._mask_sensitive(child, child_key)
                for child_key, child in value.items()
            }
        if isinstance(value, list):
            return [cls._mask_sensitive(child) for child in value]
        return value
