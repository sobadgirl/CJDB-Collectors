from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import UUID

from sqlmodel import select

from cjdb_collectors.models import (
    Account,
    Aweme,
    Provider,
    ProviderSync,
    SyncObjectType,
    WorkerSubject,
    WorkerTask,
    WorkerTaskType,
    TaskStatus,
    VideoTranscription,
)
from cjdb_collectors.domains.store import StoreResult

from .base import SessionFactory, now_utc
from .logger import LoggerService, LogType


class ExecutionService:
    """Executes one already-claimed record and performs token-guarded writes."""

    def __init__(
        self,
        session_factory: SessionFactory,
        container,
    ) -> None:
        self._session = session_factory
        self.container = container
        self.logger_service = getattr(container, "logger", LoggerService)
        self.logger = self.logger_service.get_logger(LogType.WORKER_TASKS, "unknown")

    def run(self, worker_task_id: UUID) -> None:
        with self._session() as session:
            worker_task = session.get(WorkerTask, worker_task_id)
            if not worker_task:
                return
            task_type = worker_task.task_type
            subject_type = worker_task.subject_type
            subject_id = worker_task.subject_id
            token = worker_task.run_token
        self.logger = self.logger_service.get_logger(
            LogType.WORKER_TASKS,
            task_type.value,
        )
        self.logger.info(
            "执行任务开始：task_id=%s type=%s subject=%s subject_id=%s",
            worker_task_id,
            task_type.value,
            subject_type.value,
            subject_id,
        )
        if self._skip_v1_hidden_task(task_type, subject_type, subject_id, token):
            self.logger.info("执行任务跳过：task_id=%s reason=v1_hidden", worker_task_id)
            return
        try:
            if task_type == WorkerTaskType.DATA_COLLECT:
                self._collect(subject_type, subject_id, token)
            elif task_type == WorkerTaskType.ACCOUNT_HISTORY_COLLECT:
                self._account_history(subject_id, token)
            elif task_type == WorkerTaskType.MEDIA_DOWNLOAD:
                self._download(subject_type, subject_id, token)
            elif task_type == WorkerTaskType.COMMENT_COLLECT:
                self._comments(subject_id, token)
            elif task_type == WorkerTaskType.VIDEO_TRANSCRIPTION:
                self._transcribe(subject_id, token)
            elif task_type == WorkerTaskType.DATA_SYNC:
                self._sync(subject_type, subject_id, token)
        except Exception as exc:
            self.logger.exception("执行任务失败：task_id=%s error=%s", worker_task_id, exc)
            self._fail(task_type, subject_type, subject_id, token, str(exc))
            raise
        finally:
            with self._session() as session:
                worker_task = session.get(WorkerTask, worker_task_id)
                if worker_task and worker_task.run_token == token:
                    session.delete(worker_task)
        self.logger.info("执行任务完成：task_id=%s", worker_task_id)

    def _collect(self, subject_type: WorkerSubject, subject_id: UUID, token: str) -> None:
        with self._session() as session:
            if subject_type == WorkerSubject.ACCOUNT:
                item = session.get(Account, subject_id)
                if not item or item.collection_run_token != token:
                    return
                account = item
            else:
                item = session.get(Aweme, subject_id)
                if not item or item.collection_run_token != token:
                    return
                aweme = item
        if subject_type == WorkerSubject.AWEME:
            self.logger.info("采集作品：aweme_id=%s", subject_id)
            self.container.awemes.fetch_data(aweme)
            return
        if subject_type == WorkerSubject.ACCOUNT:
            self.logger.info("采集账号：account_id=%s", subject_id)
            self.container.accounts.fetch_data(account)

    def _account_history(self, account_id: UUID, token: str) -> None:
        with self._session() as session:
            account = session.get(Account, account_id)
            if (
                not account
                or account.history_run_token != token
                or not account.platform_account_id
            ):
                return
        self.container.accounts.process_published_history(account)

    def _download(
        self, subject_type: WorkerSubject, subject_id: UUID, token: str
    ) -> None:
        if subject_type == WorkerSubject.VIDEO_TRANSCRIPTION:
            with self._session() as session:
                item = session.get(VideoTranscription, subject_id)
                if not item or item.run_token != token or not item.source_url:
                    return
                source_url = item.source_url
            self.logger.info("下载转写视频：transcription_id=%s", subject_id)
            result = self.container.media_downloader.download(
                source_url, media_type="video"
            )
            with self._session() as session:
                item = session.get(VideoTranscription, subject_id)
                if not item or item.run_token != token:
                    return
                item.video_path = str(result.path)
                item.video_sha256 = result.sha256
                item.status = TaskStatus.PENDING
                item.run_token = None
                item.started_at = None
                session.add(item)
            return
        with self._session() as session:
            aweme = session.get(Aweme, subject_id)
            if not aweme or aweme.media_download_run_token != token:
                return
        self.logger.info("下载作品媒体：aweme_id=%s", subject_id)
        self.container.awemes.download_media(aweme)

    def _comments(self, aweme_id: UUID, token: str) -> None:
        with self._session() as session:
            aweme = session.get(Aweme, aweme_id)
            if (
                not aweme
                or aweme.comment_collection_run_token != token
                or not aweme.platform_aweme_id
            ):
                return
        self.container.awemes.fetch_comments(aweme)

    def _skip_v1_hidden_task(
        self,
        task_type: WorkerTaskType,
        subject_type: WorkerSubject,
        subject_id: UUID,
        token: str,
    ) -> bool:
        hidden_account_collection = (
            task_type == WorkerTaskType.DATA_COLLECT
            and subject_type == WorkerSubject.ACCOUNT
        )
        hidden_task = task_type in {
            WorkerTaskType.ACCOUNT_HISTORY_COLLECT,
            WorkerTaskType.COMMENT_COLLECT,
        }
        if not hidden_account_collection and not hidden_task:
            return False

        with self._session() as session:
            if hidden_account_collection:
                item = session.get(Account, subject_id)
                prefix = "collection"
            elif task_type == WorkerTaskType.ACCOUNT_HISTORY_COLLECT:
                item = session.get(Account, subject_id)
                prefix = "history"
            else:
                item = session.get(Aweme, subject_id)
                prefix = "comment_collection"
            if item:
                token_field = f"{prefix}_run_token"
                if getattr(item, token_field) == token:
                    # V1.0 发布隐藏：清理已 claim 的隐藏任务，避免后续被误认为仍在运行。
                    setattr(item, f"{prefix}_status", TaskStatus.NOT_REQUESTED)
                    setattr(item, token_field, None)
                    setattr(item, f"{prefix}_heartbeat_at", None)
                    setattr(item, f"{prefix}_next_run_at", None)
                    session.add(item)
            worker_task = session.exec(
                select(WorkerTask).where(
                    WorkerTask.task_type == task_type,
                    WorkerTask.subject_type == subject_type,
                    WorkerTask.subject_id == subject_id,
                    WorkerTask.run_token == token,
                )
            ).first()
            if worker_task:
                session.delete(worker_task)
        return True

    def _transcribe(self, transcription_id: UUID, token: str) -> None:
        with self._session() as session:
            item = session.get(VideoTranscription, transcription_id)
            if not item or item.run_token != token or not item.video_path:
                return
        self.logger.info("执行视频转写：transcription_id=%s", transcription_id)
        self.container.transcriptions.run(item)

    def _sync(self, subject_type: WorkerSubject, sync_id: UUID, token: str) -> None:
        expected_object_type = _sync_object_type(subject_type)
        with self._session() as session:
            relation = session.get(ProviderSync, sync_id)
            if (
                not relation
                or relation.run_token != token
                or relation.object_type != expected_object_type
            ):
                return
            provider_record = session.get(Provider, relation.provider_id)
            if not provider_record:
                raise RuntimeError("provider not found")
            last_store_result = (
                StoreResult(
                    success=True,
                    success_payload=dict(relation.success_payload_json),
                )
                if relation.success_payload_json
                else None
            )
            if relation.object_type == SyncObjectType.AWEME:
                item = session.get(Aweme, relation.object_id)
                if not item:
                    raise RuntimeError("aweme not found")
            elif relation.object_type == SyncObjectType.ACCOUNT:
                item = session.get(Account, relation.object_id)
                if not item:
                    raise RuntimeError("account not found")
            else:
                item = session.get(
                    VideoTranscription,
                    relation.object_id,
                )
                if not item:
                    raise RuntimeError("video transcription not found")
            content_version = f"{item.id}:{item.updated_at.isoformat()}"
        if expected_object_type == SyncObjectType.AWEME:
            self.logger.info(
                "同步作品：sync_id=%s provider_id=%s",
                sync_id,
                provider_record.id,
            )
            result = self.container.stores.store_aweme(
                item,
                provider_record.id,
                last_store_result,
            )
        else:
            if expected_object_type == SyncObjectType.ACCOUNT:
                self.logger.info(
                    "同步账号：sync_id=%s provider_id=%s",
                    sync_id,
                    provider_record.id,
                )
                result = self.container.stores.store_account(
                    item,
                    provider_record.id,
                    last_store_result,
                )
            else:
                self.logger.info(
                    "同步转写：sync_id=%s provider_id=%s",
                    sync_id,
                    provider_record.id,
                )
                result = self.container.stores.store_transcription(
                    item,
                    provider_record.id,
                    last_store_result,
                )
        if not result.success:
            self.logger.error(
                "同步失败：sync_id=%s message=%s",
                sync_id,
                result.message or "store provider returned an unsuccessful result",
            )
            self._fail(
                WorkerTaskType.DATA_SYNC,
                subject_type,
                sync_id,
                token,
                result.message or "store provider returned an unsuccessful result",
            )
            return
        content_hash = hashlib.sha256(content_version.encode()).hexdigest()
        with self._session() as session:
            relation = session.get(ProviderSync, sync_id)
            if not relation or relation.run_token != token:
                return
            relation.success_payload_json = dict(result.success_payload)
            relation.last_synced_hash = content_hash
            relation.last_synced_at = now_utc()
            relation.finished_at = now_utc()
            relation.status = TaskStatus.SUCCEEDED
            relation.error_message = None
            session.add(relation)

    def _fail(
        self,
        task_type: WorkerTaskType,
        subject_type: WorkerSubject,
        subject_id: UUID,
        token: str,
        error: str,
    ) -> None:
        task_config = getattr(self.container.runtime_settings.worker_tasks, task_type.value)
        next_status = TaskStatus.FAILED
        with self._session() as session:
            if task_type == WorkerTaskType.DATA_COLLECT:
                model = Account if subject_type == WorkerSubject.ACCOUNT else Aweme
                item = session.get(model, subject_id)
                prefix = "collection"
            elif task_type == WorkerTaskType.ACCOUNT_HISTORY_COLLECT:
                item = session.get(Account, subject_id)
                prefix = "history"
            elif task_type == WorkerTaskType.MEDIA_DOWNLOAD:
                if subject_type == WorkerSubject.VIDEO_TRANSCRIPTION:
                    item = session.get(VideoTranscription, subject_id)
                    prefix = ""
                else:
                    item = session.get(Aweme, subject_id)
                    prefix = "media_download"
            elif task_type == WorkerTaskType.COMMENT_COLLECT:
                item = session.get(Aweme, subject_id)
                prefix = "comment_collection"
            elif task_type == WorkerTaskType.VIDEO_TRANSCRIPTION:
                item = session.get(VideoTranscription, subject_id)
                prefix = ""
            else:
                item = session.get(ProviderSync, subject_id)
                prefix = ""
            if not item:
                return
            token_field = f"{prefix}_run_token" if prefix else "run_token"
            if getattr(item, token_field) != token:
                return
            attempt_field = f"{prefix}_attempt_count" if prefix else "attempt_count"
            attempts = getattr(item, attempt_field)
            if attempts < task_config.retry_limit:
                next_status = TaskStatus.RETRY_WAIT
            status_field = f"{prefix}_status" if prefix else "status"
            error_field = (
                f"{prefix}_error"
                if prefix in {"collection", "media_download", "comment_collection", "history"}
                else "error_message"
            )
            next_field = f"{prefix}_next_run_at" if prefix else "next_run_at"
            setattr(item, status_field, next_status)
            setattr(item, error_field, error[:4000])
            setattr(
                item,
                next_field,
                now_utc() + timedelta(seconds=task_config.retry_delay_seconds)
                if next_status == TaskStatus.RETRY_WAIT
                else None,
            )
            setattr(item, token_field, None)
            session.add(item)


def _sync_object_type(subject_type: WorkerSubject) -> SyncObjectType:
    if subject_type == WorkerSubject.AWEME_SYNC:
        return SyncObjectType.AWEME
    if subject_type == WorkerSubject.ACCOUNT_SYNC:
        return SyncObjectType.ACCOUNT
    return SyncObjectType.VIDEO_TRANSCRIPTION
