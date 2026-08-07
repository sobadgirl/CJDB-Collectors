from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from cjdb_collectors.settings import MediaServiceSettings, Settings, load_settings

from .base import InvalidOperationError


class BusinessSettings:
    """Business-facing settings mapped to concrete config.yaml paths."""

    _PROPERTY_PATHS = {
        "logs_dir": "app.logs_dir",
        "transcription_model": "services.transcription.active_model",
    }
    _SECRET_PROPERTIES: set[str] = set()

    def __init__(self, config: "SettingsService") -> None:
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
            raise InvalidOperationError(f"unknown settings property: {name}") from exc


class SettingsService:
    """Manage runtime YAML without putting operational settings in SQLite."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._raw_cache: dict[str, Any] | None = None
        self.business_settings_view = BusinessSettings(self)

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
        """Reload settings written by another process."""
        self.settings = load_settings(
            self.settings.config_path,
            force_reload=True,
        )
        self._raw_cache = self._with_runtime_defaults(self._read_raw())

    def get(self, dotted_key: str | None = None) -> Any:
        """Read one settings path or all settings from one cached YAML load."""
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
        return self.business_settings_view

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
            raise InvalidOperationError("settings root must be a mapping")
        return data

    @staticmethod
    def _with_runtime_defaults(data: dict[str, Any]) -> dict[str, Any]:
        data.pop("spider", None)
        data.pop("providers", None)
        services = data.setdefault("services", {})
        if not isinstance(services, dict):
            services = {}
            data["services"] = services
        media = services.setdefault("media", {})
        if not isinstance(media, dict):
            media = {}
            services["media"] = media
        default_media = MediaServiceSettings()
        media.setdefault(
            "transcription_download_dir",
            str(default_media.transcription_download_dir),
        )
        media.setdefault("aweme_download_dir", str(default_media.aweme_download_dir))
        return data

    @staticmethod
    def _parts(dotted_key: str) -> list[str]:
        parts = [part for part in dotted_key.split(".") if part]
        if not parts:
            raise InvalidOperationError("settings key is required")
        return parts

    @classmethod
    def _value_at(cls, data: dict[str, Any], dotted_key: str) -> Any:
        cursor: Any = data
        for part in cls._parts(dotted_key):
            if not isinstance(cursor, dict) or part not in cursor:
                raise InvalidOperationError(f"unknown settings path: {dotted_key}")
            cursor = cursor[part]
        return cursor

    @classmethod
    def _set_value(cls, data: dict[str, Any], dotted_key: str, raw_value: Any) -> None:
        parts = cls._parts(dotted_key)
        cursor: dict[str, Any] = data
        for index, part in enumerate(parts[:-1]):
            child = cursor.get(part)
            allow_dynamic_namespace = parts[0] == "stores" and index >= 1
            if child is None and allow_dynamic_namespace:
                child = {}
                cursor[part] = child
            if not isinstance(child, dict):
                raise InvalidOperationError(f"unknown settings path: {dotted_key}")
            cursor = child
        if parts[-1] not in cursor:
            raise InvalidOperationError(f"unknown settings path: {dotted_key}")
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
