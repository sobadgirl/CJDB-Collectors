from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, SecretStr, field_validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecretSettings(StrictModel):
    """Secrets loaded from config.yaml."""

    tikhub_api_key: SecretStr | None = None
    collector_api_key: SecretStr | None = None
    notion_token: SecretStr | None = None

    def resolve(self, reference: str | None) -> str | None:
        if not reference:
            return None
        field_name = reference.strip().lower()
        field_name = field_name.replace("-", "_")
        secret = getattr(self, field_name, None)
        return secret.get_secret_value() if isinstance(secret, SecretStr) else None


class AppSettings(StrictModel):
    data_dir: Path = Path(".data")
    database_path: Path = Path(".data/cjdb-collectors.db")
    logs_dir: Path = Path("logs")


class WebSettings(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


class WorkerSettings(StrictModel):
    scan_interval_seconds: float = Field(default=1, gt=0)
    idle_scan_interval_seconds: float = Field(default=5, gt=0)
    terminate_grace_seconds: float = Field(default=10, ge=0)


class WorkerTaskSettings(StrictModel):
    process_limit: int = Field(default=1, ge=0)
    timeout_seconds: PositiveInt
    retry_limit: int = Field(default=3, ge=0)
    retry_delay_seconds: int = Field(default=30, ge=0)


class WorkerTasksSettings(StrictModel):
    data_collect: WorkerTaskSettings = WorkerTaskSettings(process_limit=4, timeout_seconds=300)
    media_download: WorkerTaskSettings = WorkerTaskSettings(
        process_limit=2, timeout_seconds=1800
    )
    video_transcription: WorkerTaskSettings = WorkerTaskSettings(
        process_limit=1, timeout_seconds=7200, retry_limit=2, retry_delay_seconds=60
    )
    comment_collect: WorkerTaskSettings = WorkerTaskSettings(
        process_limit=4, timeout_seconds=600
    )
    data_sync: WorkerTaskSettings = WorkerTaskSettings(process_limit=2, timeout_seconds=600)


class ProvidersSettings(StrictModel):
    model_config = ConfigDict(extra="allow")

    selected: dict[str, str] = Field(
        default_factory=lambda: {
            "douyin_aweme_collect": "tikhub",
            "xiaohongshu_aweme_collect": "tikhub",
            "wechat_channels_aweme_collect": "tikhub",
            "wechat_mp_aweme_collect": "tikhub",
            "xiaohongshu_comment_collect": "tikhub",
            "account_collect": "http_collector",
            "video_transcription": "faster_whisper",
        }
    )
    tikhub: dict[str, Any] = Field(default_factory=dict)
    http_collector: dict[str, Any] = Field(default_factory=dict)
    faster_whisper: dict[str, Any] = Field(default_factory=dict)


class CollectorServiceSettings(StrictModel):
    enabled: bool = True
    base_url: str = "http://localhost:8001"
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=10, gt=0)


class TikHubServiceSettings(StrictModel):
    enabled: bool = True
    base_url: str = "https://api.tikhub.dev"
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=30, gt=0)


class TranscriptionServiceSettings(StrictModel):
    enabled: bool = True
    engine: str = "faster_whisper"
    active_model: str = "turbo"
    model_dir: Path | None = None
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = "zh"
    vad_filter: bool = True
    word_timestamps: bool = False
    browse_roots: list[Path] = Field(
        default_factory=lambda: [
            Path("~/Movies"),
            Path("~/Downloads"),
            Path(".data/media"),
        ]
    )
    allowed_video_extensions: set[str] = {
        "avi",
        "flv",
        "m4v",
        "mkv",
        "mov",
        "mp4",
        "mpeg",
        "mpg",
        "ts",
        "webm",
    }

    @field_validator("model_dir", mode="before")
    @classmethod
    def empty_model_dir_is_default_cache(cls, value: Any) -> Any:
        if value == "":
            return None
        return value


class ServicesSettings(StrictModel):
    collector: CollectorServiceSettings = CollectorServiceSettings()
    tikhub: TikHubServiceSettings = TikHubServiceSettings()
    transcription: TranscriptionServiceSettings = TranscriptionServiceSettings()


