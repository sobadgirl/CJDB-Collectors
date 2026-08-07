from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlmodel import select

from cjdb_collectors.models import (
    Account,
    Aweme,
    Project,
    ProjectAccount,
    ProjectAweme,
    ProjectProviderSelection,
    ProjectStatus,
    ProjectVideoTranscription,
    Provider,
    ProviderSync,
    SyncObjectType,
    VideoTranscription,
)
from cjdb_collectors.domains.provider import ProviderType


def target_provider_ids(
    session,
    project_ids: Iterable[UUID] = (),
    provider_types: Iterable[ProviderType | str] = (),
) -> set[UUID]:
    selected_projects = set(project_ids)
    selected_types = {ProviderType(value).value for value in provider_types}
    if not selected_projects or not selected_types:
        return set()
    return set(
        session.exec(
            select(ProjectProviderSelection.provider_id)
            .join(Project, Project.id == ProjectProviderSelection.project_id)
            .join(Provider, Provider.id == ProjectProviderSelection.provider_id)
            .where(
                ProjectProviderSelection.project_id.in_(selected_projects),
                ProjectProviderSelection.provider_type.in_(selected_types),
                Project.status == ProjectStatus.ACTIVE,
                Project.deleted_at.is_(None),
                Provider.status != "disabled",
            )
        ).all()
    )


def _ensure_relations(
    session,
    object_type: SyncObjectType,
    subject_id: UUID,
    targets: set[UUID],
) -> None:
    relations = list(
        session.exec(
            select(ProviderSync).where(
                ProviderSync.object_type == object_type,
                ProviderSync.object_id == subject_id,
            )
        ).all()
    )
    existing = {relation.provider_id for relation in relations}
    for relation in relations:
        relation.enabled = relation.provider_id in targets
        session.add(relation)
    for provider_id in targets - existing:
        session.add(
            ProviderSync(
                object_type=object_type,
                object_id=subject_id,
                provider_id=provider_id,
            )
        )


def ensure_aweme_store_relations(
    session,
    aweme_id: UUID,
    project_ids: Iterable[UUID] | None = None,
) -> None:
    if project_ids is None:
        project_ids = session.exec(
            select(ProjectAweme.project_id).where(ProjectAweme.aweme_id == aweme_id)
        ).all()
    _ensure_relations(
        session,
        SyncObjectType.AWEME,
        aweme_id,
        target_provider_ids(session, project_ids, [ProviderType.STORE_AWEME]),
    )


def ensure_account_store_relations(
    session,
    account_id: UUID,
    project_ids: Iterable[UUID] | None = None,
) -> None:
    if project_ids is None:
        project_ids = session.exec(
            select(ProjectAccount.project_id).where(
                ProjectAccount.account_id == account_id
            )
        ).all()
    _ensure_relations(
        session,
        SyncObjectType.ACCOUNT,
        account_id,
        target_provider_ids(session, project_ids, [ProviderType.STORE_ACCOUNT]),
    )


def ensure_transcription_store_relations(
    session,
    transcription_id: UUID,
    project_ids: Iterable[UUID] | None = None,
) -> None:
    if project_ids is None:
        project_ids = session.exec(
            select(ProjectVideoTranscription.project_id).where(
                ProjectVideoTranscription.video_transcription_id == transcription_id
            )
        ).all()
    _ensure_relations(
        session,
        SyncObjectType.VIDEO_TRANSCRIPTION,
        transcription_id,
        target_provider_ids(
            session,
            project_ids,
            [ProviderType.STORE_VIDEO_TRANSCRIPTION],
        ),
    )


def ensure_project_store_relations(
    session,
    project_id: UUID,
    *,
    aweme_ids: Iterable[UUID] | None = None,
    account_ids: Iterable[UUID] | None = None,
    transcription_ids: Iterable[UUID] | None = None,
) -> None:
    if aweme_ids is None:
        aweme_ids = session.exec(
            select(ProjectAweme.aweme_id).where(ProjectAweme.project_id == project_id)
        ).all()
    for aweme_id in aweme_ids:
        ensure_aweme_store_relations(session, aweme_id)

    if account_ids is None:
        account_ids = session.exec(
            select(ProjectAccount.account_id).where(
                ProjectAccount.project_id == project_id
            )
        ).all()
    for account_id in account_ids:
        ensure_account_store_relations(session, account_id)

    if transcription_ids is None:
        transcription_ids = session.exec(
            select(ProjectVideoTranscription.video_transcription_id).where(
                ProjectVideoTranscription.project_id == project_id
            )
        ).all()
    for transcription_id in transcription_ids:
        ensure_transcription_store_relations(session, transcription_id)


def ensure_default_store_relations(session) -> None:
    for aweme_id in session.exec(
        select(Aweme.id).where(Aweme.deleted_at.is_(None))
    ).all():
        ensure_aweme_store_relations(session, aweme_id)
    for account_id in session.exec(
        select(Account.id).where(Account.deleted_at.is_(None))
    ).all():
        ensure_account_store_relations(session, account_id)
    for transcription_id in session.exec(select(VideoTranscription.id)).all():
        ensure_transcription_store_relations(session, transcription_id)
