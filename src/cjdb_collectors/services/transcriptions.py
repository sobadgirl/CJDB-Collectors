from __future__ import annotations

from uuid import UUID, uuid4

from sqlmodel import select

from cjdb_collectors.data_provider import (
    DataProviderType,
    TranscriptionRequest,
    VideoTranscriptionProviderMixin,
)
from cjdb_collectors.models import Aweme, TaskStatus, VideoTranscription

from .base import (
    InvalidOperationError,
    NotFoundError,
    SessionFactory,
    as_uuid,
    now_utc,
)
from .data_providers import DataProviderService


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
        with self._session() as session:
            aweme = None
            if aweme_id:
                aweme = session.get(Aweme, as_uuid(aweme_id))
                if not aweme:
                    raise NotFoundError("aweme not found")
                video_path = video_path or aweme.video_path
                source_url = source_url or aweme.video_url
                aweme.video_transcription_status = TaskStatus.PENDING
                if not video_path and source_url:
                    aweme.media_download_status = TaskStatus.PENDING
                session.add(aweme)
            item = VideoTranscription(
                aweme_id=aweme.id if aweme else None,
                video_path=video_path,
                source_url=source_url,
                status=TaskStatus.PENDING,
            )
            session.add(item)
            session.flush()
            session.refresh(item)
            return item

    def transcribe_aweme(self, aweme_id: UUID | str) -> VideoTranscription:
        return self.create(aweme_id=aweme_id)

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
            item.run_token = run_token
            if not claimed:
                item.attempt_count += 1
            video_path = item.video_path
            session.add(item)
        try:
            provider = self.data_providers.get_provider(
                DataProviderType.VIDEO_TRANSCRIPTION
            )
            if not isinstance(provider, VideoTranscriptionProviderMixin):
                raise InvalidOperationError(
                    f"provider {provider.namespace} does not support transcription"
                )
            result = provider.transcribe(
                TranscriptionRequest(video_path=video_path)
            )
        except Exception as exc:
            with self._session() as session:
                item = session.get(VideoTranscription, transcription_id)
                if item and item.run_token == run_token:
                    item.status = TaskStatus.FAILED
                    item.finished_at = now_utc()
                    item.error_message = str(exc)
                    item.run_token = None
                    session.add(item)
            raise
        with self._session() as session:
            item = session.get(VideoTranscription, transcription_id)
            if not item:
                raise NotFoundError("video transcription not found")
            if item.run_token != run_token:
                return item
            item.text = result.text
            item.normalized_text = result.normalized_text
            item.segments_json = result.segments
            item.progress = 1
            item.status = TaskStatus.SUCCEEDED
            item.finished_at = now_utc()
            item.error_message = None
            item.run_token = None
            session.add(item)
            if item.aweme_id:
                aweme = session.get(Aweme, item.aweme_id)
                if aweme:
                    aweme.transcription_text = (
                        result.normalized_text or result.text
                    )
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
            item.run_token = None
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
            session.add(item)
            session.flush()
            session.refresh(item)
            return item
