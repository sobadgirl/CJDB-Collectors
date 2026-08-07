from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import json
from datetime import timedelta
from pathlib import Path
from typing import Callable
from uuid import UUID

from sqlmodel import select

from cjdb_collectors.domains.provider import ProviderType
from cjdb_collectors.domains.store import (
    AccountStorePayload,
    AccountStoreProviderMixin,
    AwemeStorePayload,
    AwemeStoreProviderMixin,
    SetupResult,
    StoreResult,
    TranscriptionStorePayload,
    TranscriptionStoreProviderMixin,
)
from cjdb_collectors.models import (
    Account,
    Aweme,
    ProjectProvider,
    ProjectProviderSelection,
    Provider,
    ProviderSync,
    SyncObjectType,
    TaskStatus,
    VideoTranscription,
)

from .base import (
    InvalidOperationError,
    NotFoundError,
    SessionFactory,
    apply_changes,
    as_uuid,
    now_utc,
)
from .logger import LoggerService, LogType
from .store_providers import StoreProviderService


_STORE_TYPES_BY_SUBJECT = {
    "aweme": (ProviderType.STORE_AWEME,),
    "account": (ProviderType.STORE_ACCOUNT,),
    "video_transcription": (ProviderType.STORE_VIDEO_TRANSCRIPTION,),
}