class Settings(StrictModel):
    version: int = 1
    app: AppSettings = AppSettings()
    web: WebSettings = WebSettings()
    worker: WorkerSettings = WorkerSettings()
    worker_tasks: WorkerTasksSettings = WorkerTasksSettings()
    providers: ProvidersSettings = ProvidersSettings()
    stores: dict[str, dict[str, Any]] = Field(default_factory=dict)
    services: ServicesSettings = ServicesSettings()
    secrets: SecretSettings = SecretSettings()
    config_path: Path = Field(default=DEFAULT_CONFIG_PATH, exclude=True)

    def resolve_paths(self) -> "Settings":
        base = self.config_path.parent
        updates: dict[str, Any] = {}
        app = self.app
        updates["app"] = app.model_copy(
            update={
                "data_dir": _resolve_path(app.data_dir, base),
                "database_path": _resolve_path(app.database_path, base),
                "logs_dir": _resolve_path(app.logs_dir, base),
            }
        )
        transcription_updates: dict[str, Any] = {
            "browse_roots": [
                _resolve_path(path, base)
                for path in self.services.transcription.browse_roots
            ],
        }
        if self.services.transcription.model_dir is not None:
            transcription_updates["model_dir"] = _resolve_path(
                self.services.transcription.model_dir, base
            )
        transcription = self.services.transcription.model_copy(
            update=transcription_updates
        )
        updates["services"] = self.services.model_copy(
            update={"transcription": transcription}
        )
        return self.model_copy(update=updates)


def _resolve_path(path: Path, base: Path) -> Path:
    expanded = path.expanduser()
    return expanded.resolve() if expanded.is_absolute() else (base / expanded).resolve()


@lru_cache(maxsize=8)
def _load_settings_cached(path_string: str) -> Settings:
    path = Path(path_string).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Old development configs may still contain the retired Spider section.
    raw.pop("spider", None)
    settings = Settings.model_validate(
        {
            **raw,
            "config_path": path,
        }
    )
    settings.providers.selected = {
        **ProvidersSettings().selected,
        **settings.providers.selected,
    }
    settings.services.collector.api_key = settings.secrets.collector_api_key
    settings.services.tikhub.api_key = settings.secrets.tikhub_api_key
    if settings.providers.tikhub:
        tikhub_config = settings.providers.tikhub
        service_updates: dict[str, Any] = {}
        if "api_key" in tikhub_config:
            service_updates["api_key"] = SecretStr(str(tikhub_config["api_key"]))
        for key in ("base_url", "timeout_seconds"):
            if key in tikhub_config:
                service_updates[key] = tikhub_config[key]
        if service_updates:
            settings.services.tikhub = settings.services.tikhub.model_copy(
                update=service_updates
            )
    if settings.providers.faster_whisper:
        whisper_config = settings.providers.faster_whisper
        transcription_updates: dict[str, Any] = {}
        if "model" in whisper_config:
            transcription_updates["active_model"] = whisper_config["model"]
        if whisper_config.get("model_dir"):
            transcription_updates["model_dir"] = Path(whisper_config["model_dir"])
        for key in ("device", "language"):
            if key in whisper_config:
                transcription_updates[key] = whisper_config[key]
        if transcription_updates:
            settings.services.transcription = (
                settings.services.transcription.model_copy(
                    update=transcription_updates
                )
            )
    if settings.providers.http_collector:
        collector_config = settings.providers.http_collector
        collector_updates: dict[str, Any] = {}
        if "api_key" in collector_config:
            collector_updates["api_key"] = SecretStr(
                str(collector_config["api_key"])
            )
        for key in ("base_url", "timeout_seconds"):
            if key in collector_config:
                collector_updates[key] = collector_config[key]
        if collector_updates:
            settings.services.collector = settings.services.collector.model_copy(
                update=collector_updates
            )
    return settings.resolve_paths()


def load_settings(
    path: str | Path | None = None, *, force_reload: bool = False
) -> Settings:
    """Load YAML runtime settings from config.yaml."""
    if force_reload:
        _load_settings_cached.cache_clear()
    selected = Path(path or os.environ.get("CJDB_CONFIG") or DEFAULT_CONFIG_PATH)
    return _load_settings_cached(str(selected.expanduser().resolve()))
