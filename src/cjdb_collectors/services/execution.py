from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import UUID

from cjdb_collectors.models import (
    Account,
    AccountDataStorerSync,
    Aweme,
    AwemeDataStorerSync,
    DataStorer,
    WorkerSubject,
    WorkerTask,
    WorkerTaskType,
    TaskStatus,
    VideoTranscription,
)

from .base import SessionFactory, now_utc


class ExecutionService:
    """Executes one already-claimed record and performs token-guarded writes."""

    def __init__(
        self,
        session_factory: SessionFactory,
        container,
    ) -> None:
        self._session = session_factory
        self.container = container

    def run(self, worker_task_id: UUID) -> None:
        with self._session() as session:
            worker_task = session.get(WorkerTask, worker_task_id)
            if not worker_task:
                return
            task_type = worker_task.task_type
            subject_type = worker_task.subject_type
            subject_id = worker_task.subject_id
            token = worker_task.run_token
        try:
            if task_type == WorkerTaskType.DATA_COLLECT:
                self._collect(subject_type, subject_id, token)
            elif task_type == WorkerTaskType.MEDIA_DOWNLOAD:
                self._download(subject_type, subject_id, token)
            elif task_type == WorkerTaskType.COMMENT_COLLECT:
                self._comments(subject_id, token)
            elif task_type == WorkerTaskType.VIDEO_TRANSCRIPTION:
                self._transcribe(subject_id, token)
            elif task_type == WorkerTaskType.DATA_SYNC:
                self._sync(subject_type, subject_id, token)
        except Exception as exc:
            self._fail(task_type, subject_type, subject_id, token, str(exc))
            raise
        finally:
            with self._session() as session:
                worker_task = session.get(WorkerTask, worker_task_id)
                if worker_task and worker_task.run_token == token:
                    session.delete(worker_task)

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
            self.container.awemes.fetch_data(aweme)
            return
        if subject_type == WorkerSubject.ACCOUNT:
            self.container.accounts.fetch_data(account)

    def _download(
        self, subject_type: WorkerSubject, subject_id: UUID, token: str
    ) -> None:
        if subject_type == WorkerSubject.VIDEO_TRANSCRIPTION:
            with self._session() as session:
                item = session.get(VideoTranscription, subject_id)
                if not item or item.run_token != token or not item.source_url:
                    return
                source_url = item.source_url
            result = self.container.media_downloader.download(source_url)
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
        self.container.awemes.download_video(aweme)

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

    def _transcribe(self, transcription_id: UUID, token: str) -> None:
        with self._session() as session:
            item = session.get(VideoTranscription, transcription_id)
            if not item or item.run_token != token or not item.video_path:
                return
        self.container.transcriptions.run(item)

    def _sync(self, subject_type: WorkerSubject, sync_id: UUID, token: str) -> None:
        sync_model = (
            AwemeDataStorerSync
            if subject_type == WorkerSubject.AWEME_SYNC
            else AccountDataStorerSync
        )
        with self._session() as session:
            relation = session.get(sync_model, sync_id)
            if not relation or relation.run_token != token:
                return
            store_record = session.get(DataStorer, relation.data_storer_id)
            if not store_record:
                raise RuntimeError("data storer not found")
            remote_record_id = relation.remote_record_id
            if subject_type == WorkerSubject.AWEME_SYNC:
                item = session.get(Aweme, relation.aweme_id)
                if not item:
                    raise RuntimeError("aweme not found")
            else:
                item = session.get(Account, relation.account_id)
                if not item:
                    raise RuntimeError("account not found")
            content_version = f"{item.id}:{item.updated_at.isoformat()}"
        storer = self.container.store_providers.get_storer(store_record.id)
        if subject_type == WorkerSubject.AWEME_SYNC:
            result = self.container.stores.store_aweme(
                item,
                storer,
                remote_record_id,
            )
        else:
            result = self.container.stores.store_account(
                item,
                storer,
                remote_record_id,
            )
        content_hash = hashlib.sha256(content_version.encode()).hexdigest()
        with self._session() as session:
            relation = session.get(sync_model, sync_id)
            if not relation or relation.run_token != token:
                return
            relation.remote_record_id = result.remote_record_id
            relation.remote_url = result.remote_url
            relation.remote_attachment_json = dict(result.remote_attachment)
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
        task_config = getattr(self.container.settings.worker_tasks, task_type.value)
        next_status = TaskStatus.FAILED
        with self._session() as session:
            if task_type == WorkerTaskType.DATA_COLLECT:
                model = Account if subject_type == WorkerSubject.ACCOUNT else Aweme
                item = session.get(model, subject_id)
                prefix = "collection"
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
                model = (
                    AwemeDataStorerSync
                    if subject_type == WorkerSubject.AWEME_SYNC
                    else AccountDataStorerSync
                )
                item = session.get(model, subject_id)
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
                if prefix in {"collection", "media_download", "comment_collection"}
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
            session.add(item)