class StoreService:
    """Compatibility facade for Provider instances with store capabilities."""

    status_refresh_seconds = 30

    def __init__(
        self,
        session_factory: SessionFactory,
        providers: StoreProviderService,
        runtime_settings=None,
        logger_service: LoggerService | None = None,
    ) -> None:
        self._session = session_factory
        self.providers = providers
        self.runtime_settings = runtime_settings
        self.logger_service = logger_service

    def types(self) -> list[dict]:
        return self.providers.list()

    def _namespaces(self, subject_type: str | None = None) -> set[str]:
        provider_types = (
            [item.value for item in _STORE_TYPES_BY_SUBJECT[subject_type]]
            if subject_type
            else None
        )
        return {str(item["type"]) for item in self.providers.list(provider_types)}

    def list(
        self,
        include_disabled: bool = False,
        subject_type: str | None = None,
        project_id: UUID | str | None = None,
    ) -> list[Provider]:
        namespaces = self._namespaces(subject_type)
        if not namespaces:
            return []
        with self._session() as session:
            statement = select(Provider).where(Provider.namespace.in_(namespaces))
            if not include_disabled:
                statement = statement.where(Provider.status != "disabled")
            if project_id is not None:
                statement = statement.join(
                    ProjectProviderSelection,
                    ProjectProviderSelection.provider_id == Provider.id,
                ).where(
                    ProjectProviderSelection.project_id == as_uuid(project_id),
                    ProjectProviderSelection.provider_type.in_(
                        [
                            item.value
                            for item in _STORE_TYPES_BY_SUBJECT[
                                subject_type or "aweme"
                            ]
                        ]
                    ),
                )
            return list(
                session.exec(statement.distinct().order_by(Provider.name)).all()
            )

    def get(self, provider_id: UUID | str) -> Provider:
        with self._session() as session:
            item = session.get(Provider, as_uuid(provider_id))
            if not item:
                raise NotFoundError("provider not found")
            return item

    def create(
        self,
        type: str,
        *,
        name: str,
        subject_type: str = "aweme",
        setup_values: dict | None = None,
        default: bool = False,
        project_id: UUID | str | None = None,
    ) -> Provider:
        item, _setup_result = self.create_with_setup_result(
            type,
            name=name,
            subject_type=subject_type,
            setup_values=setup_values,
            default=default,
            project_id=project_id,
        )
        return item

    def create_with_setup_result(
        self,
        type: str,
        *,
        name: str,
        subject_type: str = "aweme",
        setup_values: dict | None = None,
        default: bool = False,
        project_id: UUID | str | None = None,
    ) -> tuple[Provider, dict | None]:
        del subject_type, default
        provider_class = self.providers.registry.get(type)
        namespace = provider_class.namespace
        setup_result = None
        with self._session() as session:
            item = Provider(namespace=namespace, name=name.strip() or provider_class.name)
            session.add(item)
            session.flush()
            if project_id is not None:
                session.add(
                    ProjectProvider(
                        project_id=as_uuid(project_id),
                        provider_id=item.id,
                    )
                )
            session.refresh(item)
            provider_id = item.id
        if setup_values:
            setup_result = self.setup(provider_id, setup_values)
            if not setup_result["success"]:
                self._discard_new_provider(provider_id)
                raise InvalidOperationError(
                    setup_result.get("message") or "provider setup failed"
                )
        return self.get(provider_id), setup_result

    add = create

    def update(self, provider_id: UUID | str, **changes) -> Provider:
        with self._session() as session:
            item = session.get(Provider, as_uuid(provider_id))
            if item is None:
                raise NotFoundError("provider not found")
            apply_changes(item, changes, {"name", "status"})
            session.add(item)
            session.flush()
            session.refresh(item)
            return item

    def setup(self, provider_id: UUID | str, values: dict) -> dict:
        selected_id = as_uuid(provider_id)
        item = self.get(selected_id)
        current_payload = dict(item.setup_payload_json or {})
        parser = self.providers.registry.get(item.namespace, current_payload)
        log_path = self._provider_setup_log_path(item)
        self._write_setup_log(log_path, f"开始设置 Store Provider：{item.name or item.namespace}")
        try:
            with self._setup_log_redirect(log_path):
                setup_params = parser.parse_setup_params(values, current=current_payload)
                provider = self.providers.registry.get(
                    item.namespace,
                    current_payload,
                    logger=(
                        self.logger_service.get_logger(LogType.PROVIDER_SETUP, item)
                        if self.logger_service is not None
                        else None
                    ),
                )
                provider.logger.info("开始执行 setup")
                result = provider.setup(setup_params)
        except Exception as exc:
            result = SetupResult(success=False, message=str(exc))
        if not isinstance(result, SetupResult):
            result = SetupResult(
                success=False,
                message="Store Provider setup did not return SetupResult",
            )
        self._write_setup_result_log(log_path, result)
        if result.success:
            self.providers.persist_setup_result(
                selected_id,
                {**dict(result.setup_payload), **setup_params},
                result.message,
                dict(result.details or {}),
            )
            self.refresh_status(selected_id, force=True)
        else:
            self.providers.persist_setup_failure(
                selected_id,
                result.message or "Store Provider setup failed",
            )
        return result.model_dump()

    def _provider_log_path(self, provider: Provider) -> Path | None:
        if self.logger_service is not None:
            return self.logger_service.get_log_path(
                LogType.PROVIDER_RUNTIME,
                provider,
            )
        return None

    def _provider_setup_log_path(self, provider: Provider) -> Path | None:
        if self.logger_service is not None:
            return self.logger_service.get_log_path(LogType.PROVIDER_SETUP, provider)
        return None

    def _write_setup_log(self, log_path: Path | None, message: str) -> None:
        if log_path is None or self.logger_service is None:
            return
        self.logger_service.append_line(log_path, message)

    def _write_setup_result_log(
        self,
        log_path: Path | None,
        result: SetupResult,
    ) -> None:
        status = "成功" if result.success else "失败"
        self._write_setup_log(
            log_path,
            f"设置{status}：{result.message or '无返回消息'}",
        )

    @contextmanager
    def _setup_log_redirect(self, log_path: Path | None):
        if log_path is None or self.logger_service is None:
            yield
            return
        with self.logger_service.open_text_append(log_path) as handle:
            with redirect_stdout(handle), redirect_stderr(handle):
                yield

    def delete(self, provider_id: UUID | str) -> None:
        selected_id = as_uuid(provider_id)
        with self._session() as session:
            usage = session.exec(
                select(ProjectProvider).where(
                    ProjectProvider.provider_id == selected_id
                )
            ).first()
            if usage is not None:
                raise InvalidOperationError(
                    "provider is still used by a project; unbind it first"
                )
            item = session.get(Provider, selected_id)
            if item is None:
                raise NotFoundError("provider not found")
            session.delete(item)

    def _discard_new_provider(self, provider_id: UUID | str) -> None:
        selected_id = as_uuid(provider_id)
        with self._session() as session:
            for relation in session.exec(
                select(ProjectProvider).where(
                    ProjectProvider.provider_id == selected_id
                )
            ).all():
                session.delete(relation)
            item = session.get(Provider, selected_id)
            if item is not None:
                session.delete(item)

    def status(self, provider_id: UUID | str) -> dict:
        return self.refresh_status(provider_id, force=False)

    def refresh_status(self, provider_id: UUID | str, *, force: bool = True) -> dict:
        item = self.get(provider_id)
        if (
            not force
            and item.last_checked_at
            and now_utc() - item.last_checked_at
            < timedelta(seconds=self.status_refresh_seconds)
        ):
            return {
                "provider": item.model_dump(mode="json"),
                "status": item.status,
                "ready": item.status == "ready",
                "message": item.status_message,
                "details": {"cached": True},
                "checked_at": item.last_checked_at.isoformat(),
            }
        return self.providers.status(provider_id)

    validate = refresh_status

    @staticmethod
    def _invoke_store(operation: Callable[[], StoreResult]) -> StoreResult:
        try:
            result = operation()
            if not isinstance(result, StoreResult):
                return StoreResult(
                    success=False,
                    message="store provider did not return StoreResult",
                )
            if result.success:
                json.dumps(dict(result.success_payload))
            return result
        except Exception as exc:
            return StoreResult(success=False, message=str(exc))

    def store_aweme(
        self,
        aweme: Aweme,
        provider_id: UUID | str,
        last_store_result: StoreResult | None = None,
    ) -> StoreResult:
        payload = AwemeStorePayload(
            local_id=str(aweme.id),
            platform=aweme.platform.value,
            platform_aweme_id=aweme.platform_aweme_id,
            aweme_url=aweme.aweme_url,
            source_url=aweme.source_url,
            title=aweme.title,
            description=aweme.description,
            published_at=aweme.published_at,
            metrics={
                "play_count": aweme.play_count,
                "like_count": aweme.like_count,
                "collect_count": aweme.collect_count,
                "share_count": aweme.share_count,
                "comment_count": aweme.comment_count,
            },
            comments=aweme.comments_json,
            video_path=aweme.video_path,
            photo_paths=aweme.photo_paths,
            transcription_text=aweme.transcription_text,
        )
        provider = self.providers.get_provider(provider_id)
        if not isinstance(provider, AwemeStoreProviderMixin):
            return StoreResult(success=False, message="provider does not support awemes")
        return self._invoke_store(lambda: provider.store_aweme(payload, last_store_result))

    def store_account(
        self,
        account: Account,
        provider_id: UUID | str,
        last_store_result: StoreResult | None = None,
    ) -> StoreResult:
        payload = AccountStorePayload(
            local_id=str(account.id),
            platform=account.platform.value,
            platform_account_id=account.platform_account_id,
            profile_url=account.profile_url,
            display_name=account.display_name,
            profile_data={
                "signature": account.signature,
                "location": account.location,
                "ip_location": account.ip_location,
                "gender": account.gender,
                "verified": account.verified,
                "follower_count": account.follower_count,
                "following_count": account.following_count,
                "work_count": account.work_count,
                "like_count": account.like_count,
                "collect_count": account.collect_count,
                "comment_count": account.comment_count,
                "share_count": account.share_count,
                "total_favorited": account.total_favorited,
                **account.extra_data_json,
            },
            avatar_path=account.avatar_path,
        )
        provider = self.providers.get_provider(provider_id)
        if not isinstance(provider, AccountStoreProviderMixin):
            return StoreResult(success=False, message="provider does not support accounts")
        return self._invoke_store(lambda: provider.store_account(payload, last_store_result))

    def store_transcription(
        self,
        transcription: VideoTranscription,
        provider_id: UUID | str,
        last_store_result: StoreResult | None = None,
    ) -> StoreResult:
        payload = TranscriptionStorePayload(
            local_id=str(transcription.id),
            aweme_id=str(transcription.aweme_id) if transcription.aweme_id else None,
            source_url=transcription.source_url,
            video_path=transcription.video_path,
            status=transcription.status.value,
            text=transcription.text,
            normalized_text=transcription.normalized_text,
            text_summary=transcription.text_summary,
            duration_seconds=transcription.duration_seconds,
            segments=transcription.segments_json,
        )
        provider = self.providers.get_provider(provider_id)
        if not isinstance(provider, TranscriptionStoreProviderMixin):
            return StoreResult(
                success=False,
                message="provider does not support transcriptions",
            )
        return self._invoke_store(
            lambda: provider.store_transcription(payload, last_store_result)
        )

    def sync(
        self,
        subject: Aweme | Account | VideoTranscription | UUID | str,
        provider: Provider | UUID | str,
        *,
        subject_type: str | None = None,
    ) -> ProviderSync:
        provider_id = provider.id if isinstance(provider, Provider) else as_uuid(provider)
        if isinstance(subject, Aweme) or subject_type == "aweme":
            subject_id = subject.id if isinstance(subject, Aweme) else as_uuid(subject)
            return self._queue_sync(SyncObjectType.AWEME, subject_id, provider_id)
        if isinstance(subject, Account) or subject_type == "account":
            subject_id = subject.id if isinstance(subject, Account) else as_uuid(subject)
            return self._queue_sync(SyncObjectType.ACCOUNT, subject_id, provider_id)
        if isinstance(subject, VideoTranscription) or subject_type == "video_transcription":
            subject_id = (
                subject.id if isinstance(subject, VideoTranscription) else as_uuid(subject)
            )
            return self._queue_sync(
                SyncObjectType.VIDEO_TRANSCRIPTION,
                subject_id,
                provider_id,
            )
        raise ValueError("subject_type is required for an ID-only sync")

    def project_usage_counts(self, provider_ids: list[UUID | str]) -> dict[str, int]:
        selected_ids = [as_uuid(value) for value in provider_ids]
        counts = {str(value): 0 for value in selected_ids}
        if not selected_ids:
            return counts
        with self._session() as session:
            for relation in session.exec(
                select(ProjectProvider).where(
                    ProjectProvider.provider_id.in_(selected_ids)
                )
            ).all():
                key = str(relation.provider_id)
                counts[key] = counts.get(key, 0) + 1
        return counts

    def default_ids(self, subject_type: str | None = None) -> list[UUID]:
        del subject_type
        return []

    def is_default(self, provider_id: UUID | str) -> bool:
        del provider_id
        return False

    def set_default(self, provider_id: UUID | str, enabled: bool = True) -> dict[str, object]:
        return {"provider_id": str(as_uuid(provider_id)), "selected": enabled}

    def _queue_sync(
        self,
        object_type: SyncObjectType,
        subject_id: UUID,
        provider_id: UUID,
    ):
        with self._session() as session:
            subject_model = (
                Aweme
                if object_type == SyncObjectType.AWEME
                else Account
                if object_type == SyncObjectType.ACCOUNT
                else VideoTranscription
            )
            subject = session.get(subject_model, subject_id)
            if not subject or getattr(subject, "deleted_at", None):
                raise NotFoundError(f"{object_type.value} not found")
            if session.get(Provider, provider_id) is None:
                raise NotFoundError("provider not found")
            relation = session.exec(
                select(ProviderSync).where(
                    ProviderSync.object_type == object_type,
                    ProviderSync.object_id == subject_id,
                    ProviderSync.provider_id == provider_id,
                )
            ).first()
            if relation is None:
                relation = ProviderSync(
                    object_type=object_type,
                    object_id=subject_id,
                    provider_id=provider_id,
                )
            relation.status = TaskStatus.PENDING
            relation.enabled = True
            relation.next_run_at = None
            relation.error_message = None
            relation.run_token = None
            session.add(relation)
            session.flush()
            session.refresh(relation)
            return relation
