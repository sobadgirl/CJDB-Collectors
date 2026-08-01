from __future__ import annotations

from uuid import UUID

from sqlmodel import select

from cjdb_collectors.models import (
    Account,
    AccountDataStorerSync,
    Aweme,
    AwemeDataStorerSync,
    DataStorer,
    DataStorerStatus,
    DefaultDataStorer,
    TaskStatus,
)
from cjdb_collectors.store import (
    AccountStorePayload,
    AwemeStorePayload,
    StoreResult,
    Storer,
)

from .base import (
    InvalidOperationError,
    NotFoundError,
    SessionFactory,
    apply_changes,
    as_uuid,
    now_utc,
)
from .store_providers import StoreProviderService
from .store_relations import ensure_default_store_relations


class StoreService:
    def __init__(
        self,
        session_factory: SessionFactory,
        providers: StoreProviderService,
    ) -> None:
        self._session = session_factory
        self.providers = providers

    def types(self) -> list[dict]:
        return self.providers.list()

    def list(self, include_disabled: bool = False) -> list[DataStorer]:
        with self._session() as session:
            statement = select(DataStorer)
            if not include_disabled:
                statement = statement.where(
                    DataStorer.status != DataStorerStatus.DISABLED
                )
            return list(session.exec(statement.order_by(DataStorer.name)).all())

    def get(self, store_id: UUID | str) -> DataStorer:
        with self._session() as session:
            item = session.get(DataStorer, as_uuid(store_id))
            if not item:
                raise NotFoundError("store not found")
            return item

    def create(
        self,
        type: str,
        *,
        name: str,
        secret_ref: str | None = None,
        connection_config_json: dict | None = None,
        container_config_json: dict | None = None,
        field_mapping_json: dict | None = None,
        attachment_policy_json: dict | None = None,
        conflict_policy: str = "upsert",
        setup_values: dict | None = None,
        default: bool = False,
    ) -> DataStorer:
        self.providers.registry.get(type)
        with self._session() as session:
            item = DataStorer(
                name=name,
                type=type.lower(),
                secret_ref=secret_ref,
                connection_config_json=connection_config_json or {},
                container_config_json=container_config_json or {},
                field_mapping_json=field_mapping_json or {},
                attachment_policy_json=attachment_policy_json or {},
                conflict_policy=conflict_policy,
            )
            session.add(item)
            session.flush()
            session.refresh(item)
            item_id = item.id
        if setup_values:
            self.providers.setup(item_id, setup_values)
        if default:
            self.set_default(item_id, True)
        return self.get(item_id)

    add = create

    def update(self, store_id: UUID | str, **changes) -> DataStorer:
        allowed = {
            "name",
            "secret_ref",
            "connection_config_json",
            "container_config_json",
            "field_mapping_json",
            "attachment_policy_json",
            "conflict_policy",
            "status",
        }
        with self._session() as session:
            item = session.get(DataStorer, as_uuid(store_id))
            if not item:
                raise NotFoundError("store not found")
            if "status" in changes and isinstance(changes["status"], str):
                changes["status"] = DataStorerStatus(changes["status"])
            apply_changes(item, changes, allowed)
            session.add(item)
            if "status" in changes:
                session.flush()
                ensure_default_store_relations(session)
            session.flush()
            session.refresh(item)
            return item

    def delete(self, store_id: UUID | str) -> None:
        with self._session() as session:
            item = session.get(DataStorer, as_uuid(store_id))
            if not item:
                raise NotFoundError("store not found")
            item.status = DataStorerStatus.DISABLED
            default = session.get(DefaultDataStorer, item.id)
            if default:
                session.delete(default)
            session.add(item)
            session.flush()
            ensure_default_store_relations(session)

    def status(self, store_id: UUID | str) -> dict:
        result = self.providers.status(store_id)
        with self._session() as session:
            item = session.get(DataStorer, as_uuid(store_id))
            if not item:
                raise NotFoundError("store not found")
            item.status = (
                DataStorerStatus.ACTIVE
                if result["ready"]
                else DataStorerStatus.NEEDS_ATTENTION
            )
            item.validation_error = result.get("message")
            item.last_validated_at = now_utc()
            session.add(item)
        return result

    validate = status

    def get_storer(self, store_id: UUID | str) -> Storer:
        return self.providers.get_storer(store_id)

    def store_aweme(
        self,
        aweme: Aweme,
        storer: Storer,
        remote_record_id: str | None = None,
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
        return storer.store_aweme(payload, remote_record_id)

    def store_account(
        self,
        account: Account,
        storer: Storer,
        remote_record_id: str | None = None,
    ) -> StoreResult:
        payload = AccountStorePayload(
            local_id=str(account.id),
            platform=account.platform.value,
            platform_account_id=account.platform_account_id,
            profile_url=account.profile_url,
            display_name=account.display_name,
            profile_data=account.profile_data_json,
            avatar_path=account.avatar_path,
        )
        return storer.store_account(payload, remote_record_id)

    def sync(
        self,
        subject: Aweme | Account | UUID | str,
        storer: DataStorer | Storer | UUID | str,
        *,
        subject_type: str | None = None,
    ) -> AwemeDataStorerSync | AccountDataStorerSync:
        store_id = storer.id if isinstance(storer, (DataStorer, Storer)) else as_uuid(storer)
        if isinstance(subject, Aweme) or subject_type == "aweme":
            subject_id = subject.id if isinstance(subject, Aweme) else as_uuid(subject)
            return self._queue_sync(AwemeDataStorerSync, "aweme_id", subject_id, store_id)
        if isinstance(subject, Account) or subject_type == "account":
            subject_id = (
                subject.id if isinstance(subject, Account) else as_uuid(subject)
            )
            return self._queue_sync(
                AccountDataStorerSync,
                "account_id",
                subject_id,
                store_id,
            )
        raise ValueError("subject_type is required for an ID-only sync")

    def set_default(
        self,
        store_id: UUID | str,
        enabled: bool = True,
    ) -> dict[str, object]:
        selected_id = as_uuid(store_id)
        with self._session() as session:
            item = session.get(DataStorer, selected_id)
            if not item:
                raise NotFoundError("store not found")
            if enabled and item.status == DataStorerStatus.DISABLED:
                raise InvalidOperationError(
                    "disabled store cannot be set as default"
                )
            relation = session.get(DefaultDataStorer, selected_id)
            if enabled and relation is None:
                session.add(DefaultDataStorer(data_storer_id=selected_id))
                session.flush()
                ensure_default_store_relations(session)
            elif not enabled and relation is not None:
                session.delete(relation)
                session.flush()
                ensure_default_store_relations(session)
        return {
            "store_id": str(selected_id),
            "default": enabled,
        }

    def default_ids(self) -> list[UUID]:
        with self._session() as session:
            return list(
                session.exec(select(DefaultDataStorer.data_storer_id)).all()
            )

    def is_default(self, store_id: UUID | str) -> bool:
        selected_id = as_uuid(store_id)
        with self._session() as session:
            return session.get(DefaultDataStorer, selected_id) is not None

    def adapter_config(self, item: DataStorer) -> dict:
        return self.providers.get_storer(item.id).config

    def _queue_sync(
        self,
        model,
        subject_field: str,
        subject_id: UUID,
        store_id: UUID,
    ):
        with self._session() as session:
            subject_model = Aweme if subject_field == "aweme_id" else Account
            subject = session.get(subject_model, subject_id)
            if not subject or subject.deleted_at:
                raise NotFoundError(f"{subject_field.removesuffix('_id')} not found")
            store = session.get(DataStorer, store_id)
            if not store:
                raise NotFoundError("store not found")
            relation = session.exec(
                select(model).where(
                    getattr(model, subject_field) == subject_id,
                    model.data_storer_id == store_id,
                )
            ).first()
            if relation is None:
                relation = model(
                    **{
                        subject_field: subject_id,
                        "data_storer_id": store_id,
                    }
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
