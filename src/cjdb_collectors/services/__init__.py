from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlmodel import Session

from cjdb_collectors.settings import Settings, load_settings
from cjdb_collectors.db import create_db_engine
from cjdb_collectors.domains.media import HttpMediaDownloader

from .accounts import AccountService
from .awemes import AwemeService
from .base import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
    ServiceError,
)
from .settings import SettingsService
from .data_providers import DataProviderService, build_data_provider_service
from .projects import ProjectService
from .health import HealthService
from .local_files import LocalFileService
from .logger import LoggerService
from .store_providers import StoreProviderService, build_store_provider_service
from .stores import StoreService
from .sync import SyncService
from .transcriptions import TranscriptionService
from .worker_tasks import WorkerService


@dataclass(slots=True)
class ServiceContainer:
    accounts: AccountService
    awemes: AwemeService
    projects: ProjectService
    stores: StoreService
    store_providers: StoreProviderService
    transcriptions: TranscriptionService
    sync: SyncService
    worker_tasks: WorkerService
    health: HealthService
    local_files: LocalFileService
    settings: SettingsService
    providers: DataProviderService
    media_downloader: HttpMediaDownloader
    aweme_media_downloader: HttpMediaDownloader
    logger: LoggerService
    runtime_settings: Settings

    def close(self) -> None:
        self.providers.close()
        self.media_downloader.close()
        self.aweme_media_downloader.close()


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
    aweme_media_downloader: HttpMediaDownloader | None = None,
    data_provider_service: DataProviderService | None = None,
    store_provider_service: StoreProviderService | None = None,
) -> ServiceContainer:
    settings = settings or load_settings()
    selected_engine = db_engine or create_db_engine(settings.app.database_path)
    sessions = session_factory or _default_session_factory(selected_engine)
    transcription_settings = settings.services.transcription
    media_downloader = media_downloader or HttpMediaDownloader(
        settings.services.media.transcription_download_dir
    )
    aweme_media_downloader = aweme_media_downloader or HttpMediaDownloader(
        settings.services.media.aweme_download_dir
    )
    settings_service = SettingsService(settings)
    LoggerService.configure(settings=settings)
    logger_service = LoggerService
    providers = data_provider_service or build_data_provider_service(
        settings=settings_service,
        session_factory=sessions,
        logger_service=logger_service,
    )
    store_providers = store_provider_service or build_store_provider_service(
        sessions,
    )
    stores = StoreService(
        sessions,
        store_providers,
        runtime_settings=settings,
        logger_service=logger_service,
    )
    projects = ProjectService(sessions)
    return ServiceContainer(
        accounts=AccountService(sessions, providers),
        awemes=AwemeService(
            sessions,
            providers,
            aweme_media_downloader,
        ),
        projects=projects,
        stores=stores,
        store_providers=store_providers,
        transcriptions=TranscriptionService(sessions, providers),
        sync=SyncService(sessions),
        worker_tasks=WorkerService(sessions, settings, logger_service),
        health=HealthService(sessions, providers),
        local_files=LocalFileService(
            transcription_settings.browse_roots,
            transcription_settings.allowed_video_extensions,
        ),
        settings=settings_service,
        providers=providers,
        media_downloader=media_downloader,
        aweme_media_downloader=aweme_media_downloader,
        logger=logger_service,
        runtime_settings=settings,
    )


__all__ = [
    "ConflictError",
    "InvalidOperationError",
    "NotFoundError",
    "ServiceContainer",
    "ServiceError",
    "build_services",
]
