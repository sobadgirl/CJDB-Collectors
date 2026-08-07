from types import SimpleNamespace
from datetime import timedelta
from contextlib import contextmanager

from sqlmodel import Session, select

from cjdb_collectors.domains.data_provider import DataProviderType
from cjdb_collectors.models import (
    Account,
    Aweme,
    Platform,
    Provider,
    ProviderSync,
    Project,
    ProjectAweme,
    SyncObjectType,
    TaskStatus,
    VideoTranscription,
    WorkerSubject,
    WorkerTask,
    WorkerTaskType,
)
from cjdb_collectors.services.data_providers import DataProviderService
from cjdb_collectors.services.sync import SyncService
from cjdb_collectors.services.transcriptions import TranscriptionService
from cjdb_collectors.settings import AppSettings, Settings
from cjdb_collectors.db import create_db_engine, init_db
from cjdb_collectors.services.base import now_utc
from cjdb_collectors.worker.scheduler import SlotResult, Worker


def test_worker_rotates_v1_slots(tmp_path, monkeypatch):
    engine = create_db_engine(":memory:")
    init_db(engine)
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )
    worker = Worker(config, services=SimpleNamespace(), db_engine=engine)
    calls: list[str] = []

    def record(name: str):
        def handler() -> SlotResult:
            calls.append(name)
            return SlotResult.EMPTY

        return handler

    monkeypatch.setattr(worker, "check_and_pull_data", record("data"))
    monkeypatch.setattr(worker, "process_timeout", record("timeout"))
    monkeypatch.setattr(worker, "check_and_download_media", record("media"))
    monkeypatch.setattr(worker, "check_and_transcribe", record("transcription"))
    monkeypatch.setattr(worker, "check_and_sync_data", record("sync"))
    monkeypatch.setattr(worker, "reset_stale", record("reset"))

    for _ in range(7):
        worker.run_once()

    assert calls == [
        "data",
        "timeout",
        "media",
        "timeout",
        "transcription",
        "sync",
        "reset",
    ]

    for _ in range(7):
        worker.run_once()

    assert calls == [
        "data",
        "timeout",
        "media",
        "timeout",
        "transcription",
        "sync",
        "reset",
        "data",
        "timeout",
        "timeout",
    ]
    engine.dispose()


def test_worker_finds_due_candidate_after_non_due_records(tmp_path):
    engine = create_db_engine(":memory:")
    init_db(engine)
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )
    worker = Worker(config, services=SimpleNamespace(), db_engine=engine)
    with Session(engine) as session:
        session.add(
            Account(
                platform=Platform.DOUYIN,
                profile_url="https://example.com/not-due",
                collection_status=TaskStatus.NOT_REQUESTED,
            )
        )
        due = Account(
            platform=Platform.DOUYIN,
            profile_url="https://example.com/due",
            collection_status=TaskStatus.PENDING,
        )
        session.add(due)
        due_aweme = Aweme(
            platform=Platform.DOUYIN,
            source_url="https://www.douyin.com/video/731234567890",
            collection_status=TaskStatus.PENDING,
        )
        session.add(due_aweme)
        session.commit()

        item, subject, prefix = worker._find_data_collect_candidate(session)

    assert item.id == due_aweme.id
    assert subject == WorkerSubject.AWEME
    assert prefix == "collection"
    engine.dispose()


def test_worker_finds_image_media_download_candidate(tmp_path):
    engine = create_db_engine(":memory:")
    init_db(engine)
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )
    worker = Worker(config, services=SimpleNamespace(), db_engine=engine)
    with Session(engine) as session:
        aweme = Aweme(
            platform=Platform.XIAOHONGSHU,
            source_url="https://www.xiaohongshu.com/explore/note-1",
            platform_aweme_id="note-1",
            photos=["https://cdn.test/photo.jpg"],
            media_download_status=TaskStatus.PENDING,
        )
        session.add(aweme)
        session.commit()

        item, subject, prefix = worker._find_media_download_candidate(session)

    assert item.id == aweme.id
    assert subject == WorkerSubject.AWEME
    assert prefix == "media_download"
    engine.dispose()


