"""Versioned HTTP routes.

Every route delegates business decisions to the service container.  This file
only performs HTTP parsing and response/status-code selection.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Response, status

from cjdb_collectors.domains.data_provider import ProviderStatus
from cjdb_collectors.domains.provider import ProviderType
from cjdb_collectors.exceptions import InvalidOperationError
from cjdb_collectors.services.logger import LogType

from .dependencies import get_services
from .schemas import (
    AccountCreate,
    AccountUpdate,
    AwemeCreate,
    AwemeUpdate,
    SettingsGetMany,
    SettingsPatch,
    SettingsSet,
    IdList,
    ProjectCreate,
    ProjectMembersUpdate,
    ProjectUpdate,
    ProviderSelection,
    ProviderCreate,
    ProviderSetup,
    ProviderUpdate,
    TranscriptionCreate,
)

Services = Annotated[Any, Depends(get_services)]
api_router = APIRouter(prefix="/api/v1")


def _cjdb_command(*arguments: str) -> list[str]:
    return [sys.executable, "-m", "cjdb_collectors.cli", *arguments]


def _cjdb_environment(services: Any) -> dict[str, str]:
    return {
        **os.environ,
        "CJDB_CONFIG": str(services.runtime_settings.config_path),
        "CJDB_PROVIDER_SETUP_OUTPUT_REDIRECTED": "1",
        "PYTHONUNBUFFERED": "1",
    }


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _filtered_page(
    values: list[Any],
    *,
    status_value: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Any]:
    if status_value:
        values = [
            item
            for item in values
            if _value(getattr(item, "status", getattr(item, "collection_status", None)))
            == status_value
        ]
    return values[offset : offset + limit if limit is not None else None]


def _assert_provider_supports_type(
    services: Any,
    provider_id: str,
    provider_type: ProviderType,
) -> None:
    record = services.stores.get(provider_id)
    if provider_type.value.startswith("store_"):
        provider_class = services.store_providers.registry._registry.get(
            record.namespace
        )
    else:
        provider_class = services.providers.registry.get(record.namespace)
    supported = {ProviderType(value) for value in provider_class.supported_types}
    if provider_type not in supported:
        raise InvalidOperationError(
            f"provider {record.namespace} does not support {provider_type.value}"
        )


@api_router.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}


@api_router.get("/health/ready", tags=["health"])
def ready(services: Services) -> Any:
    return services.health.ready()


@api_router.get("/services/health", tags=["health"])
def services_health(services: Services) -> Any:
    return services.health.services()


@api_router.get("/providers", tags=["providers"])
def providers(
    services: Services,
    type: str | None = Query(default=None),  # noqa: A002
    project_id: str | None = Query(default=None),
    importable: bool = Query(default=False),
) -> Any:
    if project_id is not None:
        if not type:
            raise InvalidOperationError("type is required with project_id")
        if type.startswith("store_"):
            class_items = services.store_providers.list([type])
            namespaces = {str(item["type"]) for item in class_items}
        else:
            class_items = services.providers.providers(type, include_status=False)
            namespaces = {str(item["namespace"]) for item in class_items}
        metadata = {
            str(item.get("namespace", item.get("type"))): item
            for item in class_items
        }
        records = services.projects.providers(
            project_id=None if importable else project_id,
            exclude_project_id=project_id if importable else None,
            namespaces=namespaces,
        )
        project_names = {
            str(project.id): project.name for project in services.projects.list()
        }
        selected_type = ProviderType(type)
        selected_ids = (
            []
            if importable
            else [
                str(value)
                for value in services.projects.selected_provider_ids(
                    project_id,
                    selected_type,
                )
            ]
        )
        def setup_payload_for(record: Any) -> dict[str, Any]:
            raw_payload = dict(record.setup_payload_json or {})
            if type.startswith("store_"):
                provider = services.store_providers.registry.get(
                    record.namespace,
                    raw_payload,
                )
                return provider.clean_params_value(
                    provider.parameters,
                    raw_payload,
                    current=raw_payload,
                )
            provider_class = services.providers.registry.get(record.namespace)
            return provider_class.clean_params_value(
                provider_class.parameters,
                raw_payload,
                current=raw_payload,
            )

        return {
            "type": type,
            "project_id": project_id,
            "importable": importable,
            "selection_mode": selected_type.selection_mode.value,
            "selected": (
                selected_ids
                if selected_type.selection_mode.value == "multiple"
                else (selected_ids[0] if selected_ids else None)
            ),
            "provider_classes": list(metadata.values()),
            "providers": [
                {
                    **metadata[record.namespace],
                    **record.model_dump(mode="json"),
                    "provider_id": str(record.id),
                    "setup_payload": setup_payload_for(record),
                    "projects": [
                        {
                            "id": str(value),
                            "name": project_names.get(str(value), str(value)),
                        }
                        for value in services.projects.provider_project_ids(record.id)
                    ],
                }
                for record in records
            ],
        }
    return services.providers.catalog(
        type,
        include_setup_payload=True,
    )


@api_router.post(
    "/providers",
    status_code=status.HTTP_201_CREATED,
    tags=["providers"],
)
def create_provider(payload: ProviderCreate, services: Services) -> Any:
    setup_result = None
    if payload.namespace in services.store_providers.registry._provider_classes:
        item, setup_result = services.stores.create_with_setup_result(
            payload.namespace,
            name=payload.name,
            setup_values=payload.values,
            project_id=payload.project_id,
        )
    else:
        item, setup_result = services.providers.create_instance_with_setup_result(
            payload.namespace,
            name=payload.name,
            project_id=payload.project_id,
            values=payload.values,
        )
    if payload.provider_type:
        provider_type = ProviderType(payload.provider_type)
        supported = {
            str(value)
            for value in (
                services.store_providers.registry.get(
                    item.namespace,
                    item.setup_payload_json,
                ).supported_types
                if provider_type.value.startswith("store_")
                else services.providers.registry.get(item.namespace).supported_types
            )
        }
        if provider_type.value not in supported:
            raise InvalidOperationError(
                f"provider {item.namespace} does not support {provider_type.value}"
            )
        services.projects.select_provider(
            payload.project_id,
            provider_type,
            item.id,
        )
    return {
        **item.model_dump(mode="json"),
        "provider_id": str(item.id),
        "projects": [payload.project_id],
        "setup_result": setup_result,
    }


@api_router.patch("/providers/instances/{provider_id}", tags=["providers"])
def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    services: Services,
) -> Any:
    return services.stores.update(
        provider_id,
        **payload.model_dump(exclude_unset=True),
    )


@api_router.post(
    "/providers/instances/{provider_id}/setup",
    tags=["providers"],
)
def setup_provider_instance(
    provider_id: str,
    payload: ProviderSetup,
    services: Services,
) -> Any:
    item = services.stores.get(provider_id)
    if item.namespace in services.store_providers.registry._provider_classes:
        return services.stores.setup(provider_id, payload.values)
    return services.providers.setup_instance(provider_id, payload.values)


@api_router.get(
    "/providers/instances/{provider_id}/status",
    tags=["providers"],
)
def provider_instance_status(provider_id: str, services: Services) -> Any:
    item = services.stores.get(provider_id)
    if item.namespace in services.store_providers.registry._provider_classes:
        return services.stores.status(provider_id)
    return services.providers.status_instance(provider_id)


@api_router.post(
    "/providers/instances/{provider_id}/status/refresh",
    tags=["providers"],
)
def refresh_provider_instance_status(provider_id: str, services: Services) -> Any:
    item = services.stores.get(provider_id)
    if item.namespace in services.store_providers.registry._provider_classes:
        return services.stores.refresh_status(provider_id)
    return services.providers.status_instance(provider_id, refresh=True)


@api_router.delete(
    "/providers/instances/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["providers"],
)
def delete_provider_instance(provider_id: str, services: Services) -> Response:
    services.stores.delete(provider_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.post(
    "/projects/{project_id}/providers/{provider_id}",
    tags=["projects", "providers"],
)
def bind_project_provider(
    project_id: str,
    provider_id: str,
    services: Services,
    type: str | None = Query(default=None),  # noqa: A002
) -> Any:
    services.projects.bind_provider(project_id, provider_id)
    selected_ids: list[str] = []
    if type:
        selected_type = ProviderType(type)
        _assert_provider_supports_type(services, provider_id, selected_type)
        selected_ids = [
            str(value)
            for value in services.projects.select_provider(
                project_id,
                selected_type,
                provider_id,
            )
        ]
    return {
        "project_id": project_id,
        "provider_id": provider_id,
        "provider_type": type,
        "selected": selected_ids,
    }


@api_router.get(
    "/projects/{project_id}/providers",
    tags=["projects", "providers"],
)
def list_project_providers(
    project_id: str,
    services: Services,
    type: str | None = Query(default=None),  # noqa: A002
) -> Any:
    namespaces: set[str] | None = None
    if type:
        if type.startswith("store_"):
            namespaces = {
                str(item["type"])
                for item in services.store_providers.list([type])
            }
        else:
            namespaces = {
                str(item["namespace"])
                for item in services.providers.providers(
                    type,
                    include_status=False,
                )
            }
    records = services.projects.providers(
        project_id=project_id,
        namespaces=namespaces,
    )
    return [
        {
            **record.model_dump(mode="json"),
            "provider_id": str(record.id),
        }
        for record in records
    ]


@api_router.get(
    "/projects/{project_id}/providers/importable",
    tags=["projects", "providers"],
)
def importable_project_providers(
    project_id: str,
    services: Services,
    subject_type: str = Query(pattern="^(aweme|account|video_transcription)$"),
) -> Any:
    namespaces = services.stores._namespaces(subject_type)
    records = services.projects.providers(
        exclude_project_id=project_id,
        namespaces=namespaces,
    )
    projects = {str(item.id): item.name for item in services.projects.list()}
    return {
        "providers": [
            {
                **record.model_dump(mode="json"),
                "provider_id": str(record.id),
                "projects": [
                    {
                        "id": str(value),
                        "name": projects.get(str(value), str(value)),
                    }
                    for value in services.projects.provider_project_ids(record.id)
                ],
            }
            for record in records
        ]
    }


@api_router.delete(
    "/projects/{project_id}/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["projects", "providers"],
)
def unbind_project_provider(
    project_id: str,
    provider_id: str,
    services: Services,
) -> Response:
    services.projects.unbind_provider(project_id, provider_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.get("/providers/services", tags=["providers"])
def provider_services(services: Services) -> Any:
    return services.providers.services()


@api_router.get("/providers/status", tags=["providers"])
def provider_service_status(services: Services) -> Any:
    return services.providers.service_status()


@api_router.post("/providers/status/refresh", tags=["providers"])
def refresh_provider_service_status(services: Services) -> Any:
    result = subprocess.run(
        _cjdb_command("provider", "status", "--refresh", "--format", "json"),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=str(services.runtime_settings.config_path.parent),
        env=_cjdb_environment(services),
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise InvalidOperationError(message or "Provider 状态刷新失败")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InvalidOperationError("Provider 状态刷新命令未返回有效结果") from exc


@api_router.get("/providers/{provider_type}/status", tags=["providers"])
def provider_status(provider_type: str, services: Services) -> Any:
    return services.providers.status(provider_type)


@api_router.post("/providers/{provider_type}/status/refresh", tags=["providers"])
def refresh_provider_status(provider_type: str, services: Services) -> Any:
    result = subprocess.run(
        _cjdb_command(
            "provider",
            "status",
            provider_type,
            "--refresh",
            "--format",
            "json",
        ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=str(services.runtime_settings.config_path.parent),
        env=_cjdb_environment(services),
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise InvalidOperationError(message or "Provider 状态刷新失败")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InvalidOperationError("Provider 状态刷新命令未返回有效结果") from exc


@api_router.patch("/providers/selection", tags=["providers"])
def select_provider(payload: ProviderSelection, services: Services) -> Any:
    if payload.project_id and not payload.provider_id and not payload.selected:
        selected_type = ProviderType(payload.type)
        selected_ids = services.projects.unselect_provider_type(
            payload.project_id,
            selected_type,
        )
        return {
            "type": payload.type,
            "project_id": payload.project_id,
            "provider_id": None,
            "selected": [str(value) for value in selected_ids],
        }
    if payload.project_id and payload.provider_id:
        selected_type = ProviderType(payload.type)
        _assert_provider_supports_type(
            services,
            payload.provider_id,
            selected_type,
        )
        services.projects.bind_provider(payload.project_id, payload.provider_id)
        selected_ids = (
            services.projects.select_provider(
                payload.project_id,
                selected_type,
                payload.provider_id,
            )
            if payload.selected
            else services.projects.unselect_provider(
                payload.project_id,
                selected_type,
                payload.provider_id,
            )
        )
        return {
            "type": payload.type,
            "project_id": payload.project_id,
            "provider_id": payload.provider_id,
            "selected": [str(value) for value in selected_ids],
        }
    if not payload.namespace:
        raise InvalidOperationError("namespace or provider_id is required")
    return services.providers.select(payload.type, payload.namespace)


@api_router.post("/providers/{provider_type}/setup", tags=["providers"])
def provider_setup(
    provider_type: str,
    payload: ProviderSetup,
    services: Services,
) -> Any:
    namespace = services.providers.selected_namespace(provider_type)
    services.providers.assert_provider_config_mutable(namespace)
    setup_dir = Path(services.runtime_settings.app.data_dir) / "provider-setup"
    setup_dir.mkdir(parents=True, exist_ok=True)
    values_path = setup_dir / f"{namespace}-{uuid4().hex}.json"
    values_path.write_text(
        json.dumps(payload.values, ensure_ascii=False),
        encoding="utf-8",
    )
    values_path.chmod(0o600)

    log_path = services.logger.get_log_path(LogType.PROVIDER_SETUP, namespace)
    try:
        with services.logger.open_binary_append(log_path) as log:
            process = subprocess.Popen(
                _cjdb_command(
                    "provider",
                    "setup",
                    provider_type,
                    "--values-file",
                    str(values_path),
                    "--unlink-values-file",
                    "--format",
                    "json",
                ),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(services.runtime_settings.config_path.parent),
                env=_cjdb_environment(services),
                start_new_session=True,
                close_fds=True,
            )
    except Exception:
        values_path.unlink(missing_ok=True)
        raise
    services.providers.set_provider_status(
        namespace,
        ProviderStatus(
            status="setting_up",
            message="Provider setup 正在运行",
            setup_pid=process.pid,
        ),
    )

    return {
        "status": "starting",
        "setup_pid": process.pid,
        "type": provider_type,
    }


@api_router.delete("/providers/{provider_type}/setup", tags=["providers"])
def stop_provider_setup(provider_type: str, services: Services) -> Any:
    result = subprocess.run(
        _cjdb_command(
            "provider",
            "setup",
            provider_type,
            "--stop",
            "--format",
            "json",
        ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        cwd=str(services.runtime_settings.config_path.parent),
        env=_cjdb_environment(services),
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise InvalidOperationError(message or "停止 Provider setup 失败")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InvalidOperationError("Provider setup 停止命令未返回有效结果") from exc


@api_router.get("/providers/{provider_type}/logs", tags=["providers"])
def provider_logs(
    provider_type: str,
    services: Services,
    scope: str = Query(default="runtime", pattern="^(runtime|setup)$"),
    before: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
) -> Any:
    catalog = services.providers.catalog(
        provider_type,
        include_status=False,
    )
    selected_namespace = catalog.get("selected")
    provider = next(
        (
            item
            for item in catalog.get("providers", [])
            if item.get("namespace") == selected_namespace
        ),
        None,
    )
    if provider is None:
        return {
            "type": provider_type,
            "provider": None,
            "lines": [],
            "start": 0,
            "end": 0,
            "total": 0,
            "has_more": False,
        }

    namespace = str(provider["namespace"])
    log_type = (
        LogType.PROVIDER_SETUP
        if scope == "setup"
        else LogType.PROVIDER_RUNTIME
    )
    return {
        "type": provider_type,
        "scope": scope,
        "provider": {
            "name": provider["name"],
            "namespace": namespace,
        },
        **services.logger.read_page(
            log_type,
            namespace,
            before=before,
            limit=limit,
        ),
    }


@api_router.get("/providers/instances/{provider_id}/logs", tags=["providers"])
def provider_instance_logs(
    provider_id: str,
    services: Services,
    scope: str = Query(default="runtime", pattern="^(runtime|setup)$"),
    before: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
) -> Any:
    provider = services.stores.get(provider_id)
    base = {
        "scope": scope,
        "provider": {
            "id": str(provider.id),
            "name": provider.name,
            "namespace": provider.namespace,
        },
    }
    log_type = (
        LogType.PROVIDER_SETUP
        if scope == "setup"
        else LogType.PROVIDER_RUNTIME
    )
    return {
        **base,
        **services.logger.read_page(
            log_type,
            provider,
            before=before,
            limit=limit,
        ),
    }


@api_router.get("/worker-tasks/health", tags=["worker-tasks"])
def worker_health(services: Services) -> Any:
    return services.worker_tasks.health()


@api_router.get("/worker-tasks/logs", tags=["worker-tasks"])
def worker_logs(
    services: Services,
    scope: str = Query(default="worker", pattern="^(worker|tasks)$"),
    task_type: str | None = Query(default=None),
    before: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
) -> Any:
    log_type = LogType.WORKER_TASKS if scope == "tasks" else LogType.WORKER
    return {
        "scope": scope,
        "task_type": task_type,
        **services.logger.read_page(
            log_type,
            task_type if log_type == LogType.WORKER_TASKS else None,
            before=before,
            limit=limit,
        ),
    }


@api_router.post("/worker-tasks/start", tags=["worker-tasks"])
def start_worker(services: Services) -> Any:
    return services.worker_tasks.start_worker()


@api_router.post("/worker-tasks/stop", tags=["worker-tasks"])
def stop_worker(services: Services) -> Any:
    return services.worker_tasks.stop_worker()


@api_router.post("/worker-tasks/restart", tags=["worker-tasks"])
def restart_worker(services: Services) -> Any:
    return services.worker_tasks.restart_worker()


@api_router.get("/settings", tags=["settings"])
def show_config(services: Services) -> Any:
    return services.settings.show()


@api_router.get("/settings/business", tags=["settings"])
def show_business_settings(services: Services) -> Any:
    return services.settings.business_settings().show()


@api_router.get("/settings/business/value", tags=["settings"])
def get_business_settings_value(key: str, services: Services) -> Any:
    config = services.settings.business_settings()
    return {"key": key, "value": getattr(config, key)}


@api_router.patch("/settings/business", tags=["settings"])
def patch_business_settings(payload: SettingsPatch, services: Services) -> Any:
    return services.settings.business_settings().patch(payload.values)


@api_router.get("/settings/value", tags=["settings"])
def get_config_value(key: str, services: Services) -> Any:
    return {"key": key, "value": services.settings.get(key)}


@api_router.post("/settings/values", tags=["settings"])
def get_config_values(payload: SettingsGetMany, services: Services) -> Any:
    return services.settings.get_many(payload.keys)


@api_router.patch("/settings", tags=["settings"])
def set_config(payload: SettingsSet, services: Services) -> Any:
    return services.settings.set(payload.key, payload.value)


@api_router.patch("/settings/values", tags=["settings"])
def patch_config(payload: SettingsPatch, services: Services) -> Any:
    return services.settings.patch(payload.values)


@api_router.get("/accounts", tags=["accounts"])
def list_accounts(
    services: Services,
    project_id: list[str] = Query(default=[]),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return _filtered_page(
        services.accounts.list(project_ids=project_id),
        status_value=status_filter,
        limit=limit,
        offset=offset,
    )


@api_router.post("/accounts", status_code=status.HTTP_202_ACCEPTED, tags=["accounts"])
def create_account(payload: AccountCreate, services: Services) -> Any:
    return services.accounts.create(**payload.model_dump())


@api_router.get("/accounts/{account_id}", tags=["accounts"])
def get_account(account_id: str, services: Services) -> Any:
    return services.accounts.get(account_id)


@api_router.patch("/accounts/{account_id}", tags=["accounts"])
def update_account(account_id: str, payload: AccountUpdate, services: Services) -> Any:
    return services.accounts.update(
        account_id, **payload.model_dump(exclude_unset=True)
    )


@api_router.delete(
    "/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["accounts"]
)
def delete_account(account_id: str, services: Services) -> Response:
    services.accounts.delete(account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.post(
    "/accounts/{account_id}/collect",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["accounts"],
)
def collect_account(account_id: str, services: Services) -> Any:
    return services.accounts.request_collection(account_id)


@api_router.put("/accounts/{account_id}/projects", tags=["accounts", "projects"])
def set_account_projects(account_id: str, payload: IdList, services: Services) -> Any:
    return services.accounts.set_projects(account_id, payload.ids)


@api_router.get("/accounts/{account_id}/syncs", tags=["accounts", "sync"])
def account_syncs(account_id: str, services: Services) -> Any:
    return services.sync.list(account_id=account_id)


@api_router.get("/awemes", tags=["awemes"])
def list_awemes(
    services: Services,
    project_id: list[str] = Query(default=[]),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return _filtered_page(
        services.awemes.list(project_ids=project_id),
        status_value=status_filter,
        limit=limit,
        offset=offset,
    )


@api_router.post("/awemes", status_code=status.HTTP_202_ACCEPTED, tags=["awemes"])
def create_aweme(payload: AwemeCreate, services: Services) -> Any:
    return services.awemes.create(**payload.model_dump())


@api_router.get("/awemes/{aweme_id}", tags=["awemes"])
def get_aweme(aweme_id: str, services: Services) -> Any:
    return services.awemes.get(aweme_id)


@api_router.patch("/awemes/{aweme_id}", tags=["awemes"])
def update_aweme(aweme_id: str, payload: AwemeUpdate, services: Services) -> Any:
    return services.awemes.update(aweme_id, **payload.model_dump(exclude_unset=True))


@api_router.delete(
    "/awemes/{aweme_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["awemes"]
)
def delete_aweme(aweme_id: str, services: Services) -> Response:
    services.awemes.delete(aweme_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.post(
    "/awemes/{aweme_id}/collect",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["awemes"],
)
def collect_aweme(aweme_id: str, services: Services) -> Any:
    return services.awemes.request_collection(aweme_id)


@api_router.post("/awemes/{aweme_id}/fetch", tags=["awemes"])
def fetch_aweme(aweme_id: str, services: Services) -> Any:
    return services.awemes.fetch_data(services.awemes.get(aweme_id))


@api_router.post("/awemes/{aweme_id}/comments/fetch", tags=["awemes"])
def fetch_aweme_comments(aweme_id: str, services: Services) -> Any:
    # V1.0 发布隐藏：评论采集接口保留兼容，不触发真实评论抓取。
    return services.awemes.get(aweme_id)


@api_router.post("/awemes/{aweme_id}/video/download", tags=["awemes"])
def download_aweme_video(aweme_id: str, services: Services) -> Any:
    return services.awemes.download_video(services.awemes.get(aweme_id))


@api_router.put("/awemes/{aweme_id}/projects", tags=["awemes", "projects"])
def set_aweme_projects(aweme_id: str, payload: IdList, services: Services) -> Any:
    return services.awemes.set_projects(aweme_id, payload.ids)


@api_router.get("/awemes/{aweme_id}/syncs", tags=["awemes", "sync"])
def aweme_syncs(aweme_id: str, services: Services) -> Any:
    return services.sync.list(aweme_id=aweme_id)


def _list_projects(services: Services, include_disabled: bool = False) -> Any:
    values = services.projects.list()
    if include_disabled:
        return values
    return [
        item for item in values if _value(getattr(item, "status", "active")) == "active"
    ]


@api_router.get("/projects", tags=["projects"])
def list_projects(services: Services, include_disabled: bool = False) -> Any:
    return _list_projects(services, include_disabled)


@api_router.post("/projects", status_code=status.HTTP_201_CREATED, tags=["projects"])
def create_project(payload: ProjectCreate, services: Services) -> Any:
    return services.projects.create(**payload.model_dump())


@api_router.get("/projects/{project_id}", tags=["projects"])
def get_project(project_id: str, services: Services) -> Any:
    return services.projects.get(project_id)


@api_router.patch("/projects/{project_id}", tags=["projects"])
def update_project(project_id: str, payload: ProjectUpdate, services: Services) -> Any:
    return services.projects.update(project_id, **payload.model_dump(exclude_unset=True))


@api_router.delete(
    "/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["projects"]
)
def delete_project(project_id: str, services: Services) -> Response:
    services.projects.delete(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.put("/projects/{project_id}/members", tags=["projects"])
def set_project_members(
    project_id: str, payload: ProjectMembersUpdate, services: Services
) -> Any:
    return services.projects.set_members(
        project_id,
        aweme_ids=payload.aweme_ids,
        account_ids=payload.account_ids,
    )


@api_router.get("/video-transcriptions", tags=["video-transcriptions"])
def list_transcriptions(
    services: Services,
    status_filter: str | None = Query(default=None, alias="status"),
    aweme_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return services.transcriptions.list_summaries(
        status=status_filter,
        aweme_id=aweme_id,
        limit=limit,
        offset=offset,
    )


@api_router.get("/local-media", tags=["video-transcriptions"])
def browse_local_media(
    services: Services,
    root_id: str | None = None,
    path: str = "",
) -> Any:
    return services.local_files.browse(root_id=root_id, path=path)


@api_router.post(
    "/video-transcriptions",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["video-transcriptions"],
)
def create_transcription(payload: TranscriptionCreate, services: Services) -> Any:
    return services.transcriptions.create(**payload.model_dump())


@api_router.get(
    "/video-transcriptions/{transcription_id}", tags=["video-transcriptions"]
)
def get_transcription(transcription_id: str, services: Services) -> Any:
    return services.transcriptions.get(transcription_id)


@api_router.get(
    "/video-transcriptions/{transcription_id}/syncs",
    tags=["video-transcriptions", "sync"],
)
def transcription_syncs(transcription_id: str, services: Services) -> Any:
    return services.sync.list(transcription_id=transcription_id)


@api_router.post(
    "/video-transcriptions/{transcription_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["video-transcriptions"],
)
def retry_transcription(transcription_id: str, services: Services) -> Any:
    return services.transcriptions.retry(transcription_id)


@api_router.post(
    "/video-transcriptions/{transcription_id}/cancel",
    tags=["video-transcriptions"],
)
def cancel_transcription(transcription_id: str, services: Services) -> Any:
    return services.transcriptions.cancel(transcription_id)


@api_router.get("/sync", tags=["sync"])
def list_syncs(
    services: Services,
    aweme_id: str | None = None,
    account_id: str | None = None,
    transcription_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
) -> Any:
    return _filtered_page(
        services.sync.list(
            aweme_id=aweme_id,
            account_id=account_id,
            transcription_id=transcription_id,
        ),
        status_value=status_filter,
    )


@api_router.get("/sync/{sync_id}", tags=["sync"])
def get_sync(sync_id: str, services: Services) -> Any:
    return services.sync.get(sync_id)


@api_router.post(
    "/sync/{sync_id}/retry", status_code=status.HTTP_202_ACCEPTED, tags=["sync"]
)
@api_router.post(
    "/syncs/{sync_id}/retry", status_code=status.HTTP_202_ACCEPTED, tags=["sync"]
)
def retry_sync(sync_id: str, services: Services) -> Any:
    return services.sync.retry(sync_id)


@api_router.post(
    "/sync/{sync_id}/cancel", status_code=status.HTTP_202_ACCEPTED, tags=["sync"]
)
@api_router.post(
    "/syncs/{sync_id}/cancel", status_code=status.HTTP_202_ACCEPTED, tags=["sync"]
)
def cancel_sync(sync_id: str, services: Services) -> Any:
    return services.sync.cancel(sync_id)


@api_router.post("/sync/{sync_id}/enable", tags=["sync"])
@api_router.post("/syncs/{sync_id}/enable", tags=["sync"])
def enable_sync(sync_id: str, services: Services) -> Any:
    return services.sync.enable(sync_id)


@api_router.post("/sync/{sync_id}/disable", tags=["sync"])
@api_router.post("/syncs/{sync_id}/disable", tags=["sync"])
def disable_sync(sync_id: str, services: Services) -> Any:
    return services.sync.disable(sync_id)


@api_router.get("/worker-tasks", tags=["worker-tasks"])
def list_worker_tasks(services: Services, task_type: str | None = None) -> Any:
    values = services.worker_tasks.list()
    if not task_type:
        return values
    return [
        item
        for item in values
        if _value(getattr(item, "task_type", None)) == task_type
    ]


@api_router.get("/worker-tasks/{worker_task_id}", tags=["worker-tasks"])
def get_worker_task(worker_task_id: str, services: Services) -> Any:
    return services.worker_tasks.get(worker_task_id)


@api_router.post("/worker-tasks/{worker_task_id}/stop", tags=["worker-tasks"])
def stop_worker_task(worker_task_id: str, services: Services) -> Any:
    return services.worker_tasks.stop(worker_task_id)
