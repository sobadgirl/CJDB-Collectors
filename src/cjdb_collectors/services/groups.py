from __future__ import annotations

from uuid import UUID

from sqlmodel import select

from cjdb_collectors.models import (
    Group,
    GroupAccount,
    GroupAweme,
    GroupDataStorer,
    GroupStatus,
)

from .base import NotFoundError, SessionFactory, apply_changes, as_uuid, now_utc
from .store_relations import ensure_group_store_relations


class GroupService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session = session_factory

    def list(self, include_disabled: bool = False) -> list[Group]:
        with self._session() as session:
            statement = select(Group).where(Group.deleted_at.is_(None))
            if not include_disabled:
                statement = statement.where(Group.status == GroupStatus.ACTIVE)
            return list(
                session.exec(statement.order_by(Group.sort_order, Group.name)).all()
            )

    def get(self, group_id: UUID | str) -> Group:
        with self._session() as session:
            group = session.get(Group, as_uuid(group_id))
            if not group or group.deleted_at:
                raise NotFoundError("group not found")
            return group

    def create(
        self,
        name: str,
        *,
        description: str | None = None,
        color: str | None = None,
        sort_order: int = 0,
    ) -> Group:
        with self._session() as session:
            group = Group(
                name=name.strip(),
                description=description,
                color=color,
                sort_order=sort_order,
            )
            session.add(group)
            session.flush()
            session.refresh(group)
            return group

    def update(self, group_id: UUID | str, **changes) -> Group:
        with self._session() as session:
            group = session.get(Group, as_uuid(group_id))
            if not group or group.deleted_at:
                raise NotFoundError("group not found")
            if "status" in changes and isinstance(changes["status"], str):
                changes["status"] = GroupStatus(changes["status"])
            apply_changes(
                group,
                changes,
                {"name", "description", "color", "sort_order", "status"},
            )
            session.add(group)
            if "status" in changes:
                session.flush()
                self._ensure_sync_relations(session, group.id)
            session.flush()
            session.refresh(group)
            return group

    def delete(self, group_id: UUID | str) -> None:
        with self._session() as session:
            group = session.get(Group, as_uuid(group_id))
            if not group or group.deleted_at:
                raise NotFoundError("group not found")
            group.deleted_at = now_utc()
            session.add(group)
            session.flush()
            self._ensure_sync_relations(session, group.id)

    def set_members(
        self,
        group_id: UUID | str,
        *,
        aweme_ids: list[UUID | str] | None = None,
        account_ids: list[UUID | str] | None = None,
    ) -> Group:
        group_uuid = as_uuid(group_id)
        with self._session() as session:
            group = session.get(Group, group_uuid)
            if not group or group.deleted_at:
                raise NotFoundError("group not found")
            affected_awemes = set(
                session.exec(
                    select(GroupAweme.aweme_id).where(
                        GroupAweme.group_id == group_uuid
                    )
                ).all()
            )
            affected_accounts = set(
                session.exec(
                    select(GroupAccount.account_id).where(
                        GroupAccount.group_id == group_uuid
                    )
                ).all()
            )
            if aweme_ids is not None:
                affected_awemes.update(as_uuid(value) for value in aweme_ids)
                self._replace(
                    session,
                    GroupAweme,
                    "aweme_id",
                    group_uuid,
                    {as_uuid(value) for value in aweme_ids},
                )
            if account_ids is not None:
                affected_accounts.update(as_uuid(value) for value in account_ids)
                self._replace(
                    session,
                    GroupAccount,
                    "account_id",
                    group_uuid,
                    {as_uuid(value) for value in account_ids},
                )
            session.flush()
            self._ensure_sync_relations(
                session,
                group_uuid,
                aweme_ids=affected_awemes,
                account_ids=affected_accounts,
            )
            return group

    def set_stores(
        self, group_id: UUID | str, store_ids: list[UUID | str]
    ) -> Group:
        group_uuid = as_uuid(group_id)
        with self._session() as session:
            group = session.get(Group, group_uuid)
            if not group or group.deleted_at:
                raise NotFoundError("group not found")
            self._replace(
                session,
                GroupDataStorer,
                "data_storer_id",
                group_uuid,
                {as_uuid(value) for value in store_ids},
            )
            self._ensure_sync_relations(session, group_uuid)
            return group

    def store_ids(self, group_id: UUID | str) -> list[UUID]:
        group_uuid = as_uuid(group_id)
        with self._session() as session:
            group = session.get(Group, group_uuid)
            if not group or group.deleted_at:
                raise NotFoundError("group not found")
            return list(
                session.exec(
                    select(GroupDataStorer.data_storer_id).where(
                        GroupDataStorer.group_id == group_uuid
                    )
                ).all()
            )

    def bind_store(
        self, group_id: UUID | str, store_id: UUID | str
    ) -> Group:
        values = set(self.store_ids(group_id))
        values.add(as_uuid(store_id))
        return self.set_stores(group_id, list(values))

    def unbind_store(
        self, group_id: UUID | str, store_id: UUID | str
    ) -> Group:
        excluded = as_uuid(store_id)
        values = [
            value for value in self.store_ids(group_id) if value != excluded
        ]
        return self.set_stores(group_id, values)

    @staticmethod
    def _replace(session, model, value_field: str, group_id: UUID, desired: set[UUID]):
        existing = list(
            session.exec(select(model).where(model.group_id == group_id)).all()
        )
        current = {getattr(item, value_field) for item in existing}
        for item in existing:
            if getattr(item, value_field) not in desired:
                session.delete(item)
        for value in desired - current:
            session.add(model(group_id=group_id, **{value_field: value}))

    @staticmethod
    def _ensure_sync_relations(
        session,
        group_id: UUID,
        *,
        aweme_ids=None,
        account_ids=None,
    ) -> None:
        ensure_group_store_relations(
            session,
            group_id,
            aweme_ids=aweme_ids,
            account_ids=account_ids,
        )
