from __future__ import annotations

from contextlib import contextmanager
from enum import StrEnum
import logging
from pathlib import Path
import re
from threading import RLock
from typing import Any, BinaryIO, Iterator, TextIO

from cjdb_collectors.settings import Settings, load_settings


class LogType(StrEnum):
    PROVIDER_RUNTIME = "provider_runtime"
    PROVIDER_SETUP = "provider_setup"
    WORKER = "worker"
    WORKER_TASKS = "worker_tasks"
    RUNTIME = "runtime"


class LoggerService:
    _loggers: dict[str, logging.Logger] = {}
    _lock = RLock()
    _settings: Settings | None = None
    _logs_dir: Path | None = None

    def __init__(
        self,
        settings: Settings | str | Path | None = None,
        *,
        logs_dir: str | Path | None = None,
    ) -> None:
        # Compatibility shim for older call sites/tests. LoggerService is a
        # process-level service; new code should call class methods directly.
        if isinstance(settings, (str, Path)) and logs_dir is None:
            logs_dir = settings
            settings = None
        if settings is not None or logs_dir is not None:
            self.configure(settings=settings, logs_dir=logs_dir)

    @classmethod
    def configure(
        cls,
        *,
        settings: Settings | None = None,
        logs_dir: str | Path | None = None,
    ) -> None:
        cls._settings = settings
        cls._logs_dir = Path(logs_dir) if logs_dir is not None else None

    @classmethod
    def logs_dir(cls, settings: Settings | None = None) -> Path:
        if settings is not None:
            return Path(settings.app.logs_dir)
        if cls._logs_dir is not None:
            return cls._logs_dir
        return Path((cls._settings or load_settings()).app.logs_dir)

    @classmethod
    def get_log_path(
        cls,
        log_type: LogType | str,
        instance: Any = None,
        *,
        settings: Settings | None = None,
    ) -> Path:
        selected_type = LogType(log_type)
        logs_dir = cls.logs_dir(settings)
        if selected_type == LogType.PROVIDER_RUNTIME:
            cls._require_instance(selected_type, instance)
            return logs_dir / f"provider-{cls._instance_identifier(instance)}.log"
        if selected_type == LogType.PROVIDER_SETUP:
            cls._require_instance(selected_type, instance)
            return logs_dir / f"provider-{cls._instance_identifier(instance)}-setup.log"
        if selected_type == LogType.WORKER:
            return logs_dir / "worker.log"
        if selected_type == LogType.WORKER_TASKS:
            suffix = (
                f"-{cls._safe_name(str(instance))}"
                if instance is not None
                else ""
            )
            return logs_dir / f"worker-tasks{suffix}.log"
        if selected_type == LogType.RUNTIME:
            cls._require_instance(selected_type, instance)
            return logs_dir / f"{cls._safe_name(str(instance))}.log"
        raise ValueError(f"unknown log type: {log_type}")

    @classmethod
    def get_logger(
        cls,
        log_type: LogType | str | Any,
        instance: Any = None,
        *,
        settings: Settings | None = None,
    ) -> logging.Logger:
        selected_type = LogType(log_type)
        path = cls.get_log_path(selected_type, instance=instance, settings=settings)
        logger_name = cls._logger_name(selected_type, instance, path)
        with cls._lock:
            logger = cls._loggers.get(logger_name)
            if logger is not None:
                return logger
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            logger.addHandler(handler)
            cls._loggers[logger_name] = logger
            return logger

    @staticmethod
    def append_line(path: Path, message: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            print(message, file=handle)

    @staticmethod
    @contextmanager
    def open_binary_append(path: Path) -> Iterator[BinaryIO]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            yield handle

    @staticmethod
    @contextmanager
    def open_text_append(path: Path) -> Iterator[TextIO]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            yield handle

    @staticmethod
    def read_page(
        log_type_or_path: LogType | str | Path,
        instance: Any = None,
        *,
        before: int | None,
        limit: int,
        settings: Settings | None = None,
    ) -> dict[str, Any]:
        path = (
            log_type_or_path
            if isinstance(log_type_or_path, Path)
            else LoggerService.get_log_path(
                log_type_or_path,
                instance=instance,
                settings=settings,
            )
        )
        if not path.exists():
            return {
                "path": str(path),
                "lines": [],
                "start": 0,
                "end": 0,
                "total": 0,
                "has_more": False,
            }
        file_size = path.stat().st_size
        end = min(before if before is not None else file_size, file_size)
        if end <= 0:
            return {
                "path": str(path),
                "lines": [],
                "start": 0,
                "end": 0,
                "total": file_size,
                "has_more": False,
            }

        chunk_size = 64 * 1024
        cursor = end
        newline_count = 0
        chunks: list[bytes] = []
        with path.open("rb") as handle:
            while cursor > 0 and newline_count <= limit:
                size = min(chunk_size, cursor)
                cursor -= size
                handle.seek(cursor)
                chunk = handle.read(size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")

            data = b"".join(reversed(chunks))
            base_offset = cursor
            if base_offset > 0:
                handle.seek(base_offset - 1)
                starts_on_boundary = handle.read(1) == b"\n"
                if not starts_on_boundary:
                    first_newline = data.find(b"\n")
                    if first_newline >= 0:
                        base_offset += first_newline + 1
                        data = data[first_newline + 1 :]

        entries: list[dict[str, Any]] = []
        offset = base_offset
        for raw_line in data.splitlines(keepends=True):
            entries.append(
                {
                    "index": offset,
                    "text": raw_line.rstrip(b"\r\n").decode(
                        "utf-8",
                        errors="replace",
                    ),
                }
            )
            offset += len(raw_line)
        entries = entries[-limit:]
        start = entries[0]["index"] if entries else end
        return {
            "path": str(path),
            "lines": entries,
            "start": start,
            "end": end,
            "total": file_size,
            "has_more": start > 0,
        }

    @staticmethod
    def _instance_identifier(instance: Any) -> str:
        identifier = getattr(instance, "id", None)
        if identifier is None:
            identifier = getattr(instance, "namespace", None)
        if identifier is None:
            identifier = instance
        return str(identifier)

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "runtime"

    @staticmethod
    def _require_instance(log_type: LogType, instance: Any) -> None:
        if instance is None:
            raise ValueError(f"{log_type.value} log requires an instance")

    @classmethod
    def _logger_name(cls, log_type: LogType, instance: Any, path: Path) -> str:
        namespace = cls._logger_namespace(instance)
        identifier = cls._instance_identifier(instance) if instance is not None else "main"
        return f"cjdb_collectors.{log_type.value}.{namespace}.{identifier}.{path}"

    @staticmethod
    def _logger_namespace(instance: Any) -> str:
        if instance is None:
            return "main"
        if isinstance(instance, str):
            return instance
        return getattr(instance, "namespace", instance.__class__.__name__)


__all__ = ["LoggerService", "LogType"]
