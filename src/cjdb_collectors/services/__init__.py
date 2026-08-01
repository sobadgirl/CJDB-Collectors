from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session

from cjdb_collectors.config import Settings, load_settings
from cjdb_collectors.db import engine as default_engine
from cjdb_collectors.media import HttpMediaDownloader

from .accounts import AccountService
from .awemes import AwemeService
from .base import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
    ServiceError,
)
from .configuration import ConfigurationService
from .data_providers import DataProviderService, build_data_provider_service
from .groups import GroupService
from .health import HealthService
from .local_files import LocalFileService
from .store_providers import StoreProviderService, build_store_provider_service
from .stores import StoreService
from .sync import SyncService
from .transcriptions import TranscriptionService
from .worker_tasks import WorkerService


@dataclass(slots=True)
class ServiceContainer:
    accounts: AccountService
    awemes: AwemeService
    groups: GroupService
    stores: StoreService
    store_providers: StoreProviderService
    transcriptions: TranscriptionService
    sync: SyncService
    worker_tasks: WorkerService
    health: HealthService
    local_files: LocalFileService
    config: ConfigurationService
    providers: DataProviderService
    media_downloader: HttpMediaDownloader
    settings: Settings

    def close(self) -> None:
        self.providers.close()
        self.store_providers.close()
        self.media_downloader.close()


def _default_session_factory(db_engine: Engine):
    @contextmanager
    def factory():
        with Session(db_engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return factory


def build_services(
    settings: Settings | None = None,
    session_factory=None,
    *,
    db_engine: Engine | None = None,
    media_downloader: HttpMediaDownloader | None = None,
    data_provider_service: DataProviderService | None = None,
    store_provider_service: StoreProviderService | None = None,
) -> ServiceContainer:
    settings = settings or load_settings()
    selected_engine = db_engine or default_engine
    sessions = session_factory or _default_session_factory(selected_engine)
    transcription_settings = settings.services.transcription
    media_downloader = media_downloader or HttpMediaDownloader(
        Path(settings.app.data_dir) / "media"
    )
    configuration = ConfigurationService(settings)
    providers = data_provider_service or build_data_provider_service(
        config=configuration,
    )
    store_providers = store_provider_service or build_store_provider_service(
        sessions,
        config=configuration,
        secrets=settings.secrets,
    )
    stores = StoreService(sessions, store_providers)
    return ServiceContainer(
        accounts=AccountService(sessions, providers),
        awemes=AwemeService(
            sessions,
            providers,
            media_downloader,
        ),
        groups=GroupService(sessions),
        stores=stores,
        store_providers=store_providers,
        transcriptions=TranscriptionService(sessions, providers),
        sync=SyncService(sessions),
        worker_tasks=WorkerService(sessions, settings),
        health=HealthService(sessions, providers),
        local_files=LocalFileService(
            transcription_settings.browse_roots,
            transcription_settings.allowed_video_extensions,
        ),
        config=configuration,
        providers=providers,
        media_downloader=media_downloader,
        settings=settings,
    )


__all__ = [
    "ConflictError",
    "InvalidOperationError",
    "NotFoundError",
    "ServiceContainer",
    "ServiceError",
    "build_services",
]
