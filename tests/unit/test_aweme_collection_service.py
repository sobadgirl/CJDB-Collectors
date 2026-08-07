from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlmodel import Session, select

from cjdb_collectors.domains.data_provider import (
    AwemeData,
    BaseDataProvider,
    DataProviderType,
    DouyinAwemeProviderMixin,
    FetchAwemeRequest,
    SetupResult,
    ProviderStatus,
)
from cjdb_collectors.db import create_db_engine, init_db
from cjdb_collectors.models import ContentType, Platform, TaskStatus, VideoTranscription
from cjdb_collectors.services.awemes import AwemeService
from cjdb_collectors.services.base import InvalidOperationError, NotFoundError
from cjdb_collectors.services.data_providers import (
    DataProviderService,
)
from cjdb_collectors.services.transcriptions import (
    TranscriptionService,
    transcription_summary,
)


class FakeDouyin:
    def __init__(self) -> None:
        self.requested_id: str | None = None

    def fetch_one_video(self, aweme_id: str) -> AwemeData:
        self.requested_id = aweme_id
        return AwemeData(
            platform_aweme_id=aweme_id,
            platform_account_id="sec-author",
            content_type=ContentType.VIDEO,
            title="清洗后的标题",
            description="清洗后的正文",
            video_url="https://cdn.test/video.mp4",
            like_count=42,
            extra_data_json={"author": {"name": "作者"}},
        )


class FailingDouyin:
    def fetch_one_video(self, _aweme_id: str) -> AwemeData:
        raise RuntimeError("remote unavailable")


class UnusedDownloader:
    def download(self, _url: str, **_kwargs):
        raise AssertionError("download should not run")


class RecordingDownloader:
    def __init__(self, root):
        self.root = root
        self.calls = []

    def download(self, url: str, *, media_type=None, subdir=None):
        from cjdb_collectors.domains.media import DownloadResult

        self.calls.append((url, media_type, subdir))
        directory = self.root / subdir
        directory.mkdir(parents=True, exist_ok=True)
        suffix = ".mp4" if media_type == "video" else ".jpg"
        path = directory / f"{len(self.calls)}{suffix}"
        path.write_bytes(url.encode())
        return DownloadResult(path, f"sha-{len(self.calls)}", path.stat().st_size, None)


class FakeAwemeProvider(BaseDataProvider, DouyinAwemeProviderMixin):
    namespace = "fake"
    name = "测试 Provider"
    supported_types = (DataProviderType.DOUYIN_AWEME_COLLECT,)

    douyin: FakeDouyin | FailingDouyin

    def refresh_status(self) -> ProviderStatus:
        return super().refresh_status()

    def setup(self, params: dict[str, object]) -> SetupResult:
        return SetupResult(success=True, setup_payload=params)

    def fetch_douyin_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        return self.douyin.fetch_one_video(request.platform_aweme_id)


def _providers(douyin: FakeDouyin | FailingDouyin) -> DataProviderService:
    provider_type = DataProviderType.DOUYIN_AWEME_COLLECT
    FakeAwemeProvider.douyin = douyin
    return DataProviderService(
        [FakeAwemeProvider],
        selected={provider_type.value: "fake"},
    )


def test_fetch_aweme_accepts_persisted_object_dispatches_and_saves() -> None:
    engine = create_db_engine(":memory:")
    init_db(engine)

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    douyin = FakeDouyin()
    service = AwemeService(
        sessions,
        _providers(douyin),
        UnusedDownloader(),
    )
    aweme = service.create(
        "https://www.douyin.com/video/731234",
        platform_aweme_id="731234",
        content_type=ContentType.VIDEO,
    )
    assert aweme.collection_status == TaskStatus.NOT_REQUESTED

    result = service.fetch_aweme(aweme)

    assert douyin.requested_id == "731234"
    assert result.aweme_url == "https://www.douyin.com/video/731234"
    assert result.title == "清洗后的标题"
    assert result.platform_account_id == "sec-author"
    assert result.like_count == 42
    assert result.extra_data_json == {"author": {"name": "作者"}}
    assert result.collection_status == TaskStatus.SUCCEEDED
    assert result.collection_run_token is None
    assert service.get(aweme.id).video_url == "https://cdn.test/video.mp4"
    engine.dispose()