def test_worker_does_not_prepare_aweme_transcription_without_local_video(tmp_path):
    engine = create_db_engine(":memory:")
    init_db(engine)
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    worker = Worker(
        config,
        services=SimpleNamespace(
            transcriptions=TranscriptionService(sessions, DataProviderService([])),
        ),
        db_engine=engine,
    )
    with Session(engine) as session:
        aweme = Aweme(
            platform=Platform.DOUYIN,
            source_url="https://www.douyin.com/video/731234567890",
            video_transcription_status=TaskStatus.PENDING,
        )
        session.add(aweme)
        session.commit()

    assert worker._prepare_aweme_transcription_candidate() == SlotResult.EMPTY
    with Session(engine) as session:
        assert session.exec(select(VideoTranscription)).all() == []
    engine.dispose()


def test_worker_prepares_aweme_transcription_after_local_video_exists(tmp_path):
    engine = create_db_engine(":memory:")
    init_db(engine)
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    worker = Worker(
        config,
        services=SimpleNamespace(
            transcriptions=TranscriptionService(sessions, DataProviderService([])),
        ),
        db_engine=engine,
    )
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    with Session(engine) as session:
        aweme = Aweme(
            platform=Platform.DOUYIN,
            source_url="https://www.douyin.com/video/731234567890",
            video_path=str(video),
            video_transcription_status=TaskStatus.PENDING,
        )
        session.add(aweme)
        session.commit()
        aweme_id = aweme.id

    assert worker._prepare_aweme_transcription_candidate() == SlotResult.HANDLED
    with Session(engine) as session:
        transcription = session.exec(
            select(VideoTranscription).where(VideoTranscription.aweme_id == aweme_id)
        ).one()
        assert transcription.source_url is None
        assert transcription.video_path == str(video)
        item, subject, prefix = worker._find_transcription_candidate(session)

    assert item.id == transcription.id
    assert subject == WorkerSubject.VIDEO_TRANSCRIPTION
    assert prefix == ""
    engine.dispose()


def test_worker_prepares_project_requested_transcription_after_local_video_exists(tmp_path):
    engine = create_db_engine(":memory:")
    init_db(engine)
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )

    @contextmanager
    def sessions():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    worker = Worker(
        config,
        services=SimpleNamespace(
            transcriptions=TranscriptionService(sessions, DataProviderService([])),
        ),
        db_engine=engine,
    )
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    with Session(engine) as session:
        project = Project(name="Project")
        aweme = Aweme(
            platform=Platform.DOUYIN,
            source_url="https://www.douyin.com/video/731234567890",
            video_path=str(video),
            video_transcription_status=TaskStatus.NOT_REQUESTED,
        )
        session.add(project)
        session.add(aweme)
        session.flush()
        session.add(
            ProjectAweme(
                project_id=project.id,
                aweme_id=aweme.id,
                transcribe_enabled=True,
            )
        )
        session.commit()
        aweme_id = aweme.id

    assert worker._prepare_aweme_transcription_candidate() == SlotResult.HANDLED
    with Session(engine) as session:
        transcription = session.exec(
            select(VideoTranscription).where(VideoTranscription.aweme_id == aweme_id)
        ).one()
        aweme = session.get(Aweme, aweme_id)

    assert transcription.video_path == str(video)
    assert aweme.video_transcription_status == TaskStatus.PENDING
    engine.dispose()


def test_worker_uses_aweme_project_provider_for_video_transcription(tmp_path):
    engine = create_db_engine(":memory:")
    init_db(engine)
    calls: list[tuple[object, object | None]] = []
    providers = SimpleNamespace(
        is_ready=lambda provider_type, project_id=None: calls.append(
            (provider_type, project_id)
        )
        or True,
    )
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )
    worker = Worker(
        config,
        services=SimpleNamespace(providers=providers),
        db_engine=engine,
    )
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    with Session(engine) as session:
        project = Project(name="作品项目")
        aweme = Aweme(
            platform=Platform.DOUYIN,
            source_url="https://www.douyin.com/video/731234567890",
            video_path=str(video),
        )
        session.add(project)
        session.add(aweme)
        session.flush()
        project_id = project.id
        session.add(ProjectAweme(project_id=project.id, aweme_id=aweme.id))
        session.add(
            VideoTranscription(
                aweme_id=aweme.id,
                video_path=str(video),
                status=TaskStatus.PENDING,
            )
        )
        session.commit()

    worker_task_id, provider_blocked = worker._claim(
        WorkerTaskType.VIDEO_TRANSCRIPTION,
        300,
    )

    assert worker_task_id is not None
    assert provider_blocked is False
    assert calls == [(DataProviderType.VIDEO_TRANSCRIPTION, str(project_id))]
    engine.dispose()


