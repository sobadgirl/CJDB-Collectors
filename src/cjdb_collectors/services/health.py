from __future__ import annotations

from sqlalchemy import text

from cjdb_collectors.domains.data_provider import DataProviderType

from .base import SessionFactory
from .data_providers import DataProviderService


class HealthService:
    def __init__(
        self,
        session_factory: SessionFactory,
        data_providers: DataProviderService,
    ) -> None:
        self._session = session_factory
        self.data_providers = data_providers

    def ready(self) -> dict:
        try:
            with self._session() as session:
                session.exec(text("SELECT 1"))
        except Exception as exc:
            return {
                "ready": False,
                "database": {"ready": False, "reason": str(exc)},
            }
        return {"ready": True, "database": {"ready": True, "reason": None}}

    def services(self) -> dict:
        collector = self.data_providers.status(
            DataProviderType.DOUYIN_AWEME_COLLECT
        )
        transcription = self.data_providers.status(
            DataProviderType.VIDEO_TRANSCRIPTION
        )
        return {
            "collector": {
                "ready": collector["status"] == "ready",
                "reason": collector.get("message"),
            },
            "transcription": {
                "ready": transcription["status"] == "ready",
                "reason": transcription.get("message"),
            },
        }

    services_health = services