def test_transcribe_request_requires_local_video_download_first() -> None:
    engine = create_db_engine(":memory:")
    init_db(engine)

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    service = AwemeService(
        sessions,
        _providers(FakeDouyin()),
        UnusedDownloader(),
    )
    aweme = service.create(
        "https://www.douyin.com/video/731234",
        platform_aweme_id="731234",
        content_type=ContentType.VIDEO,
        download_video=False,
        transcribe=True,
    )

    assert aweme.media_download_status == TaskStatus.PENDING
    assert aweme.video_transcription_status == TaskStatus.NOT_REQUESTED
    with sessions() as session:
        transcriptions = session.exec(
            select(VideoTranscription).where(VideoTranscription.aweme_id == aweme.id)
        ).all()
        assert transcriptions == []
    engine.dispose()


def test_transcribe_aweme_does_not_use_remote_video_url() -> None:
    engine = create_db_engine(":memory:")
    init_db(engine)

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    provider_service = _providers(FakeDouyin())
    aweme_service = AwemeService(
        sessions,
        provider_service,
        UnusedDownloader(),
    )
    transcription_service = TranscriptionService(sessions, provider_service)
    aweme = aweme_service.create(
        "https://www.douyin.com/video/731234",
        platform_aweme_id="731234",
        content_type=ContentType.VIDEO,
    )
    with sessions() as session:
        current = session.get(type(aweme), aweme.id)
        current.video_url = "https://cdn.test/v.mp4"
        current.video_path = None
        session.add(current)

    with pytest.raises(InvalidOperationError, match="local video path"):
        transcription_service.transcribe_aweme(aweme.id)

    with sessions() as session:
        transcriptions = session.exec(
            select(VideoTranscription).where(VideoTranscription.aweme_id == aweme.id)
        ).all()
        assert transcriptions == []
    current = aweme_service.get(aweme.id)
    assert current.media_download_status == TaskStatus.PENDING
    assert current.video_transcription_status == TaskStatus.NOT_REQUESTED
    engine.dispose()


def test_aweme_media_download_saves_cover_video_and_photos_by_platform_id(tmp_path) -> None:
    engine = create_db_engine(":memory:")
    init_db(engine)

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    downloader = RecordingDownloader(tmp_path)
    service = AwemeService(
        sessions,
        _providers(FakeDouyin()),
        downloader,
    )
    aweme = service.create(
        "https://www.douyin.com/video/731234",
        platform_aweme_id="731234",
        content_type=ContentType.VIDEO,
    )
    with sessions() as session:
        current = session.get(type(aweme), aweme.id)
        current.cover_url = "https://cdn.test/cover"
        current.video_url = "https://cdn.test/video"
        current.photos = ["https://cdn.test/p1", "https://cdn.test/p2"]
        current.media_download_status = TaskStatus.PENDING
        session.add(current)

    result = service.download_media(service.get(aweme.id))

    assert result.cover_path.endswith("/731234/cover/1.jpg")
    assert result.video_path.endswith("/731234/video/2.mp4")
    assert result.photo_paths == [
        {"url": "https://cdn.test/p1", "local_path": str(tmp_path / "731234/photo/3.jpg")},
        {"url": "https://cdn.test/p2", "local_path": str(tmp_path / "731234/photo/4.jpg")},
    ]
    assert downloader.calls == [
        ("https://cdn.test/cover", "image", Path("731234/cover")),
        ("https://cdn.test/video", "video", Path("731234/video")),
        ("https://cdn.test/p1", "image", Path("731234/photo")),
        ("https://cdn.test/p2", "image", Path("731234/photo")),
    ]
    engine.dispose()


def test_create_and_fetch_aweme_extracts_platform_id_from_url() -> None:
    engine = create_db_engine(":memory:")
    init_db(engine)

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    douyin = FakeDouyin()
    service = AwemeService(
        sessions,
        _providers(douyin),
        UnusedDownloader(),
    )
    aweme = service.create(
        "https://www.douyin.com/video/731234?previous_page=app_code_link",
        content_type=ContentType.VIDEO,
    )

    result = service.fetch_data(aweme)

    assert aweme.platform_aweme_id == "731234"
    assert douyin.requested_id == "731234"
    assert result.collection_status == TaskStatus.SUCCEEDED
    engine.dispose()


