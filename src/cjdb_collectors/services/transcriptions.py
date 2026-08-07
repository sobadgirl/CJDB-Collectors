from __future__ import annotations

from time import perf_counter
from collections.abc import Iterable
from uuid import UUID, uuid4

from sqlmodel import select

from cjdb_collectors.domains.data_provider import (
    DataProviderType,
    TranscriptionRequest,
    VideoTranscriptionProviderMixin,
)
from cjdb_collectors.models import Aweme, ProjectAweme, TaskStatus, VideoTranscription
from cjdb_collectors.models.enums import display_task_status

from .base import (
    InvalidOperationError,
    NotFoundError,
    SessionFactory,
    as_uuid,
    now_utc,
)
from .data_providers import DataProviderService
from .store_relations import ensure_transcription_store_relations


def transcription_summary(text: str | None, *, max_chars: int = 50) -> str | None:
    if not text:
        return None
    normalized = " ".join(text.split())
    if not normalized:
        return None
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars]}..."


class TranscriptionService:
    def __init__(
        self,
        session_factory: SessionFactory,
        data_providers: DataProviderService,
    ) -> None:
        self._session = session_factory
        self.data_providers = data_providers

    def create(
        self,
        video_path: str | None = None,
        *,
        video_url: str | None = None,
        url: str | None = None,
        aweme_id: UUID | str | None = None,
    ) -> VideoTranscription:
        source_url = video_url or url
        if not video_path and not source_url and not aweme_id:
            raise InvalidOperationError("video path, URL, or aweme_id is required")
        missing_local_path = False
        with self._session() as session:
            aweme = None
            if aweme_id:
                aweme = session.get(Aweme, as_uuid(aweme_id))
                if not aweme:
                    raise NotFoundError("aweme not found")
                video_path = video_path or aweme.video_path
                # V1.0：从作品发起的转写不使用远程视频 URL，等待作品媒体下载回填本地路径。
                source_url = None
                if not video_path:
                    aweme.media_download_status = TaskStatus.PENDING
                    session.add(aweme)
                    missing_local_path = True
                    item = None
                else:
                    item = session.exec(
                        select(VideoTranscription).where(
                            VideoTranscription.aweme_id == aweme.id,
                            VideoTranscription.is_current.is_(True),
                            VideoTranscription.status.in_(
                                [
                                    TaskStatus.PENDING,
                                    TaskStatus.RUNNING,
                                    TaskStatus.RETRY_WAIT,
                                ]
                            ),
                        )
                    ).first()
                    if item:
                        if not item.video_path:
                            item.video_path = video_path
                        item.source_url = None
                        item.status = TaskStatus.PENDING
                        item.next_run_at = None
                        session.add(item)
                        session.flush()
                        ensure_transcription_store_relations(session, item.id)
                        session.refresh(item)
                        return item
                session.add(aweme)
            if not missing_local_path:
                if aweme:
                    aweme.video_transcription_status = TaskStatus.PENDING
                    session.add(aweme)
                item = VideoTranscription(
                    aweme_id=aweme.id if aweme else None,
                    video_path=video_path,
                    source_url=source_url,
                    status=TaskStatus.PENDING,
                )
                session.add(item)
                session.flush()
                ensure_transcription_store_relations(session, item.id)
                session.refresh(item)
                return item
        raise InvalidOperationError("aweme has no local video path")

    def transcribe_aweme(self, aweme_id: UUID | str) -> VideoTranscription:
        item = self.request_aweme_transcription(aweme_id)
        if item is None:
            raise InvalidOperationError("aweme has no local video path")
        return item

    def request_aweme_transcription(
        self,
        aweme_id: UUID | str,
    ) -> VideoTranscription | None:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme:
                raise NotFoundError("aweme not found")
            if not aweme.video_path:
                aweme.media_download_status = TaskStatus.PENDING
                session.add(aweme)
                return None
            aweme.video_transcription_status = TaskStatus.PENDING
            item = session.exec(
                select(VideoTranscription)
                .where(
                    VideoTranscription.aweme_id == aweme.id,
                    VideoTranscription.is_current.is_(True),
                )
                .order_by(VideoTranscription.created_at.desc())
                .limit(1)
            ).first()
            if item:
                if item.status == TaskStatus.RUNNING:
                    session.add(aweme)
                    return item
                item.video_path = aweme.video_path
                item.source_url = None
                item.status = TaskStatus.PENDING
                item.progress = 0
                item.next_run_at = None
                item.error_message = None
                item.duration_seconds = None
                item.run_token = None
            else:
                item = VideoTranscription(
                    aweme_id=aweme.id,
                    video_path=aweme.video_path,
                    status=TaskStatus.PENDING,
                )
            session.add(aweme)
            session.add(item)
            session.flush()
            ensure_transcription_store_relations(session, item.id)
            session.refresh(item)
            return item

    def cancel_aweme_transcription(self, aweme_id: UUID | str) -> None:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme:
                raise NotFoundError("aweme not found")
            item = session.exec(
                select(VideoTranscription)
                .where(
                    VideoTranscription.aweme_id == aweme.id,
                    VideoTranscription.is_current.is_(True),
                )
                .order_by(VideoTranscription.created_at.desc())
                .limit(1)
            ).first()
            if item:
                item.status = TaskStatus.CANCELLED
                item.run_token = None
                item.next_run_at = None
                session.add(item)
            aweme.video_transcription_status = TaskStatus.CANCELLED
            session.add(aweme)

    def run(
        self,
        transcription: VideoTranscription | UUID | str,
    ) -> VideoTranscription:
        transcription_id = (
            transcription.id
            if isinstance(transcription, VideoTranscription)
            else as_uuid(transcription)
        )
        with self._session() as session:
            item = session.get(VideoTranscription, transcription_id)
            if not item:
                raise NotFoundError("video transcription not found")
            if not item.video_path:
                raise InvalidOperationError("transcription has no local video path")
            claimed = bool(
                item.run_token and item.status == TaskStatus.RUNNING
            )
            run_token = item.run_token if claimed else uuid4().hex
            item.status = TaskStatus.RUNNING
            item.started_at = now_utc()
            item.heartbeat_at = now_utc()
            item.error_message = None
            item.duration_seconds = None
            item.run_token = run_token
            if not claimed:
                item.attempt_count += 1
            video_path = item.video_path
            project_id = (
                session.exec(
                    select(ProjectAweme.project_id)
                    .where(ProjectAweme.aweme_id == item.aweme_id)
                    .limit(1)
                ).first()
                if item.aweme_id
                else None
            )
            session.add(item)
        try:
            provider = self.data_providers.get_provider(
                DataProviderType.VIDEO_TRANSCRIPTION,
                str(project_id) if project_id else None,
            )
            if not isinstance(provider, VideoTranscriptionProviderMixin):
                raise InvalidOperationError(
                    f"provider {provider.namespace} does not support transcription"
                )
            started = perf_counter()
            result = provider.transcribe(
                TranscriptionRequest(video_path=video_path)
            )
            duration_seconds = perf_counter() - started
        except Exception as exc:
            duration_seconds = (
                perf_counter() - started if "started" in locals() else None
            )
            with self._session() as session:
                item = session.get(VideoTranscription, transcription_id)
                if item and item.run_token == run_token:
                    item.status = TaskStatus.FAILED
                    item.finished_at = now_utc()
                    item.error_message = str(exc)
                    item.duration_seconds = duration_seconds
                    item.run_token = None
                    session.add(item)
                    if item.aweme_id:
                        aweme = session.get(Aweme, item.aweme_id)
                        if aweme:
                            aweme.video_transcription_status = TaskStatus.FAILED
                            session.add(aweme)
            raise
        with self._session() as session:
            item = session.get(VideoTranscription, transcription_id)
            if not item:
                raise NotFoundError("video transcription not found")
            if item.run_token != run_token:
                return item
            item.text = result.text
            item.normalized_text = result.text
            item.text_summary = transcription_summary(result.text)
            item.segments_json = []
            item.duration_seconds = duration_seconds
            item.progress = 1
            item.status = TaskStatus.SUCCEEDED
            item.finished_at = now_utc()
            item.error_message = None
            item.run_token = None
            session.add(item)
            if item.aweme_id:
                aweme = session.get(Aweme, item.aweme_id)
                if aweme:
                    aweme.transcription_text = result.text
                    aweme.transcription_updated_at = now_utc()
                    aweme.video_transcription_status = TaskStatus.SUCCEEDED
                    session.add(aweme)
            session.flush()
            session.refresh(item)
            return item

    def list(
        self,
        *,
        status: str | None = None,
        aweme_id: UUID | str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[VideoTranscription]:
        with self._session() as session:
            statement = select(VideoTranscription)
            if status:
                statement = statement.where(
                    VideoTranscription.status == TaskStatus(status)
                )
            if aweme_id:
                statement = statement.where(
                    VideoTranscription.aweme_id == as_uuid(aweme_id)
                )
            statement = statement.order_by(VideoTranscription.created_at.desc()).offset(
                offset
            )
            if limit is not None:
                statement = statement.limit(limit)
            return list(session.exec(statement).all())

    def current_by_aweme_ids(
        self, aweme_ids: Iterable[UUID | str]
    ) -> dict[str, VideoTranscription]:
        selected_ids = [as_uuid(value) for value in aweme_ids]
        if not selected_ids:
            return {}
        with self._session() as session:
            statement = (
                select(VideoTranscription)
                .where(
                    VideoTranscription.aweme_id.in_(selected_ids),
                    VideoTranscription.is_current.is_(True),
                )
                .order_by(VideoTranscription.created_at.desc())
            )
            mapped: dict[str, VideoTranscription] = {}
            for item in session.exec(statement).all():
                if item.aweme_id is not None:
                    mapped.setdefault(str(item.aweme_id), item)
            return mapped

    def list_summaries(
        self,
        *,
        status: str | None = None,
        aweme_id: UUID | str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        with self._session() as session:
            statement = select(
                VideoTranscription.id,
                VideoTranscription.aweme_id,
                VideoTranscription.source_url,
                VideoTranscription.video_path,
                VideoTranscription.status,
                VideoTranscription.progress,
                VideoTranscription.duration_seconds,
                VideoTranscription.attempt_count,
                VideoTranscription.error_message,
                VideoTranscription.text_summary,
                VideoTranscription.created_at,
                VideoTranscription.updated_at,
            )
            if status:
                statement = statement.where(
                    VideoTranscription.status == TaskStatus(status)
                )
            if aweme_id:
                statement = statement.where(
                    VideoTranscription.aweme_id == as_uuid(aweme_id)
                )
            statement = statement.order_by(VideoTranscription.created_at.desc()).offset(
                offset
            )
            if limit is not None:
                statement = statement.limit(limit)
            rows = session.exec(statement).all()
            return [
                {
                    "id": row.id,
                    "aweme_id": row.aweme_id,
                    "source_url": row.source_url,
                    "video_path": row.video_path,
                    "status": row.status,
                    "status_display": display_task_status(row.status),
                    "progress": row.progress,
                    "duration_seconds": row.duration_seconds,
                    "attempt_count": row.attempt_count,
                    "error_message": row.error_message,
                    "text_summary": row.text_summary,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in rows
            ]

    def get(self, transcription_id: UUID | str) -> VideoTranscription:
        with self._session() as session:
            item = session.get(VideoTranscription, as_uuid(transcription_id))
            if not item:
                raise NotFoundError("video transcription not found")
            return item

    def retry(self, transcription_id: UUID | str) -> VideoTranscription:
        with self._session() as session:
            item = session.get(VideoTranscription, as_uuid(transcription_id))
            if not item:
                raise NotFoundError("video transcription not found")
            item.status = TaskStatus.PENDING
            item.next_run_at = None
            item.error_message = None
            item.duration_seconds = None
            item.run_token = None
            if item.aweme_id:
                aweme = session.get(Aweme, item.aweme_id)
                if aweme:
                    aweme.video_transcription_status = TaskStatus.PENDING
                    session.add(aweme)
            session.add(item)
            session.flush()
            session.refresh(item)
            return item

    def cancel(self, transcription_id: UUID | str) -> VideoTranscription:
        with self._session() as session:
            item = session.get(VideoTranscription, as_uuid(transcription_id))
            if not item:
                raise NotFoundError("video transcription not found")
            item.status = TaskStatus.CANCELLED
            item.run_token = None
            if item.aweme_id:
                aweme = session.get(Aweme, item.aweme_id)
                if aweme:
                    aweme.video_transcription_status = TaskStatus.CANCELLED
                    session.add(aweme)
            session.add(item)
            session.flush()
            session.refresh(item)
            return item
