from __future__ import annotations

from uuid import UUID

from sqlmodel import select

from cjdb_collectors.domains.provider import ProviderSelectionMode, ProviderType
from cjdb_collectors.models import (
    Project,
    ProjectAccount,
    ProjectAweme,
    ProjectProvider,
    ProjectProviderSelection,
    ProjectStatus,
    Provider,
)

from .base import NotFoundError, SessionFactory, apply_changes, as_uuid, now_utc
from .store_relations import ensure_project_store_relations


class ProjectService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session = session_factory

    def list(self, include_disabled: bool = False) -> list[Project]:
        with self._session() as session:
            statement = select(Project).where(Project.deleted_at.is_(None))
            if not include_disabled:
                statement = statement.where(Project.status == ProjectStatus.ACTIVE)
            return list(
                session.exec(statement.order_by(Project.sort_order, Project.name)).all()
            )

    def get(self, project_id: UUID | str) -> Project:
        with self._session() as session:
            project = session.get(Project, as_uuid(project_id))
            if not project or project.deleted_at:
                raise NotFoundError("project not found")
            return project

    def create(
        self,
        name: str,
        *,
        description: str | None = None,
        color: str | None = None,
        sort_order: int = 0,
    ) -> Project:
        with self._session() as session:
            project = Project(
                name=name.strip(),
                description=description,
                color=color,
                sort_order=sort_order,
            )
            session.add(project)
            session.flush()
            session.refresh(project)
            return project

    def update(self, project_id: UUID | str, **changes) -> Project:
        with self._session() as session:
            project = session.get(Project, as_uuid(project_id))
            if not project or project.deleted_at:
                raise NotFoundError("project not found")
            if "status" in changes and isinstance(changes["status"], str):
                changes["status"] = ProjectStatus(changes["status"])
            apply_changes(
                project,
                changes,
                {"name", "description", "color", "sort_order", "status"},
            )
            session.add(project)
            if "status" in changes:
                session.flush()
                self._ensure_sync_relations(session, project.id)
            session.flush()
            session.refresh(project)
            return project

    def delete(self, project_id: UUID | str) -> None:
        with self._session() as session:
            project = session.get(Project, as_uuid(project_id))
            if not project or project.deleted_at:
                raise NotFoundError("project not found")
            project.deleted_at = now_utc()
            session.add(project)
            session.flush()
            self._ensure_sync_relations(session, project.id)

    def set_members(
        self,
        project_id: UUID | str,
        *,
        aweme_ids: list[UUID | str] | None = None,
        account_ids: list[UUID | str] | None = None,
    ) -> Project:
        project_uuid = as_uuid(project_id)
        with self._session() as session:
            project = session.get(Project, project_uuid)
            if not project or project.deleted_at:
                raise NotFoundError("project not found")
            affected_awemes = set(
                session.exec(
                    select(ProjectAweme.aweme_id).where(
                        ProjectAweme.project_id == project_uuid
                    )
                ).all()
            )
            affected_accounts = set(
                session.exec(
                    select(ProjectAccount.account_id).where(
                        ProjectAccount.project_id == project_uuid
                    )
                ).all()
            )
            if aweme_ids is not None:
                affected_awemes.update(as_uuid(value) for value in aweme_ids)
                self._replace(
                    session,
                    ProjectAweme,
                    "aweme_id",
                    project_uuid,
                    {as_uuid(value) for value in aweme_ids},
                )
            if account_ids is not None:
                affected_accounts.update(as_uuid(value) for value in account_ids)
                self._replace(
                    session,
                    ProjectAccount,
                    "account_id",
                    project_uuid,
                    {as_uuid(value) for value in account_ids},
                )
            session.flush()
            self._ensure_sync_relations(
                session,
                project_uuid,
                aweme_ids=affected_awemes,
                account_ids=affected_accounts,
            )
            return project

    def set_providers(
        self, project_id: UUID | str, provider_ids: list[UUID | str]
    ) -> Project:
        project_uuid = as_uuid(project_id)
        with self._session() as session:
            project = session.get(Project, project_uuid)
            if not project or project.deleted_at:
                raise NotFoundError("project not found")
            self._replace(
                session,
                ProjectProvider,
                "provider_id",
                project_uuid,
                {as_uuid(value) for value in provider_ids},
            )
            session.flush()
            bound_ids = {as_uuid(value) for value in provider_ids}
            for selection in session.exec(
                select(ProjectProviderSelection).where(
                    ProjectProviderSelection.project_id == project_uuid
                )
            ).all():
                if selection.provider_id not in bound_ids:
                    session.delete(selection)
            self._ensure_sync_relations(session, project_uuid)
            return project

    def provider_ids(self, project_id: UUID | str) -> list[UUID]:
        project_uuid = as_uuid(project_id)
        with self._session() as session:
            project = session.get(Project, project_uuid)
            if not project or project.deleted_at:
                raise NotFoundError("project not found")
            return list(
                session.exec(
                    select(ProjectProvider.provider_id).where(
                        ProjectProvider.project_id == project_uuid,
                    )
                ).all()
            )

    def bind_provider(
        self, project_id: UUID | str, provider_id: UUID | str
    ) -> Project:
        provider_uuid = as_uuid(provider_id)
        with self._session() as session:
            project = session.get(Project, as_uuid(project_id))
            if not project or project.deleted_at:
                raise NotFoundError("project not found")
            if session.get(Provider, provider_uuid) is None:
                raise NotFoundError("provider not found")
            relation = session.get(
                ProjectProvider,
                (project.id, provider_uuid),
            )
            if relation is None:
                relation = ProjectProvider(
                    project_id=project.id,
                    provider_id=provider_uuid,
                )
            session.add(relation)
            session.flush()
            self._ensure_sync_relations(session, project.id)
            return project

    def unbind_provider(
        self, project_id: UUID | str, provider_id: UUID | str
    ) -> Project:
        project_uuid = as_uuid(project_id)
        with self._session() as session:
            project = session.get(Project, project_uuid)
            if not project or project.deleted_at:
                raise NotFoundError("project not found")
            relation = session.get(
                ProjectProvider,
                (project_uuid, as_uuid(provider_id)),
            )
            if relation is not None:
                for selection in session.exec(
                    select(ProjectProviderSelection).where(
                        ProjectProviderSelection.project_id == project_uuid,
                        ProjectProviderSelection.provider_id == as_uuid(provider_id),
                    )
                ).all():
                    session.delete(selection)
                session.delete(relation)
                session.flush()
                self._ensure_sync_relations(session, project_uuid)
            return project

    def provider_project_ids(self, provider_id: UUID | str) -> list[UUID]:
        provider_uuid = as_uuid(provider_id)
        with self._session() as session:
            if session.get(Provider, provider_uuid) is None:
                raise NotFoundError("provider not found")
            return list(
                session.exec(
                    select(ProjectProvider.project_id).where(
                        ProjectProvider.provider_id == provider_uuid,
                    )
                ).all()
            )

    def providers(
        self,
        *,
        project_id: UUID | str | None = None,
        exclude_project_id: UUID | str | None = None,
        namespaces: set[str] | None = None,
    ) -> list[Provider]:
        with self._session() as session:
            statement = select(Provider)
            if namespaces is not None:
                if not namespaces:
                    return []
                statement = statement.where(Provider.namespace.in_(namespaces))
            if project_id is not None:
                statement = statement.join(
                    ProjectProvider,
                    ProjectProvider.provider_id == Provider.id,
                ).where(
                    ProjectProvider.project_id == as_uuid(project_id),
                )
            if exclude_project_id is not None:
                excluded = select(ProjectProvider.provider_id).where(
                    ProjectProvider.project_id == as_uuid(exclude_project_id)
                )
                statement = statement.where(Provider.id.not_in(excluded))
            return list(session.exec(statement.order_by(Provider.name)).all())

    def selected_provider_ids(
        self,
        project_id: UUID | str,
        provider_type: ProviderType | str,
    ) -> list[UUID]:
        project_uuid = as_uuid(project_id)
        selected_type = ProviderType(provider_type)
        with self._session() as session:
            return list(
                session.exec(
                    select(ProjectProviderSelection.provider_id)
                    .where(
                        ProjectProviderSelection.project_id == project_uuid,
                        ProjectProviderSelection.provider_type
                        == selected_type.value,
                    )
                    .order_by(ProjectProviderSelection.created_at)
                ).all()
            )

    def select_provider(
        self,
        project_id: UUID | str,
        provider_type: ProviderType | str,
        provider_id: UUID | str,
    ) -> list[UUID]:
        project_uuid = as_uuid(project_id)
        provider_uuid = as_uuid(provider_id)
        selected_type = ProviderType(provider_type)
        with self._session() as session:
            if session.get(Project, project_uuid) is None:
                raise NotFoundError("project not found")
            if session.get(Provider, provider_uuid) is None:
                raise NotFoundError("provider not found")
            if session.get(ProjectProvider, (project_uuid, provider_uuid)) is None:
                raise NotFoundError("provider is not bound to project")
            existing = list(
                session.exec(
                    select(ProjectProviderSelection).where(
                        ProjectProviderSelection.project_id == project_uuid,
                        ProjectProviderSelection.provider_type
                        == selected_type.value,
                    )
                ).all()
            )
            if selected_type.selection_mode == ProviderSelectionMode.SINGLE:
                for selection in existing:
                    if selection.provider_id != provider_uuid:
                        session.delete(selection)
            if not any(item.provider_id == provider_uuid for item in existing):
                session.add(
                    ProjectProviderSelection(
                        project_id=project_uuid,
                        provider_type=selected_type.value,
                        provider_id=provider_uuid,
                    )
                )
            session.flush()
            self._ensure_sync_relations(session, project_uuid)
        return self.selected_provider_ids(project_uuid, selected_type)

    def unselect_provider(
        self,
        project_id: UUID | str,
        provider_type: ProviderType | str,
        provider_id: UUID | str,
    ) -> list[UUID]:
        project_uuid = as_uuid(project_id)
        provider_uuid = as_uuid(provider_id)
        selected_type = ProviderType(provider_type)
        with self._session() as session:
            selection = session.get(
                ProjectProviderSelection,
                (project_uuid, selected_type.value, provider_uuid),
            )
            if selection is not None:
                session.delete(selection)
                session.flush()
                self._ensure_sync_relations(session, project_uuid)
        return self.selected_provider_ids(project_uuid, selected_type)

    def unselect_provider_type(
        self,
        project_id: UUID | str,
        provider_type: ProviderType | str,
    ) -> list[UUID]:
        project_uuid = as_uuid(project_id)
        selected_type = ProviderType(provider_type)
        with self._session() as session:
            selections = list(
                session.exec(
                    select(ProjectProviderSelection).where(
                        ProjectProviderSelection.project_id == project_uuid,
                        ProjectProviderSelection.provider_type == selected_type.value,
                    )
                ).all()
            )
            for selection in selections:
                session.delete(selection)
            if selections:
                session.flush()
                self._ensure_sync_relations(session, project_uuid)
        return self.selected_provider_ids(project_uuid, selected_type)

    # Compatibility aliases for callers being migrated from DataStorer.
    set_stores = set_providers
    store_ids = provider_ids
    bind_store = bind_provider
    unbind_store = unbind_provider

    @staticmethod
    def _replace(session, model, value_field: str, project_id: UUID, desired: set[UUID]):
        existing = list(
            session.exec(select(model).where(model.project_id == project_id)).all()
        )
        current = {getattr(item, value_field) for item in existing}
        for item in existing:
            if getattr(item, value_field) not in desired:
                session.delete(item)
        for value in desired - current:
            session.add(model(project_id=project_id, **{value_field: value}))

    @staticmethod
    def _ensure_sync_relations(
        session,
        project_id: UUID,
        *,
        aweme_ids=None,
        account_ids=None,
    ) -> None:
        ensure_project_store_relations(
            session,
            project_id,
            aweme_ids=aweme_ids,
            account_ids=account_ids,
        )
