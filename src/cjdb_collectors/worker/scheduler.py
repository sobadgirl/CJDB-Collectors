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
from sqlalchemy import and_, or_
from sqlmodel import Session, select

from cjdb_collectors.settings import PROJECT_ROOT, Settings, load_settings
from cjdb_collectors.db import create_db_engine
from cjdb_collectors.models import (
    Account,
    Aweme,
    Provider,
    ProviderSync,
    ProjectAccount,
    ProjectAweme,
    SyncObjectType,
    TaskStatus,
    VideoTranscription,
    WorkerSubject,
    WorkerTask,
    WorkerTaskStatus,
    WorkerTaskType,
)
from cjdb_collectors.services import ServiceContainer, build_services
from cjdb_collectors.services.base import now_utc
from cjdb_collectors.services.logger import LoggerService, LogType
from cjdb_collectors.domains.data_provider import DataProviderType


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
        LoggerService.configure(settings=self.settings)
        self.logger_service = LoggerService
        self.logger = LoggerService.get_logger(LogType.WORKER)
        self.engine = db_engine or create_db_engine(self.settings.app.database_path)
        self.services = services or build_services(self.settings, db_engine=self.engine)
        self.processes: dict[UUID, subprocess.Popen] = {}
        self._stopping = False
        self._slot_index = 0
        self._slots = [
            WorkerSlot("data_collect"),
            WorkerSlot("timeout"),
            # V1.0 发布隐藏：账号/作者采集和评论采集不进入主调度轮询，避免误启动。
            # WorkerSlot("account_history_collect", cooldown_on_empty=2),
            # WorkerSlot("comment_collect", cooldown_on_empty=2),
            WorkerSlot("media_download", cooldown_on_empty=1),
            WorkerSlot("timeout"),
            WorkerSlot("video_transcription", cooldown_on_empty=2),
            WorkerSlot("data_sync", cooldown_on_empty=1),
            WorkerSlot("reset_stale", cooldown_on_empty=5),
        ]

    def stop(self) -> None:
        self._stopping = True

    def run_forever(self) -> None:
        self.logger.info(
            "Worker 主循环启动：slots=%s",
            ",".join(slot.name for slot in self._slots),
        )
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
        self.logger.info("Worker 主循环退出")

    def _write_heartbeat(self) -> None:
        path = Path(self.settings.app.data_dir) / "worker.heartbeat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(now_utc().isoformat(), encoding="utf-8")

    def run_once(self) -> bool:
        self._reap_finished_processes()
        self._refresh_running_heartbeats()
        slot = self._next_slot()
        self.logger.info("Worker 扫描：slot=%s", slot.name)
        if slot.cooldown_remaining > 0:
            self.logger.info(
                "Worker 扫描跳过：slot=%s reason=cooldown remaining=%s",
                slot.name,
                slot.cooldown_remaining,
            )
            slot.cooldown_remaining -= 1
            self._advance_slot()
            return False

        result = self._handle_slot(slot.name)
        self.logger.info("Worker 轮询：slot=%s result=%s", slot.name, result.value)
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
        # V1.0 发布隐藏：评论采集任务暂不调度。
        # return self._dispatch_one_worker_task(WorkerTaskType.COMMENT_COLLECT)
        return SlotResult.EMPTY

    def check_and_run_account_history(self) -> SlotResult:
        # V1.0 发布隐藏：账号/作者历史作品采集任务暂不调度。
        # return self._dispatch_one_worker_task(WorkerTaskType.ACCOUNT_HISTORY_COLLECT)
        return SlotResult.EMPTY

    def check_and_download_media(self) -> SlotResult:
        return self._dispatch_one_worker_task(WorkerTaskType.MEDIA_DOWNLOAD)

    def check_and_transcribe(self) -> SlotResult:
        prepared = self._prepare_aweme_transcription_candidate()
        if prepared != SlotResult.EMPTY:
            return prepared
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
            if self._reset_orphan_running(session):
                session.commit()
                return SlotResult.HANDLED
        return SlotResult.EMPTY

    def _handle_slot(self, name: str) -> SlotResult:
        handlers = {
            "data_collect": self.check_and_pull_data,
            # V1.0 发布隐藏：保留 handler 代码，轮询槽位已关闭。
            # "account_history_collect": self.check_and_run_account_history,
            # "comment_collect": self.check_and_run_comments,
            "media_download": self.check_and_download_media,
            "video_transcription": self.check_and_transcribe,
            "data_sync": self.check_and_sync_data,
            "timeout": self.process_timeout,
            "reset_stale": self.reset_stale,
        }
        return handlers[name]()

    def _dispatch_one_worker_task(self, task_type: WorkerTaskType) -> SlotResult:
        task_settings = getattr(self.settings.worker_tasks, task_type.value)
        if task_settings.process_limit <= 0:
            self.logger.info(
                "任务调度阻塞：type=%s reason=process_limit_zero",
                task_type.value,
            )
            return SlotResult.BLOCKED
        if self._running_count(task_type) >= task_settings.process_limit:
            self.logger.info(
                "任务调度阻塞：type=%s reason=process_limit_reached",
                task_type.value,
            )
            return SlotResult.BLOCKED
        worker_task_id, provider_blocked = self._claim(
            task_type,
            task_settings.timeout_seconds,
        )
        if worker_task_id is None:
            self.logger.info(
                "任务调度无候选：type=%s provider_blocked=%s",
                task_type.value,
                provider_blocked,
            )
            return SlotResult.BLOCKED if provider_blocked else SlotResult.EMPTY
        process = self._start_worker_process(worker_task_id, task_type)
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
        self.logger.info(
            "任务已派发：type=%s worker_task_id=%s pid=%s",
            task_type.value,
            worker_task_id,
            process.pid,
        )
        return SlotResult.DISPATCHED

    def _start_worker_process(
        self,
        worker_task_id: UUID,
        task_type: WorkerTaskType,
    ) -> subprocess.Popen:
        command = [
            str(PROJECT_ROOT / "cjdb"),
            "worker",
            "run-task",
            "--worker-task-id",
            str(worker_task_id),
            "--config",
            str(self.settings.config_path),
        ]
        log_path = self.logger_service.get_log_path(
            LogType.WORKER_TASKS,
            task_type.value,
        )
        with self.logger_service.open_binary_append(log_path) as log:
            self.logger.info(
                "启动任务子进程：worker_task_id=%s command=%s",
                worker_task_id,
                command,
            )
            return subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )

    def _running_count(self, task_type: WorkerTaskType) -> int:
        with Session(self.engine) as session:
            return len(
                session.exec(
                    select(WorkerTask.id).where(WorkerTask.task_type == task_type)
                ).all()
            )

    @staticmethod
    def _due_clause(status_field, next_run_at_field):
        return or_(
            status_field == TaskStatus.PENDING,
            and_(
                status_field == TaskStatus.RETRY_WAIT,
                or_(next_run_at_field.is_(None), next_run_at_field <= now_utc()),
            ),
        )

    def _claim(
        self,
        task_type: WorkerTaskType,
        timeout_seconds: int,
    ) -> tuple[UUID | None, bool]:
        with Session(self.engine) as session:
            found = self._find_candidate(session, task_type)
            if not found:
                return None, False
            item, subject_type, prefix = found
            provider_type = self._required_provider_type(
                task_type,
                item,
                subject_type,
            )
            project_id = self._project_id_for_subject(
                session,
                item,
                subject_type,
            )
            provider_ready = True
            if provider_type is not None:
                provider_ready = (
                    self.services.providers.is_ready(provider_type, str(project_id))
                    if project_id
                    else self.services.providers.is_ready(provider_type)
                )
            if not provider_ready:
                return None, True
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
            now = now_utc()
            worker_task = WorkerTask(
                task_type=task_type,
                subject_type=subject_type,
                subject_id=item.id,
                run_token=token,
                heartbeat_at=now,
                timeout_at=now + timedelta(seconds=timeout_seconds),
            )
            session.add(item)
            session.add(worker_task)
            try:
                session.commit()
            except Exception:
                session.rollback()
                return None, False
            return worker_task.id, False

    @staticmethod
    def _project_id_for_subject(session, item, subject_type: WorkerSubject):
        if subject_type == WorkerSubject.ACCOUNT:
            return session.exec(
                select(ProjectAccount.project_id)
                .where(ProjectAccount.account_id == item.id)
                .limit(1)
            ).first()
        if subject_type == WorkerSubject.AWEME:
            return session.exec(
                select(ProjectAweme.project_id)
                .where(ProjectAweme.aweme_id == item.id)
                .limit(1)
            ).first()
        if subject_type == WorkerSubject.VIDEO_TRANSCRIPTION and item.aweme_id:
            return session.exec(
                select(ProjectAweme.project_id)
                .where(ProjectAweme.aweme_id == item.aweme_id)
                .limit(1)
            ).first()
        return None

    def _required_provider_type(
        self,
        task_type: WorkerTaskType,
        item,
        subject_type: WorkerSubject,
    ) -> DataProviderType | None:
        if task_type == WorkerTaskType.DATA_COLLECT:
            if subject_type == WorkerSubject.ACCOUNT:
                # V1.0 发布隐藏：账号/作者采集不需要 Provider。
                # return self.services.providers.type_for_account(
                #     item.platform,
                #     item.profile_url,
                # )
                return None
            return self.services.providers.type_for_aweme(
                item.platform,
                item.source_url,
            )
        if task_type == WorkerTaskType.ACCOUNT_HISTORY_COLLECT:
            # V1.0 发布隐藏：账号/作者历史采集不检查 Provider、不调度。
            # return self.services.providers.type_for_account(
            #     item.platform,
            #     item.profile_url,
            # )
            return None
        if task_type == WorkerTaskType.COMMENT_COLLECT:
            # V1.0 发布隐藏：评论采集不检查 Provider、不调度。
            # return self.services.providers.type_for_comments(
            #     item.platform,
            #     item.source_url,
            # )
            return None
        if task_type == WorkerTaskType.VIDEO_TRANSCRIPTION:
            return DataProviderType.VIDEO_TRANSCRIPTION
        return None

    def _find_candidate(self, session: Session, task_type: WorkerTaskType):
        if task_type == WorkerTaskType.DATA_COLLECT:
            return self._find_data_collect_candidate(session)
        if task_type == WorkerTaskType.ACCOUNT_HISTORY_COLLECT:
            # V1.0 发布隐藏：账号/作者历史采集候选暂不开放。
            # return self._find_account_history_candidate(session)
            return None
        if task_type == WorkerTaskType.COMMENT_COLLECT:
            # V1.0 发布隐藏：评论采集候选暂不开放。
            # return self._find_comment_collect_candidate(session)
            return None
        if task_type == WorkerTaskType.MEDIA_DOWNLOAD:
            return self._find_media_download_candidate(session)
        if task_type == WorkerTaskType.VIDEO_TRANSCRIPTION:
            return self._find_transcription_candidate(session)
        if task_type == WorkerTaskType.DATA_SYNC:
            return self._find_sync_candidate(session)
        return None

    def _find_data_collect_candidate(self, session: Session):
        # V1.0 发布隐藏：普通 data_collect 只处理作品，不扫描 Account，避免作者/账号采集误启动。
        item = session.exec(
            select(Aweme)
            .where(
                Aweme.deleted_at.is_(None),
                self._due_clause(
                    Aweme.collection_status,
                    Aweme.collection_next_run_at,
                ),
            )
            .order_by(Aweme.created_at)
            .limit(1)
        ).first()
        if item:
            return item, WorkerSubject.AWEME, "collection"
        return None

    def _find_account_history_candidate(self, session: Session):
        item = session.exec(
            select(Account)
            .where(
                Account.deleted_at.is_(None),
                Account.platform_account_id.is_not(None),
                self._due_clause(
                    Account.history_status,
                    Account.history_next_run_at,
                ),
            )
            .order_by(Account.created_at)
            .limit(1)
        ).first()
        if item:
            return item, WorkerSubject.ACCOUNT, "history"
        return None

    def _find_comment_collect_candidate(self, session: Session):
        item = session.exec(
            select(Aweme)
            .where(
                Aweme.deleted_at.is_(None),
                Aweme.platform_aweme_id.is_not(None),
                self._due_clause(
                    Aweme.comment_collection_status,
                    Aweme.comment_collection_next_run_at,
                ),
            )
            .order_by(Aweme.created_at)
            .limit(1)
        ).first()
        if item:
            return item, WorkerSubject.AWEME, "comment_collection"
        return None

    def _find_media_download_candidate(self, session: Session):
        item = session.exec(
            select(Aweme)
            .where(
                Aweme.deleted_at.is_(None),
                or_(
                    Aweme.cover_url.is_not(None),
                    Aweme.video_url.is_not(None),
                    Aweme.photos != [],
                ),
                self._due_clause(
                    Aweme.media_download_status,
                    Aweme.media_download_next_run_at,
                ),
            )
            .order_by(Aweme.created_at)
            .limit(1)
        ).first()
        if item:
            return item, WorkerSubject.AWEME, "media_download"
        transcription = session.exec(
            select(VideoTranscription)
            .where(
                VideoTranscription.source_url.is_not(None),
                VideoTranscription.video_path.is_(None),
                self._due_clause(
                    VideoTranscription.status,
                    VideoTranscription.next_run_at,
                ),
            )
            .order_by(VideoTranscription.created_at)
            .limit(1)
        ).first()
        if transcription:
            return transcription, WorkerSubject.VIDEO_TRANSCRIPTION, ""
        return None

    def _find_transcription_candidate(self, session: Session):
        item = session.exec(
            select(VideoTranscription)
            .where(
                VideoTranscription.video_path.is_not(None),
                self._due_clause(
                    VideoTranscription.status,
                    VideoTranscription.next_run_at,
                ),
            )
            .order_by(VideoTranscription.created_at)
            .limit(1)
        ).first()
        if item:
            return item, WorkerSubject.VIDEO_TRANSCRIPTION, ""
        return None

    def _prepare_aweme_transcription_candidate(self) -> SlotResult:
        with Session(self.engine) as session:
            candidates = session.exec(
                select(Aweme)
                .outerjoin(ProjectAweme)
                .where(
                    Aweme.deleted_at.is_(None),
                    Aweme.video_path.is_not(None),
                    or_(
                        Aweme.video_transcription_status == TaskStatus.PENDING,
                        ProjectAweme.transcribe_enabled.is_(True),
                    ),
                )
                .distinct()
                .order_by(Aweme.created_at)
                .limit(20)
            ).all()
            aweme_id = None
            for candidate in candidates:
                existing = session.exec(
                    select(VideoTranscription)
                    .where(
                        VideoTranscription.aweme_id == candidate.id,
                        VideoTranscription.is_current.is_(True),
                    )
                    .limit(1)
                ).first()
                if existing:
                    continue
                aweme_id = candidate.id
                break
        if not aweme_id:
            return SlotResult.EMPTY
        self.services.transcriptions.transcribe_aweme(aweme_id)
        return SlotResult.HANDLED

    def _find_sync_candidate(self, session: Session):
        for object_type, subject, owner_model in (
            (SyncObjectType.AWEME, WorkerSubject.AWEME_SYNC, Aweme),
            (SyncObjectType.ACCOUNT, WorkerSubject.ACCOUNT_SYNC, Account),
            (
                SyncObjectType.VIDEO_TRANSCRIPTION,
                WorkerSubject.VIDEO_TRANSCRIPTION_SYNC,
                VideoTranscription,
            ),
        ):
            relations = session.exec(
                select(ProviderSync)
                .where(
                    ProviderSync.object_type == object_type,
                    ProviderSync.enabled.is_(True),
                    self._due_clause(ProviderSync.status, ProviderSync.next_run_at),
                )
                .order_by(ProviderSync.created_at)
                .limit(30)
            ).all()
            for relation in relations:
                provider = session.get(Provider, relation.provider_id)
                if not provider or provider.status != "ready":
                    self.logger.info(
                        "同步候选跳过：sync_id=%s reason=provider_not_ready provider_id=%s status=%s",
                        relation.id,
                        relation.provider_id,
                        provider.status if provider else "missing",
                    )
                    continue
                if not self.services.store_providers.is_ready(provider.id):
                    self.logger.info(
                        "同步候选跳过：sync_id=%s reason=store_provider_not_ready provider_id=%s",
                        relation.id,
                        provider.id,
                    )
                    continue
                owner = session.get(owner_model, relation.object_id)
                if not owner:
                    self.logger.info(
                        "同步候选跳过：sync_id=%s reason=owner_missing",
                        relation.id,
                    )
                    continue
                if owner_model is Aweme:
                    ready = self.services.sync.aweme_ready(owner)
                elif owner_model is Account:
                    ready = self.services.sync.account_ready(owner)
                else:
                    ready = owner.status == TaskStatus.SUCCEEDED
                if ready:
                    return relation, subject, ""
                self.logger.info(
                    "同步候选跳过：sync_id=%s reason=owner_not_ready owner_id=%s",
                    relation.id,
                    relation.object_id,
                )
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

    def _refresh_running_heartbeats(self) -> None:
        heartbeat_at = now_utc()
        with Session(self.engine) as session:
            worker_tasks = session.exec(
                select(WorkerTask)
                .where(WorkerTask.status == WorkerTaskStatus.RUNNING)
                .order_by(WorkerTask.started_at)
            ).all()
            changed = False
            for worker_task in worker_tasks:
                if not self._worker_task_alive(worker_task):
                    continue
                worker_task.heartbeat_at = heartbeat_at
                item, prefix = self._task_item_and_prefix(session, worker_task)
                if item:
                    token_field = f"{prefix}_run_token" if prefix else "run_token"
                    if getattr(item, token_field) == worker_task.run_token:
                        heartbeat_field = (
                            f"{prefix}_heartbeat_at" if prefix else "heartbeat_at"
                        )
                        setattr(item, heartbeat_field, heartbeat_at)
                        session.add(item)
                session.add(worker_task)
                changed = True
            if changed:
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

    def _mark_timeout_or_failure(
        self, session: Session, worker_task: WorkerTask, *, timed_out: bool
    ) -> None:
        item, prefix = self._task_item_and_prefix(session, worker_task)
        if not item:
            return
        token_field = f"{prefix}_run_token" if prefix else "run_token"
        if getattr(item, token_field) != worker_task.run_token:
            return
        task_config = getattr(self.settings.worker_tasks, worker_task.task_type.value)
        attempt_field = f"{prefix}_attempt_count" if prefix else "attempt_count"
        attempts = getattr(item, attempt_field)
        final_status = TaskStatus.TIMEOUT if timed_out else TaskStatus.FAILED
        next_status = self._failure_next_status(attempts, task_config, final_status)
        error = (
            "worker task timed out" if timed_out else "worker task exited unexpectedly"
        )
        self._mark_business_failure(
            session,
            item,
            prefix,
            worker_task.task_type,
            next_status,
            error,
        )

    def _reset_orphan_running(self, session: Session) -> bool:
        for task_type, model, subject, prefix, extra_conditions in (
            (
                WorkerTaskType.DATA_COLLECT,
                Aweme,
                WorkerSubject.AWEME,
                "collection",
                (),
            ),
            # V1.0 发布隐藏：账号/作者采集、账号历史采集、评论采集不做 stale reset。
            # (
            #     WorkerTaskType.DATA_COLLECT,
            #     Account,
            #     WorkerSubject.ACCOUNT,
            #     "collection",
            #     (),
            # ),
            # (
            #     WorkerTaskType.ACCOUNT_HISTORY_COLLECT,
            #     Account,
            #     WorkerSubject.ACCOUNT,
            #     "history",
            #     (Account.platform_account_id.is_not(None),),
            # ),
            # (
            #     WorkerTaskType.COMMENT_COLLECT,
            #     Aweme,
            #     WorkerSubject.AWEME,
            #     "comment_collection",
            #     (),
            # ),
            (
                WorkerTaskType.MEDIA_DOWNLOAD,
                Aweme,
                WorkerSubject.AWEME,
                "media_download",
                (),
            ),
            (
                WorkerTaskType.MEDIA_DOWNLOAD,
                VideoTranscription,
                WorkerSubject.VIDEO_TRANSCRIPTION,
                "",
                (
                    VideoTranscription.source_url.is_not(None),
                    VideoTranscription.video_path.is_(None),
                ),
            ),
            (
                WorkerTaskType.VIDEO_TRANSCRIPTION,
                VideoTranscription,
                WorkerSubject.VIDEO_TRANSCRIPTION,
                "",
                (VideoTranscription.video_path.is_not(None),),
            ),
            (
                WorkerTaskType.DATA_SYNC,
                ProviderSync,
                WorkerSubject.AWEME_SYNC,
                "",
                (ProviderSync.object_type == SyncObjectType.AWEME,),
            ),
            (
                WorkerTaskType.DATA_SYNC,
                ProviderSync,
                WorkerSubject.ACCOUNT_SYNC,
                "",
                (ProviderSync.object_type == SyncObjectType.ACCOUNT,),
            ),
            (
                WorkerTaskType.DATA_SYNC,
                ProviderSync,
                WorkerSubject.VIDEO_TRANSCRIPTION_SYNC,
                "",
                (ProviderSync.object_type == SyncObjectType.VIDEO_TRANSCRIPTION,),
            ),
        ):
            status_field = f"{prefix}_status" if prefix else "status"
            heartbeat_field = f"{prefix}_heartbeat_at" if prefix else "heartbeat_at"
            token_field = f"{prefix}_run_token" if prefix else "run_token"
            task_config = getattr(self.settings.worker_tasks, task_type.value)
            stale_before = now_utc() - timedelta(seconds=task_config.timeout_seconds)
            item = session.exec(
                select(model)
                .where(
                    getattr(model, status_field) == TaskStatus.RUNNING,
                    getattr(model, token_field).is_not(None),
                    or_(
                        getattr(model, heartbeat_field).is_(None),
                        getattr(model, heartbeat_field) <= stale_before,
                    ),
                    *extra_conditions,
                )
                .order_by(getattr(model, heartbeat_field))
                .limit(1)
            ).first()
            if not item:
                continue
            worker_task_exists = session.exec(
                select(WorkerTask.id)
                .where(
                    WorkerTask.task_type == task_type,
                    WorkerTask.subject_type == subject,
                    WorkerTask.subject_id == item.id,
                    WorkerTask.run_token == getattr(item, token_field),
                )
                .limit(1)
            ).first()
            if worker_task_exists:
                continue
            attempt_field = f"{prefix}_attempt_count" if prefix else "attempt_count"
            attempts = getattr(item, attempt_field)
            next_status = self._failure_next_status(
                attempts,
                task_config,
                TaskStatus.TIMEOUT,
            )
            self._mark_business_failure(
                session,
                item,
                prefix,
                task_type,
                next_status,
                "running task lost worker heartbeat",
            )
            return True
        return False

    @staticmethod
    def _failure_next_status(attempts: int, task_config, final_status: TaskStatus):
        return TaskStatus.RETRY_WAIT if attempts < task_config.retry_limit else final_status

    def _mark_business_failure(
        self,
        session: Session,
        item,
        prefix: str,
        task_type: WorkerTaskType,
        status: TaskStatus,
        error: str,
    ) -> None:
        task_config = getattr(self.settings.worker_tasks, task_type.value)
        status_field = f"{prefix}_status" if prefix else "status"
        error_field = (
            f"{prefix}_error"
            if prefix in {"collection", "media_download", "comment_collection", "history"}
            else "error_message"
        )
        next_field = f"{prefix}_next_run_at" if prefix else "next_run_at"
        heartbeat_field = f"{prefix}_heartbeat_at" if prefix else "heartbeat_at"
        token_field = f"{prefix}_run_token" if prefix else "run_token"
        setattr(item, status_field, status)
        setattr(item, error_field, error)
        setattr(
            item,
            next_field,
            now_utc() + timedelta(seconds=task_config.retry_delay_seconds)
            if status == TaskStatus.RETRY_WAIT
            else None,
        )
        setattr(item, heartbeat_field, None)
        setattr(item, token_field, None)
        session.add(item)
        self._sync_aweme_transcription_status(session, item, status)

    @staticmethod
    def _task_item_and_prefix(session: Session, worker_task: WorkerTask):
        if worker_task.task_type == WorkerTaskType.DATA_COLLECT:
            model = (
                Account
                if worker_task.subject_type == WorkerSubject.ACCOUNT
                else Aweme
            )
            item = session.get(model, worker_task.subject_id)
            prefix = "collection"
        elif worker_task.task_type == WorkerTaskType.ACCOUNT_HISTORY_COLLECT:
            item = session.get(Account, worker_task.subject_id)
            prefix = "history"
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
            item = session.get(ProviderSync, worker_task.subject_id)
            prefix = ""
        return item, prefix

    @staticmethod
    def _sync_aweme_transcription_status(
        session: Session, item, status: TaskStatus
    ) -> None:
        if not isinstance(item, VideoTranscription) or not item.aweme_id:
            return
        aweme = session.get(Aweme, item.aweme_id)
        if not aweme:
            return
        aweme.video_transcription_status = status
        session.add(aweme)

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
                LoggerService.configure(settings=self.settings)
                self.logger = LoggerService.get_logger(LogType.WORKER)
                self.services = build_services(self.settings, db_engine=self.engine)
        except Exception:
            pass


def time_from_timestamp(value: float):
    from datetime import datetime

    return datetime.fromtimestamp(value, tz=timezone.utc)
