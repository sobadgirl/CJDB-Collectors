from __future__ import annotations

from uuid import UUID

from sqlmodel import select

from cjdb_collectors.models import (
    Account,
    Aweme,
    ProviderSync,
    SyncObjectType,
    TaskStatus,
)

from .base import NotFoundError, SessionFactory, as_uuid


class SyncService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session = session_factory

    def list(
        self,
        *,
        aweme_id: UUID | str | None = None,
        account_id: UUID | str | None = None,
        transcription_id: UUID | str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list:
        with self._session() as session:
            statement = select(ProviderSync)
            if aweme_id:
                statement = statement.where(
                    ProviderSync.object_type == SyncObjectType.AWEME,
                    ProviderSync.object_id == as_uuid(aweme_id),
                )
            elif account_id:
                statement = statement.where(
                    ProviderSync.object_type == SyncObjectType.ACCOUNT,
                    ProviderSync.object_id == as_uuid(account_id),
                )
            elif transcription_id:
                statement = statement.where(
                    ProviderSync.object_type == SyncObjectType.VIDEO_TRANSCRIPTION,
                    ProviderSync.object_id == as_uuid(transcription_id),
                )
            if status:
                statement = statement.where(ProviderSync.status == TaskStatus(status))
            statement = statement.offset(offset)
            if limit is not None:
                statement = statement.limit(limit)
            return list(session.exec(statement).all())

    def _list_for_objects(
        self,
        object_type: SyncObjectType,
        object_ids: list[UUID | str],
    ) -> list[ProviderSync]:
        ids = [as_uuid(value) for value in object_ids]
        if not ids:
            return []
        with self._session() as session:
            statement = select(ProviderSync).where(
                ProviderSync.object_type == object_type,
                ProviderSync.object_id.in_(ids),
            )
            return list(session.exec(statement).all())

    def list_for_awemes(self, aweme_ids: list[UUID | str]) -> list[ProviderSync]:
        return self._list_for_objects(SyncObjectType.AWEME, aweme_ids)

    def list_for_transcriptions(
        self, transcription_ids: list[UUID | str]
    ) -> list[ProviderSync]:
        return self._list_for_objects(
            SyncObjectType.VIDEO_TRANSCRIPTION,
            transcription_ids,
        )

    def list_for_accounts(
        self, account_ids: list[UUID | str]
    ) -> list[ProviderSync]:
        return self._list_for_objects(SyncObjectType.ACCOUNT, account_ids)

    def get(self, sync_id: UUID | str):
        sync_uuid = as_uuid(sync_id)
        with self._session() as session:
            item = session.get(ProviderSync, sync_uuid)
            if item:
                return item
            raise NotFoundError("sync relation not found")

    def retry(self, sync_id: UUID | str):
        return self._change(sync_id, status=TaskStatus.PENDING, enabled=True)

    def cancel(self, sync_id: UUID | str):
        return self._change(sync_id, status=TaskStatus.CANCELLED, enabled=False)

    def enable(self, sync_id: UUID | str):
        return self._change(sync_id, enabled=True)

    def disable(self, sync_id: UUID | str):
        return self._change(sync_id, enabled=False)

    def _change(self, sync_id: UUID | str, **changes):
        sync_uuid = as_uuid(sync_id)
        with self._session() as session:
            item = session.get(ProviderSync, sync_uuid)
            if not item:
                raise NotFoundError("sync relation not found")
            for key, value in changes.items():
                setattr(item, key, value)
            if "status" in changes:
                item.next_run_at = None
                item.error_message = None
                item.run_token = None
            session.add(item)
            session.flush()
            session.refresh(item)
            return item

    @staticmethod
    def aweme_ready(aweme: Aweme) -> bool:
        # V1.0 发布隐藏：评论采集不参与发布目标，评论状态不能阻塞作品同步。
        return all(
            status in {TaskStatus.NOT_REQUESTED, TaskStatus.SUCCEEDED}
            for status in (
                aweme.collection_status,
                aweme.media_download_status,
                aweme.video_transcription_status,
            )
        )

    @staticmethod
    def account_ready(account: Account) -> bool:
        return account.collection_status == TaskStatus.SUCCEEDED
