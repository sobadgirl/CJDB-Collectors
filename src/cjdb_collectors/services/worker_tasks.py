from __future__ import annotations

import os
import logging
import signal
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlmodel import select
import psutil

from cjdb_collectors.settings import PROJECT_ROOT, Settings
from cjdb_collectors.models import WorkerTask
from cjdb_collectors.models import WorkerTaskType

from .base import NotFoundError, SessionFactory, as_uuid
from .logger import LoggerService, LogType

logger = logging.getLogger(__name__)


class WorkerService:
    def __init__(
        self,
        session_factory: SessionFactory,
        settings: Settings | None = None,
        logger_service: LoggerService | None = None,
    ) -> None:
        self._session = session_factory
        self.settings = settings
        if settings is not None:
            LoggerService.configure(settings=settings)
        self.logger_service = logger_service or LoggerService

    def list(self, task_type: str | None = None) -> list[WorkerTask]:
        with self._session() as session:
            statement = select(WorkerTask)
            if task_type:
                statement = statement.where(
                    WorkerTask.task_type == WorkerTaskType(task_type)
                )
            return list(
                session.exec(statement.order_by(WorkerTask.started_at.desc())).all()
            )

    def get(self, worker_task_id: UUID | str) -> WorkerTask:
        with self._session() as session:
            item = session.get(WorkerTask, as_uuid(worker_task_id))
            if not item:
                raise NotFoundError("worker task not found")
            return item

    def stop(self, worker_task_id: UUID | str) -> dict:
        item = self.get(worker_task_id)
        if item.pid:
            try:
                process = psutil.Process(item.pid)
                actual_started_at = datetime.fromtimestamp(
                    process.create_time(), tz=timezone.utc
                )
                expected_started_at = item.process_started_at
                if expected_started_at is None:
                    return {
                        "requested": False,
                        "id": str(item.id),
                        "reason": "process start time is unavailable",
                    }
                if expected_started_at.tzinfo is None:
                    expected_started_at = expected_started_at.replace(
                        tzinfo=timezone.utc
                    )
                if abs((actual_started_at - expected_started_at).total_seconds()) > 2:
                    return {
                        "requested": False,
                        "id": str(item.id),
                        "reason": "PID has been reused",
                    }
                if item.process_group_id:
                    os.killpg(item.process_group_id, signal.SIGTERM)
                else:
                    os.kill(item.pid, signal.SIGTERM)
            except (ProcessLookupError, psutil.NoSuchProcess):
                pass
        return {"requested": True, "id": str(item.id)}

    def health(self) -> dict:
        pid = self._worker_pid()
        tasks = self.list()
        by_type = Counter(str(task.task_type.value) for task in tasks)
        heartbeat_at = self._worker_heartbeat_at()
        now = datetime.now(timezone.utc)
        stale_after = (
            ((self.settings.worker.idle_scan_interval_seconds * 2) + 5)
            if self.settings
            else 15
        )
        heartbeat_age_seconds = (
            (now - heartbeat_at).total_seconds() if heartbeat_at else None
        )
        heartbeat_stale = (
            heartbeat_age_seconds is None or heartbeat_age_seconds > stale_after
        )
        worker_running = bool(pid and self._pid_alive(pid))
        limits = (
            {
                task_type.value: getattr(
                    self.settings.worker_tasks, task_type.value
                ).process_limit
                for task_type in WorkerTaskType
            }
            if self.settings
            else {task_type.value: 0 for task_type in WorkerTaskType}
        )
        return {
            "status": "running" if worker_running else "stopped",
            "pid": pid,
            "running": len(tasks),
            "running_by_type": {
                task_type.value: by_type.get(task_type.value, 0)
                for task_type in WorkerTaskType
            },
            "limits": limits,
            "heartbeat_at": heartbeat_at.isoformat() if heartbeat_at else None,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "heartbeat_stale": worker_running and heartbeat_stale,
        }

    @property
    def _pid_path(self) -> Path:
        if not self.settings:
            raise RuntimeError("worker settings are unavailable")
        return Path(self.settings.app.data_dir) / "worker.pid"

    @property
    def _log_path(self) -> Path:
        if self.logger_service is None:
            raise RuntimeError("logger service is unavailable")
        return self.logger_service.get_log_path(LogType.WORKER)

    def _worker_pid(self) -> int | None:
        try:
            return int(self._pid_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError, OSError):
            return None

    def _worker_heartbeat_at(self) -> datetime | None:
        if not self.settings:
            return None
        path = Path(self.settings.app.data_dir) / "worker.heartbeat"
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    def start_worker(self) -> dict:
        existing = self._worker_pid()
        if existing and self._pid_alive(existing):
            logger.info("Worker 启动请求忽略：已有运行进程 pid=%s", existing)
            self._append_worker_log(f"Worker 启动请求忽略：已有运行进程 pid={existing}")
            return {"status": "running", "pid": existing}
        logger.info("Worker 启动请求：log_path=%s", self._log_path)
        self._append_worker_log("Worker 启动请求")
        self._pid_path.parent.mkdir(parents=True, exist_ok=True)
        self._pid_path.unlink(missing_ok=True)
        with self.logger_service.open_binary_append(self._log_path) as log:
            process = subprocess.Popen(
                [
                    str(PROJECT_ROOT / "cjdb"),
                    "worker",
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                start_new_session=True,
                close_fds=True,
            )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pid = self._worker_pid()
            if pid:
                logger.info("Worker 启动成功：pid=%s", pid)
                self._append_worker_log(f"Worker 启动成功：pid={pid}")
                return {"status": "running", "pid": pid}
            if process.poll() is not None:
                logger.error("Worker 启动失败：launcher_pid=%s", process.pid)
                self._append_worker_log(f"Worker 启动失败：launcher_pid={process.pid}")
                return {
                    "status": "failed",
                    "pid": None,
                    "launcher_pid": process.pid,
                }
            time.sleep(0.1)
        logger.info("Worker 启动中：launcher_pid=%s", process.pid)
        self._append_worker_log(f"Worker 启动中：launcher_pid={process.pid}")
        return {"status": "starting", "pid": None, "launcher_pid": process.pid}

    def stop_worker(self) -> dict:
        pid = self._worker_pid()
        if not pid or not self._pid_alive(pid):
            self._pid_path.unlink(missing_ok=True)
            logger.info("Worker 停止请求忽略：没有运行进程")
            self._append_worker_log("Worker 停止请求忽略：没有运行进程")
            return {"status": "stopped", "pid": None}
        logger.info("Worker 停止请求：pid=%s", pid)
        self._append_worker_log(f"Worker 停止请求：pid={pid}")
        os.kill(pid, signal.SIGTERM)
        self._pid_path.unlink(missing_ok=True)
        return {"status": "stopping", "pid": pid}

    def restart_worker(self) -> dict:
        logger.info("Worker 重启请求")
        self._append_worker_log("Worker 重启请求")
        self.stop_worker()
        return self.start_worker()

    def _append_worker_log(self, message: str) -> None:
        if not self.settings:
            return
        self.logger_service.append_line(
            self._log_path,
            f"{datetime.now(timezone.utc).isoformat()} INFO {message}",
        )