def test_fetch_aweme_failure_records_failed_attempt_only() -> None:
    engine = create_db_engine(":memory:")
    init_db(engine)

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    service = AwemeService(
        sessions,
        _providers(FailingDouyin()),
        UnusedDownloader(),
    )
    aweme = service.create(
        "https://www.douyin.com/video/731234",
        platform_aweme_id="731234",
        content_type=ContentType.VIDEO,
    )

    with pytest.raises(RuntimeError, match="remote unavailable"):
        service.fetch_data(aweme)
    first = service.get(aweme.id)
    assert first.collection_attempt_count == 1
    assert first.collection_status == TaskStatus.FAILED
    assert first.collection_next_run_at is None
    assert first.collection_run_token is None
    assert first.collection_error == "remote unavailable"

    with pytest.raises(RuntimeError, match="remote unavailable"):
        service.fetch_data(first)
    second = service.get(aweme.id)
    assert second.collection_attempt_count == 2
    assert second.collection_status == TaskStatus.FAILED
    assert second.collection_next_run_at is None
    assert second.collection_run_token is None
    engine.dispose()


def test_delete_aweme_can_remove_downloaded_local_files(tmp_path) -> None:
    engine = create_db_engine(":memory:")
    init_db(engine)

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    service = AwemeService(
        sessions,
        _providers(FakeDouyin()),
        UnusedDownloader(),
    )
    aweme = service.create(
        "https://www.xiaohongshu.com/explore/note-1",
        platform="xiaohongshu",
        platform_aweme_id="note-1",
        content_type=ContentType.IMAGE,
    )
    video = tmp_path / "video.mp4"
    cover = tmp_path / "cover.jpg"
    photo = tmp_path / "photo.jpg"
    for path in (video, cover, photo):
        path.write_text("data", encoding="utf-8")
    with sessions() as session:
        current = session.get(type(aweme), aweme.id)
        current.video_path = str(video)
        current.cover_path = str(cover)
        current.photos = ["https://cdn.test/remote.jpg"]
        current.photo_paths = [str(photo)]
        session.add(current)

    files = service.deletion_files(service.get(aweme.id))
    assert [item["label"] for item in files] == ["视频", "封面", "图片 1"]

    service.delete(aweme.id, delete_downloaded_files=True)

    assert not video.exists()
    assert not cover.exists()
    assert not photo.exists()
    with pytest.raises(NotFoundError):
        service.get(aweme.id)
    engine.dispose()


def test_media_download_can_be_requested_and_cancelled() -> None:
    engine = create_db_engine(":memory:")
    init_db(engine)

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    service = AwemeService(
        sessions,
        _providers(FakeDouyin()),
        UnusedDownloader(),
    )
    aweme = service.create(
        "https://www.xiaohongshu.com/explore/note-1",
        platform=Platform.XIAOHONGSHU,
        platform_aweme_id="note-1",
        content_type=ContentType.IMAGE,
    )
    with pytest.raises(InvalidOperationError):
        service.request_media_download(aweme.id)

    with sessions() as session:
        current = session.get(type(aweme), aweme.id)
        current.photos = ["https://cdn.test/photo.jpg"]
        session.add(current)

    requested = service.request_media_download(aweme.id)
    assert requested.media_download_status == TaskStatus.PENDING
    assert requested.media_download_run_token is None

    cancelled = service.cancel_media_download(aweme.id)
    assert cancelled.media_download_status == TaskStatus.CANCELLED
    assert cancelled.media_download_run_token is None
    engine.dispose()


def test_transcription_summary_uses_first_50_chars_with_ascii_ellipsis() -> None:
    short_text = "这是一个短视频转写内容"
    long_text = "一" * 51

    assert transcription_summary(short_text) == short_text
    assert transcription_summary(long_text) == ("一" * 50) + "..."
