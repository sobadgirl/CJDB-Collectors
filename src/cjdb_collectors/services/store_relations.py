from __future__ import annotations

from collections.abc import Iterable
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
    Group,
    GroupAccount,
    GroupAweme,
    GroupDataStorer,
    GroupStatus,
)


def target_store_ids(session, group_ids: Iterable[UUID] = ()) -> set[UUID]:
    targets = set(
        session.exec(
            select(DefaultDataStorer.data_storer_id)
            .join(
                DataStorer,
                DataStorer.id == DefaultDataStorer.data_storer_id,
            )
            .where(DataStorer.status != DataStorerStatus.DISABLED)
        ).all()
    )
    selected_groups = set(group_ids)
    if selected_groups:
        targets.update(
            session.exec(
                select(GroupDataStorer.data_storer_id)
                .join(Group, Group.id == GroupDataStorer.group_id)
                .join(
                    DataStorer,
                    DataStorer.id == GroupDataStorer.data_storer_id,
                )
                .where(
                    GroupDataStorer.group_id.in_(selected_groups),
                    Group.status == GroupStatus.ACTIVE,
                    Group.deleted_at.is_(None),
                    DataStorer.status != DataStorerStatus.DISABLED,
                )
            ).all()
        )
    return targets


def ensure_aweme_store_relations(
    session,
    aweme_id: UUID,
    group_ids: Iterable[UUID] | None = None,
) -> None:
    if group_ids is None:
        group_ids = session.exec(
            select(GroupAweme.group_id).where(GroupAweme.aweme_id == aweme_id)
        ).all()
    targets = target_store_ids(session, group_ids)
    relations = list(
        session.exec(
            select(AwemeDataStorerSync).where(
                AwemeDataStorerSync.aweme_id == aweme_id
            )
        ).all()
    )
    existing = {relation.data_storer_id for relation in relations}
    for relation in relations:
        relation.enabled = relation.data_storer_id in targets
        session.add(relation)
    for store_id in targets - existing:
        session.add(
            AwemeDataStorerSync(
                aweme_id=aweme_id,
                data_storer_id=store_id,
            )
        )


def ensure_account_store_relations(
    session,
    account_id: UUID,
    group_ids: Iterable[UUID] | None = None,
) -> None:
    if group_ids is None:
        group_ids = session.exec(
            select(GroupAccount.group_id).where(
                GroupAccount.account_id == account_id
            )
        ).all()
    targets = target_store_ids(session, group_ids)
    relations = list(
        session.exec(
            select(AccountDataStorerSync).where(
                AccountDataStorerSync.account_id == account_id
            )
        ).all()
    )
    existing = {relation.data_storer_id for relation in relations}
    for relation in relations:
        relation.enabled = relation.data_storer_id in targets
        session.add(relation)
    for store_id in targets - existing:
        session.add(
            AccountDataStorerSync(
                account_id=account_id,
                data_storer_id=store_id,
            )
        )


def ensure_group_store_relations(
    session,
    group_id: UUID,
    *,
    aweme_ids: Iterable[UUID] | None = None,
    account_ids: Iterable[UUID] | None = None,
) -> None:
    if aweme_ids is None:
        aweme_ids = session.exec(
            select(GroupAweme.aweme_id).where(GroupAweme.group_id == group_id)
        ).all()
    for aweme_id in aweme_ids:
        ensure_aweme_store_relations(session, aweme_id)

    if account_ids is None:
        account_ids = session.exec(
            select(GroupAccount.account_id).where(
                GroupAccount.group_id == group_id
            )
        ).all()
    for account_id in account_ids:
        ensure_account_store_relations(session, account_id)


def ensure_default_store_relations(session) -> None:
    aweme_ids = session.exec(
        select(Aweme.id).where(Aweme.deleted_at.is_(None))
    ).all()
    account_ids = session.exec(
        select(Account.id).where(Account.deleted_at.is_(None))
    ).all()
    for aweme_id in aweme_ids:
        ensure_aweme_store_relations(session, aweme_id)
    for account_id in account_ids:
        ensure_account_store_relations(session, account_id)
