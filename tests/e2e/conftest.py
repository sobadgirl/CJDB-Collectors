from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from cjdb_collectors.config import (
    AppSettings,
    CollectorServiceSettings,
    ServicesSettings,
    Settings,
    TranscriptionServiceSettings,
)
from cjdb_collectors.db import create_db_engine, migrate_database
from cjdb_collectors.main import create_app
from cjdb_collectors.services import build_services


@pytest.fixture
def api_client(tmp_path: Path) -> Iterator[TestClient]:
    """Run the real HTTP app against an isolated SQLite database.

    External collector and transcription services are deliberately disabled:
    the E2E suite verifies that management APIs remain usable in that state.
    """

    config_path = tmp_path / "config.yaml"
    settings = Settings(
        app=AppSettings(
            data_dir=tmp_path / "data",
            database_path=tmp_path / "e2e.sqlite",
            logs_dir=tmp_path / "logs",
        ),
        services=ServicesSettings(
            collector=CollectorServiceSettings(enabled=False),
            transcription=TranscriptionServiceSettings(
                enabled=False,
                browse_roots=[tmp_path],
            ),
        ),
        config_path=config_path,
    )
    config_path.write_text(
        yaml.safe_dump(
            settings.model_dump(mode="json", exclude={"config_path"}),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    migrate_database(settings)
    engine = create_db_engine(settings.app.database_path)
    services = build_services(settings=settings, db_engine=engine)
    with TestClient(create_app(services=services)) as client:
        yield client
    engine.dispose()