def test_worker_checks_for_task_before_provider_status(tmp_path):
    engine = create_db_engine(":memory:")
    init_db(engine)
    checks: list[object] = []
    providers = SimpleNamespace(
        type_for_account=DataProviderService.type_for_account,
        is_ready=lambda provider_type: checks.append(provider_type) or True,
    )
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )
    worker = Worker(
        config,
        services=SimpleNamespace(providers=providers),
        db_engine=engine,
    )

    worker_task_id, provider_blocked = worker._claim(
        WorkerTaskType.DATA_COLLECT,
        300,
    )

    assert worker_task_id is None
    assert provider_blocked is False
    assert checks == []
    engine.dispose()


def test_worker_sync_ignores_hidden_comment_status(tmp_path):
    engine = create_db_engine(":memory:")
    init_db(engine)
    store_providers = SimpleNamespace(is_ready=lambda _provider_id: True)
    sync = SimpleNamespace(aweme_ready=SyncService.aweme_ready)
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )
    worker = Worker(
        config,
        services=SimpleNamespace(store_providers=store_providers, sync=sync),
        db_engine=engine,
    )
    with Session(engine) as session:
        provider = Provider(namespace="notion", name="Notion", status="ready")
        aweme = Aweme(
            platform=Platform.XIAOHONGSHU,
            source_url="https://www.xiaohongshu.com/explore/1",
            collection_status=TaskStatus.SUCCEEDED,
            media_download_status=TaskStatus.SUCCEEDED,
            comment_collection_status=TaskStatus.FAILED,
            video_transcription_status=TaskStatus.NOT_REQUESTED,
        )
        session.add(provider)
        session.add(aweme)
        session.flush()
        session.add(
            ProviderSync(
                object_type=SyncObjectType.AWEME,
                object_id=aweme.id,
                provider_id=provider.id,
            )
        )
        session.commit()

    worker_task_id, provider_blocked = worker._claim(WorkerTaskType.DATA_SYNC, 300)

    assert worker_task_id is not None
    assert provider_blocked is False
    engine.dispose()


def test_worker_does_not_claim_task_until_provider_is_ready(tmp_path):
    engine = create_db_engine(":memory:")
    init_db(engine)
    ready = False
    providers = SimpleNamespace(
        type_for_aweme=DataProviderService.type_for_aweme,
        is_ready=lambda _provider_type: ready,
    )
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )
    worker = Worker(
        config,
        services=SimpleNamespace(providers=providers),
        db_engine=engine,
    )
    with Session(engine) as session:
        aweme = Aweme(
            platform=Platform.DOUYIN,
            source_url="https://www.douyin.com/video/731234567890",
            collection_status=TaskStatus.PENDING,
        )
        session.add(aweme)
        session.commit()
        aweme_id = aweme.id

    worker_task_id, provider_blocked = worker._claim(
        WorkerTaskType.DATA_COLLECT,
        300,
    )

    assert worker_task_id is None
    assert provider_blocked is True
    with Session(engine) as session:
        aweme = session.get(Aweme, aweme_id)
        assert aweme is not None
        assert aweme.collection_status == TaskStatus.PENDING
        assert session.exec(select(WorkerTask)).all() == []

    ready = True
    worker_task_id, provider_blocked = worker._claim(
        WorkerTaskType.DATA_COLLECT,
        300,
    )

    assert worker_task_id is not None
    assert provider_blocked is False
    engine.dispose()


def test_worker_resets_orphan_running_aweme_to_retry_wait(tmp_path):
    engine = create_db_engine(":memory:")
    init_db(engine)
    config = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )
    worker = Worker(config, services=SimpleNamespace(), db_engine=engine)
    stale_heartbeat = now_utc() - timedelta(
        seconds=config.worker_tasks.data_collect.timeout_seconds + 1
    )

    with Session(engine) as session:
        aweme = Aweme(
            platform=Platform.DOUYIN,
            source_url="https://www.douyin.com/video/731234567890",
            collection_status=TaskStatus.RUNNING,
            collection_attempt_count=1,
            collection_run_token="lost-token",
            collection_heartbeat_at=stale_heartbeat,
        )
        session.add(aweme)
        session.commit()
        aweme_id = aweme.id

    assert worker.reset_stale() == SlotResult.HANDLED

    with Session(engine) as session:
        aweme = session.get(Aweme, aweme_id)

    assert aweme.collection_status == TaskStatus.RETRY_WAIT
    assert aweme.collection_run_token is None
    assert aweme.collection_heartbeat_at is None
    assert aweme.collection_next_run_at is not None
    assert aweme.collection_error == "running task lost worker heartbeat"
    engine.dispose()
