from __future__ import annotations

from uuid import UUID

from sqlmodel import select

from cjdb_collectors.models import (
    Account,
    AccountDataStorerSync,
    Aweme,
    AwemeDataStorerSync,
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
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list:
        with self._session() as session:
            if aweme_id:
                statement = select(AwemeDataStorerSync).where(
                    AwemeDataStorerSync.aweme_id == as_uuid(aweme_id)
                )
                if status:
                    statement = statement.where(
                        AwemeDataStorerSync.status == TaskStatus(status)
                    )
                statement = statement.offset(offset)
                if limit is not None:
                    statement = statement.limit(limit)
                return list(session.exec(statement).all())
            elif account_id:
                statement = select(AccountDataStorerSync).where(
                    AccountDataStorerSync.account_id == as_uuid(account_id)
                )
                if status:
                    statement = statement.where(
                        AccountDataStorerSync.status == TaskStatus(status)
                    )
                statement = statement.offset(offset)
                if limit is not None:
                    statement = statement.limit(limit)
                return list(session.exec(statement).all())
            values = [
                *session.exec(select(AwemeDataStorerSync)).all(),
                *session.exec(select(AccountDataStorerSync)).all(),
            ]
            if status:
                values = [item for item in values if item.status.value == status]
            return values[offset : offset + limit if limit is not None else None]

    def list_for_awemes(self, aweme_ids: list[UUID | str]) -> list[AwemeDataStorerSync]:
        ids = [as_uuid(value) for value in aweme_ids]
        if not ids:
            return []
        with self._session() as session:
            statement = select(AwemeDataStorerSync).where(
                AwemeDataStorerSync.aweme_id.in_(ids)
            )
            return list(session.exec(statement).all())

    def list_for_accounts(
        self, account_ids: list[UUID | str]
    ) -> list[AccountDataStorerSync]:
        ids = [as_uuid(value) for value in account_ids]
        if not ids:
            return []
        with self._session() as session:
            statement = select(AccountDataStorerSync).where(
                AccountDataStorerSync.account_id.in_(ids)
            )
            return list(session.exec(statement).all())

    def get(self, sync_id: UUID | str):
        sync_uuid = as_uuid(sync_id)
        with self._session() as session:
            item = session.get(AwemeDataStorerSync, sync_uuid)
            if item:
                return item
            item = session.get(AccountDataStorerSync, sync_uuid)
            if item:
                return item
            raise NotFoundError("sync relation not found")

    def retry(self, sync_id: UUID | str):
        return self._change(sync_id, status=TaskStatus.PENDING, enabled=True)

    def enable(self, sync_id: UUID | str):
        return self._change(sync_id, enabled=True)

    def disable(self, sync_id: UUID | str):
        return self._change(sync_id, enabled=False)

    def _change(self, sync_id: UUID | str, **changes):
        sync_uuid = as_uuid(sync_id)
        with self._session() as session:
            item = session.get(AwemeDataStorerSync, sync_uuid) or session.get(
                AccountDataStorerSync, sync_uuid
            )
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
        return all(
            status in {TaskStatus.NOT_REQUESTED, TaskStatus.SUCCEEDED}
            for status in (
                aweme.collection_status,
                aweme.media_download_status,
                aweme.comment_collection_status,
                aweme.video_transcription_status,
            )
        )

    @staticmethod
    def account_ready(account: Account) -> bool:
        return account.collection_status == TaskStatus.SUCCEEDED
