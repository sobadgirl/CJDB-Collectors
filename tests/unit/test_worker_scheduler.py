from types import SimpleNamespace

from cjdb_collectors.config import AppSettings, Settings
from cjdb_collectors.db import create_db_engine
from cjdb_collectors.worker.scheduler import SlotResult, Worker


def test_worker_rotates_slots_and_cools_down_empty_comments(tmp_path, monkeypatch):
    engine = create_db_engine(":memory:")
    settings = Settings(
        app=AppSettings(data_dir=tmp_path / "data", database_path=":memory:")
    )
    worker = Worker(settings, services=SimpleNamespace(), db_engine=engine)
    calls: list[str] = []

    def record(name: str):
        def handler() -> SlotResult:
            calls.append(name)
            return SlotResult.EMPTY

        return handler

    monkeypatch.setattr(worker, "check_and_pull_data", record("data"))
    monkeypatch.setattr(worker, "process_timeout", record("timeout"))
    monkeypatch.setattr(worker, "check_and_run_comments", record("comments"))
    monkeypatch.setattr(worker, "check_and_download_media", record("media"))
    monkeypatch.setattr(worker, "check_and_transcribe", record("transcription"))
    monkeypatch.setattr(worker, "check_and_sync_data", record("sync"))
    monkeypatch.setattr(worker, "reset_stale", record("reset"))

    for _ in range(8):
        worker.run_once()

    assert calls == [
        "data",
        "timeout",
        "comments",
        "media",
        "timeout",
        "transcription",
        "sync",
        "reset",
    ]

    for _ in range(8):
        worker.run_once()

    assert calls == [
        "data",
        "timeout",
        "comments",
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
