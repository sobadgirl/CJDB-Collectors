from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import timedelta, timezone
from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4

import psutil
from sqlmodel import Session, select

from cjdb_collectors.config import PROJECT_ROOT, Settings, load_settings
from cjdb_collectors.db import engine as default_engine
from cjdb_collectors.models import (
    Account,
    AccountDataStorerSync,
    Aweme,
    AwemeDataStorerSync,
    DataStorer,
    DataStorerStatus,
    TaskStatus,
    VideoTranscription,
    WorkerSubject,
    WorkerTask,
    WorkerTaskStatus,
    WorkerTaskType,
)
from cjdb_collectors.services import ServiceContainer, build_services
from cjdb_collectors.services.base import now_utc


class SlotResult(str, Enum):
    DISPATCHED = "dispatched"
    HANDLED = "handled"
    EMPTY = "empty"
    BLOCKED = "blocked"


@dataclass(slots=True)
class WorkerSlot:
    name: str
    cooldown_on_empty: int = 0
    cooldown_remaining: int = 0


class Worker:
    """Round-robin worker.

    The main process only claims one task, starts one child process, or handles
    one maintenance item per loop. Real work happens in `cjdb worker run-task`.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        services: ServiceContainer | None = None,
        db_engine=None,
    ) -> None:
        self.settings = settings or load_settings()
        self.engine = db_engine or default_engine
        self.services = services or build_services(self.settings, db_engine=self.engine)
        self.processes: dict[UUID, subprocess.Popen] = {}
        self._stopping = False
        self._slot_index = 0
        self._slots = [
            WorkerSlot("data_collect"),
            WorkerSlot("timeout"),
            WorkerSlot("comment_collect", cooldown_on_empty=2),
            WorkerSlot("media_download", cooldown_on_empty=1),
            WorkerSlot("timeout"),
            WorkerSlot("video_transcription", cooldown_on_empty=2),
            WorkerSlot("data_sync", cooldown_on_empty=1),
            WorkerSlot("reset_stale", cooldown_on_empty=5),
        ]

    def stop(self) -> None:
        self._stopping = True

    def run_forever(self) -> None:
        while not self._stopping:
            self._write_heartbeat()
            handled = self.run_once()
            delay = (
                self.settings.worker.scan_interval_seconds
                if handled
                else self.settings.worker.idle_scan_interval_seconds
            )
            time.sleep(delay)
            self._reload_settings()

    def _write_heartbeat(self) -> None:
        path = Path(self.settings.app.data_dir) / "worker.heartbeat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(now_utc().isoformat(), encoding="utf-8")

    def run_once(self) -> bool:
        self._reap_finished_processes()
        slot = self._next_slot()
        if slot.cooldown_remaining > 0:
            slot.cooldown_remaining -= 1
            self._advance_slot()
            return False

        result = self._handle_slot(slot.name)
        if result == SlotResult.EMPTY:
            slot.cooldown_remaining = slot.cooldown_on_empty
        elif result == SlotResult.BLOCKED:
            slot.cooldown_remaining = 1
        else:
            slot.cooldown_remaining = 0
        self._advance_slot()
        return result in {SlotResult.DISPATCHED, SlotResult.HANDLED}

    def check_and_pull_data(self) -> SlotResult:
        return self._dispatch_one_worker_task(WorkerTaskType.DATA_COLLECT)

    def check_and_run_comments(self) -> SlotResult:
        return self._dispatch_one_worker_task(WorkerTaskType.COMMENT_COLLECT)

    def check_and_download_media(self) -> SlotResult:
        return self._dispatch_one_worker_task(WorkerTaskType.MEDIA_DOWNLOAD)

    def check_and_transcribe(self) -> SlotResult:
        return self._dispatch_one_worker_task(WorkerTaskType.VIDEO_TRANSCRIPTION)

    def check_and_sync_data(self) -> SlotResult:
        return self._dispatch_one_worker_task(WorkerTaskType.DATA_SYNC)

    def process_timeout(self) -> SlotResult:
        with Session(self.engine) as session:
            worker_task = session.exec(
                select(WorkerTask)
                .where(WorkerTask.timeout_at <= now_utc())
                .order_by(WorkerTask.timeout_at)
                .limit(1)
            ).first()
            if not worker_task:
                return SlotResult.EMPTY
            worker_task.status = WorkerTaskStatus.TIMEOUT
            session.add(worker_task)
            self._terminate_worker_task(worker_task)
            self._mark_timeout_or_failure(session, worker_task, timed_out=True)
            session.delete(worker_task)
            session.commit()
        self.processes.pop(worker_task.id, None)
        return SlotResult.HANDLED

    def reset_stale(self) -> SlotResult:
        with Session(self.engine) as session:
            candidates = session.exec(
                select(WorkerTask)
                .where(WorkerTask.status == WorkerTaskStatus.RUNNING)
                .order_by(WorkerTask.started_at)
                .limit(20)
            ).all()
            for worker_task in candidates:
                if self._worker_task_alive(worker_task):
                    continue
                self._mark_timeout_or_failure(session, worker_task, timed_out=False)
                session.delete(worker_task)
                session.commit()
                self.processes.pop(worker_task.id, None)
                return SlotResult.HANDLED
        return SlotResult.EMPTY

    def _handle_slot(self, name: str) -> SlotResult:
        handlers = {
            "data_collect": self.check_and_pull_data,
            "comment_collect": self.check_and_run_comments,
            "media_download": self.check_and_download_media,
            "video_transcription": self.check_and_transcribe,
            "data_sync": self.check_and_sync_data,
            "timeout": self.process_timeout,
            "reset_stale": self.reset_stale,
        }
        return handlers[name]()

    def _dispatch_one_worker_task(self, task_type: WorkerTaskType) -> SlotResult:
        config = getattr(self.settings.worker_tasks, task_type.value)
        if config.process_limit <= 0:
            return SlotResult.BLOCKED
        if self._running_count(task_type) >= config.process_limit:
            return SlotResult.BLOCKED
        worker_task_id = self._claim(task_type, config.timeout_seconds)
        if worker_task_id is None:
            return SlotResult.EMPTY
        process = self._start_worker_process(worker_task_id)
        with Session(self.engine) as session:
            worker_task = session.get(WorkerTask, worker_task_id)
            if worker_task:
                worker_task.pid = process.pid
                worker_task.process_group_id = process.pid
                worker_task.process_started_at = now_utc()
                worker_task.status = WorkerTaskStatus.RUNNING
                session.add(worker_task)
                session.commit()
        self.processes[worker_task_id] = process
        return SlotResult.DISPATCHED

    def _start_worker_process(self, worker_task_id: UUID) -> subprocess.Popen:
        command = [
            str(PROJECT_ROOT / "cjdb"),
            "worker",
            "run-task",
            "--worker-task-id",
            str(worker_task_id),
            "--config",
            str(self.settings.config_path),
        ]
        log_path = Path(self.settings.app.logs_dir) / "worker-tasks.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("ab")
        try:
            return subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log.close()

    def _running_count(self, task_type: WorkerTaskType) -> int:
        with Session(self.engine) as session:
            return len(
                session.exec(
                    select(WorkerTask.id).where(WorkerTask.task_type == task_type)
                ).all()
            )

    @staticmethod
    def _due(status, next_run_at) -> bool:
        if status == TaskStatus.PENDING:
            return True
        if status != TaskStatus.RETRY_WAIT:
            return False
        return next_run_at is None or next_run_at <= now_utc()

    def _claim(self, task_type: WorkerTaskType, timeout_seconds: int) -> UUID | None:
        with Session(self.engine) as session:
            found = self._find_candidate(session, task_type)
            if not found:
                return None
            item, subject_type, prefix = found
            token = uuid4().hex
            status_field = f"{prefix}_status" if prefix else "status"
            run_token_field = f"{prefix}_run_token" if prefix else "run_token"
            started_field = f"{prefix}_started_at" if prefix else "started_at"
            heartbeat_field = f"{prefix}_heartbeat_at" if prefix else "heartbeat_at"
            attempt_field = f"{prefix}_attempt_count" if prefix else "attempt_count"
            setattr(item, status_field, TaskStatus.RUNNING)
            setattr(item, run_token_field, token)
            setattr(item, started_field, now_utc())
            setattr(item, heartbeat_field, now_utc())
            setattr(item, attempt_field, getattr(item, attempt_field) + 1)
            worker_task = WorkerTask(
                task_type=task_type,
                subject_type=subject_type,
                subject_id=item.id,
                run_token=token,
                timeout_at=now_utc() + timedelta(seconds=timeout_seconds),
            )
            session.add(item)
            session.add(worker_task)
            try:
                session.commit()
            except Exception:
                session.rollback()
                return None
            return worker_task.id

    def _find_candidate(self, session: Session, task_type: WorkerTaskType):
        if task_type == WorkerTaskType.DATA_COLLECT:
            return self._find_data_collect_candidate(session)
        if task_type == WorkerTaskType.COMMENT_COLLECT:
            return self._find_comment_collect_candidate(session)
        if task_type == WorkerTaskType.MEDIA_DOWNLOAD:
            return self._find_media_download_candidate(session)
        if task_type == WorkerTaskType.VIDEO_TRANSCRIPTION:
            return self._find_transcription_candidate(session)
        if task_type == WorkerTaskType.DATA_SYNC:
            return self._find_sync_candidate(session)
        return None

    def _find_data_collect_candidate(self, session: Session):
        for model, subject in (
            (Account, WorkerSubject.ACCOUNT),
            (Aweme, WorkerSubject.AWEME),
        ):
            items = session.exec(
                select(model)
                .where(model.deleted_at.is_(None))
                .order_by(model.created_at)
                .limit(20)
            ).all()
            for item in items:
                if self._due(item.collection_status, item.collection_next_run_at):
                    return item, subject, "collection"
        return None

    def _find_comment_collect_candidate(self, session: Session):
        items = session.exec(
            select(Aweme)
            .where(
                Aweme.deleted_at.is_(None),
                Aweme.platform_aweme_id.is_not(None),
            )
            .order_by(Aweme.created_at)
            .limit(20)
        ).all()
        for item in items:
            if self._due(
                item.comment_collection_status,
                item.comment_collection_next_run_at,
            ):
                return item, WorkerSubject.AWEME, "comment_collection"
        return None

    def _find_media_download_candidate(self, session: Session):
        items = session.exec(
            select(Aweme)
            .where(Aweme.deleted_at.is_(None), Aweme.video_url.is_not(None))
            .order_by(Aweme.created_at)
            .limit(20)
        ).all()
        for item in items:
            if self._due(item.media_download_status, item.media_download_next_run_at):
                return item, WorkerSubject.AWEME, "media_download"
        transcriptions = session.exec(
            select(VideoTranscription)
            .where(
                VideoTranscription.source_url.is_not(None),
                VideoTranscription.video_path.is_(None),
            )
            .order_by(VideoTranscription.created_at)
            .limit(20)
        ).all()
        for item in transcriptions:
            if self._due(item.status, item.next_run_at):
                return item, WorkerSubject.VIDEO_TRANSCRIPTION, ""
        return None

    def _find_transcription_candidate(self, session: Session):
        items = session.exec(
            select(VideoTranscription)
            .where(VideoTranscription.video_path.is_not(None))
            .order_by(VideoTranscription.created_at)
            .limit(20)
        ).all()
        for item in items:
            if self._due(item.status, item.next_run_at):
                return item, WorkerSubject.VIDEO_TRANSCRIPTION, ""
        return None

    def _find_sync_candidate(self, session: Session):
        for model, subject, owner_model, owner_field in (
            (AwemeDataStorerSync, WorkerSubject.AWEME_SYNC, Aweme, "aweme_id"),
            (
                AccountDataStorerSync,
                WorkerSubject.ACCOUNT_SYNC,
                Account,
                "account_id",
            ),
        ):
            relations = session.exec(
                select(model)
                .where(model.enabled.is_(True))
                .order_by(model.created_at)
                .limit(30)
            ).all()
            for relation in relations:
                if not self._due(relation.status, relation.next_run_at):
                    continue
                storer = session.get(DataStorer, relation.data_storer_id)
                if not storer or storer.status != DataStorerStatus.ACTIVE:
                    continue
                owner = session.get(owner_model, getattr(relation, owner_field))
                if not owner:
                    continue
                ready = (
                    self.services.sync.aweme_ready(owner)
                    if owner_model is Aweme
                    else self.services.sync.account_ready(owner)
                )
                if ready:
                    return relation, subject, ""
        return None

    def _reap_finished_processes(self) -> None:
        for worker_task_id, process in list(self.processes.items()):
            if process.poll() is None:
                continue
            self.processes.pop(worker_task_id, None)
            with Session(self.engine) as session:
                worker_task = session.get(WorkerTask, worker_task_id)
                if worker_task:
                    self._mark_timeout_or_failure(session, worker_task, timed_out=False)
                    session.delete(worker_task)
                    session.commit()

    def _terminate_worker_task(self, worker_task: WorkerTask) -> None:
        process = self.processes.get(worker_task.id)
        if process and process.poll() is None:
            self._terminate_process_group(process.pid)
            return
        if worker_task.pid and self._pid_matches(worker_task):
            self._terminate_process_group(worker_task.pid)

    def _terminate_process_group(self, pid: int) -> None:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + self.settings.worker.terminate_grace_seconds
        while time.monotonic() < deadline:
            if not psutil.pid_exists(pid):
                return
            time.sleep(0.1)
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return

    def _worker_task_alive(self, worker_task: WorkerTask) -> bool:
        process = self.processes.get(worker_task.id)
        if process:
            return process.poll() is None
        return bool(worker_task.pid and self._pid_matches(worker_task))

    @staticmethod
    def _pid_matches(worker_task: WorkerTask) -> bool:
        if not worker_task.pid or not worker_task.process_started_at:
            return False
        try:
            process = psutil.Process(worker_task.pid)
            actual_started_at = time_from_timestamp(process.create_time())
        except (psutil.NoSuchProcess, ProcessLookupError):
            return False
        expected_started_at = worker_task.process_started_at
        if expected_started_at.tzinfo is None:
            expected_started_at = expected_started_at.replace(tzinfo=timezone.utc)
        return abs((actual_started_at - expected_started_at).total_seconds()) <= 2

    @staticmethod
    def _mark_timeout_or_failure(
        session: Session, worker_task: WorkerTask, *, timed_out: bool
    ) -> None:
        status = TaskStatus.TIMEOUT if timed_out else TaskStatus.FAILED
        if worker_task.task_type == WorkerTaskType.DATA_COLLECT:
            model = (
                Account
                if worker_task.subject_type == WorkerSubject.ACCOUNT
                else Aweme
            )
            item = session.get(model, worker_task.subject_id)
            prefix = "collection"
        elif worker_task.task_type == WorkerTaskType.MEDIA_DOWNLOAD:
            if worker_task.subject_type == WorkerSubject.VIDEO_TRANSCRIPTION:
                item = session.get(VideoTranscription, worker_task.subject_id)
                prefix = ""
            else:
                item = session.get(Aweme, worker_task.subject_id)
                prefix = "media_download"
        elif worker_task.task_type == WorkerTaskType.COMMENT_COLLECT:
            item = session.get(Aweme, worker_task.subject_id)
            prefix = "comment_collection"
        elif worker_task.task_type == WorkerTaskType.VIDEO_TRANSCRIPTION:
            item = session.get(VideoTranscription, worker_task.subject_id)
            prefix = ""
        else:
            model = (
                AwemeDataStorerSync
                if worker_task.subject_type == WorkerSubject.AWEME_SYNC
                else AccountDataStorerSync
            )
            item = session.get(model, worker_task.subject_id)
            prefix = ""
        if not item:
            return
        token_field = f"{prefix}_run_token" if prefix else "run_token"
        if getattr(item, token_field) != worker_task.run_token:
            return
        status_field = f"{prefix}_status" if prefix else "status"
        error_field = (
            f"{prefix}_error"
            if prefix in {"collection", "media_download", "comment_collection"}
            else "error_message"
        )
        setattr(item, status_field, status)
        setattr(
            item,
            error_field,
            "worker task timed out" if timed_out else "worker task exited unexpectedly",
        )
        setattr(item, token_field, None)
        session.add(item)

    def _next_slot(self) -> WorkerSlot:
        return self._slots[self._slot_index]

    def _advance_slot(self) -> None:
        self._slot_index = (self._slot_index + 1) % len(self._slots)

    def _reload_settings(self) -> None:
        try:
            refreshed = load_settings(self.settings.config_path, force_reload=True)
            if refreshed != self.settings:
                self.services.close()
                self.settings = refreshed
                self.services = build_services(self.settings, db_engine=self.engine)
        except Exception:
            pass


def time_from_timestamp(value: float):
    from datetime import datetime

    return datetime.fromtimestamp(value, tz=timezone.utc)
