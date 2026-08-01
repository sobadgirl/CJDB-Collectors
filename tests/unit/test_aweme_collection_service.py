from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlmodel import Session

from cjdb_collectors.data_provider import (
    AwemeData,
    AwemeProviderMixin,
    BaseDataProvider,
    DataProviderType,
    FetchAwemeRequest,
    ResolvedMedia,
    ResolveVideoRequest,
)
from cjdb_collectors.db import create_db_engine, init_db
from cjdb_collectors.models import ContentType, Platform, TaskStatus
from cjdb_collectors.services.awemes import AwemeService
from cjdb_collectors.services.base import NotFoundError
from cjdb_collectors.services.data_providers import (
    DataProviderService,
)


class FakeDouyin:
    def __init__(self) -> None:
        self.requested_id: str | None = None

    def fetch_one_video(self, aweme_id: str) -> AwemeData:
        self.requested_id = aweme_id
        return AwemeData(
            platform_aweme_id=aweme_id,
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
    def download(self, _url: str):
        raise AssertionError("download should not run")


class FakeAwemeProvider(BaseDataProvider, AwemeProviderMixin):
    namespace = "fake"
    name = "测试 Provider"
    supported_types = (DataProviderType.DOUYIN_AWEME_COLLECT,)
    platforms_by_type = {
        DataProviderType.DOUYIN_AWEME_COLLECT: {Platform.DOUYIN}
    }

    douyin: FakeDouyin | FailingDouyin

    def fetch_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        return self.douyin.fetch_one_video(request.platform_aweme_id)

    def resolve_video(self, request: ResolveVideoRequest) -> ResolvedMedia | None:
        return None


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
    assert result.like_count == 42
    assert result.extra_data_json == {"author": {"name": "作者"}}
    assert result.collection_status == TaskStatus.SUCCEEDED
    assert result.collection_run_token is None
    assert service.get(aweme.id).video_url == "https://cdn.test/video.mp4"
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
