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

from cjdb_collectors.exceptions import InvalidOperationError

from .dependencies import get_services
from .schemas import (
    AccountCreate,
    AccountUpdate,
    AwemeCreate,
    AwemeUpdate,
    ConfigGetMany,
    ConfigPatch,
    ConfigSet,
    DataStorerCreate,
    DataStorerUpdate,
    GroupCreate,
    GroupMembersUpdate,
    GroupUpdate,
    IdList,
    ProviderSelection,
    ProviderSetup,
    StoreCreate,
    StoreSetup,
    StoreUpdate,
    TranscriptionCreate,
)

Services = Annotated[Any, Depends(get_services)]
api_router = APIRouter(prefix="/api/v1")


def _cjdb_command(*arguments: str) -> list[str]:
    return [sys.executable, "-m", "cjdb_collectors.cli", *arguments]


def _cjdb_environment(services: Any) -> dict[str, str]:
    return {
        **os.environ,
        "CJDB_CONFIG": str(services.settings.config_path),
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


def _store_view(
    services: Any,
    item: Any,
    *,
    default_ids: set | None = None,
) -> dict[str, Any]:
    return {
        **item.model_dump(mode="json"),
        "default": (
            item.id in default_ids
            if default_ids is not None
            else services.stores.is_default(item.id)
        ),
    }


def _read_log_page(
    path: Path,
    *,
    before: int | None,
    limit: int,
) -> dict[str, Any]:
    file_size = path.stat().st_size
    end = min(before if before is not None else file_size, file_size)
    if end <= 0:
        return {
            "lines": [],
            "start": 0,
            "end": 0,
            "total": file_size,
            "has_more": False,
        }

    chunk_size = 64 * 1024
    cursor = end
    newline_count = 0
    chunks: list[bytes] = []
    with path.open("rb") as handle:
        while cursor > 0 and newline_count <= limit:
            size = min(chunk_size, cursor)
            cursor -= size
            handle.seek(cursor)
            chunk = handle.read(size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")

        data = b"".join(reversed(chunks))
        base_offset = cursor
        if base_offset > 0:
            handle.seek(base_offset - 1)
            starts_on_boundary = handle.read(1) == b"\n"
            if not starts_on_boundary:
                first_newline = data.find(b"\n")
                if first_newline >= 0:
                    base_offset += first_newline + 1
                    data = data[first_newline + 1 :]

    entries: list[dict[str, Any]] = []
    offset = base_offset
    for raw_line in data.splitlines(keepends=True):
        entries.append(
            {
                "index": offset,
                "text": raw_line.rstrip(b"\r\n").decode(
                    "utf-8",
                    errors="replace",
                ),
            }
        )
        offset += len(raw_line)
    entries = entries[-limit:]
    start = entries[0]["index"] if entries else end
    return {
        "lines": entries,
        "start": start,
        "end": end,
        "total": file_size,
        "has_more": start > 0,
    }



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
) -> Any:
    return services.providers.catalog(
        type,
        include_configuration=True,
    )


@api_router.get("/providers/services", tags=["providers"])
def provider_services(services: Services) -> Any:
    return services.providers.services()


@api_router.get("/providers/status", tags=["providers"])
def provider_service_status(services: Services) -> Any:
    return services.providers.service_status()


@api_router.get("/providers/{provider_type}/status", tags=["providers"])
def provider_status(provider_type: str, services: Services) -> Any:
    return services.providers.status(provider_type)


@api_router.patch("/providers/selection", tags=["providers"])
def select_provider(payload: ProviderSelection, services: Services) -> Any:
    return services.providers.select(payload.type, payload.namespace)


@api_router.post("/providers/{provider_type}/setup", tags=["providers"])
def provider_setup(
    provider_type: str,
    payload: ProviderSetup,
    services: Services,
) -> Any:
    namespace = services.providers.selected_namespace(provider_type)
    setup_dir = Path(services.settings.app.data_dir) / "provider-setup"
    setup_dir.mkdir(parents=True, exist_ok=True)
    values_path = setup_dir / f"{namespace}-{uuid4().hex}.json"
    values_path.write_text(
        json.dumps(payload.values, ensure_ascii=False),
        encoding="utf-8",
    )
    values_path.chmod(0o600)

    log_path = Path(services.settings.app.logs_dir) / f"provider-{namespace}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("ab") as log:
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
                cwd=str(services.settings.config_path.parent),
                env=_cjdb_environment(services),
                start_new_session=True,
                close_fds=True,
            )
    except Exception:
        values_path.unlink(missing_ok=True)
        raise

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
        cwd=str(services.settings.config_path.parent),
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
    log_path = services.settings.app.logs_dir / f"provider-{namespace}.log"
    if not log_path.exists():
        return {
            "type": provider_type,
            "provider": {
                "name": provider["name"],
                "namespace": namespace,
            },
            "path": str(log_path),
            "lines": [],
            "start": 0,
            "end": 0,
            "total": 0,
            "has_more": False,
        }

    return {
        "type": provider_type,
        "provider": {
            "name": provider["name"],
            "namespace": namespace,
        },
        "path": str(log_path),
        **_read_log_page(
            log_path,
            before=before,
            limit=limit,
        ),
    }


@api_router.get("/worker-tasks/health", tags=["worker-tasks"])
def worker_health(services: Services) -> Any:
    return services.worker_tasks.health()


@api_router.post("/worker-tasks/start", tags=["worker-tasks"])
def start_worker(services: Services) -> Any:
    return services.worker_tasks.start_worker()


@api_router.post("/worker-tasks/stop", tags=["worker-tasks"])
def stop_worker(services: Services) -> Any:
    return services.worker_tasks.stop_worker()


@api_router.post("/worker-tasks/restart", tags=["worker-tasks"])
def restart_worker(services: Services) -> Any:
    return services.worker_tasks.restart_worker()


@api_router.get("/config", tags=["config"])
def show_config(services: Services) -> Any:
    return services.config.show()


@api_router.get("/settings", tags=["settings"])
def show_settings(services: Services) -> Any:
    return services.config.business_settings().show()


@api_router.get("/settings/value", tags=["settings"])
def get_setting_value(key: str, services: Services) -> Any:
    settings = services.config.business_settings()
    return {"key": key, "value": getattr(settings, key)}


@api_router.patch("/settings", tags=["settings"])
def patch_settings(payload: ConfigPatch, services: Services) -> Any:
    return services.config.business_settings().patch(payload.values)


@api_router.get("/config/value", tags=["config"])
def get_config_value(key: str, services: Services) -> Any:
    return {"key": key, "value": services.config.get(key)}


@api_router.post("/config/values", tags=["config"])
def get_config_values(payload: ConfigGetMany, services: Services) -> Any:
    return services.config.get_many(payload.keys)


@api_router.patch("/config", tags=["config"])
def set_config(payload: ConfigSet, services: Services) -> Any:
    return services.config.set(payload.key, payload.value)


@api_router.patch("/config/values", tags=["config"])
def patch_config(payload: ConfigPatch, services: Services) -> Any:
    return services.config.patch(payload.values)


@api_router.get("/accounts", tags=["accounts"])
def list_accounts(
    services: Services,
    group_id: list[str] = Query(default=[]),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return _filtered_page(
        services.accounts.list(group_ids=group_id),
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


@api_router.put("/accounts/{account_id}/groups", tags=["accounts"])
def set_account_groups(account_id: str, payload: IdList, services: Services) -> Any:
    return services.accounts.set_groups(account_id, payload.ids)


@api_router.get("/accounts/{account_id}/syncs", tags=["accounts", "sync"])
def account_syncs(account_id: str, services: Services) -> Any:
    return services.sync.list(account_id=account_id)


@api_router.get("/awemes", tags=["awemes"])
def list_awemes(
    services: Services,
    group_id: list[str] = Query(default=[]),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return _filtered_page(
        services.awemes.list(group_ids=group_id),
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
    return services.awemes.fetch_comments(services.awemes.get(aweme_id))


@api_router.post("/awemes/{aweme_id}/video/download", tags=["awemes"])
def download_aweme_video(aweme_id: str, services: Services) -> Any:
    return services.awemes.download_video(services.awemes.get(aweme_id))


@api_router.put("/awemes/{aweme_id}/groups", tags=["awemes"])
def set_aweme_groups(aweme_id: str, payload: IdList, services: Services) -> Any:
    return services.awemes.set_groups(aweme_id, payload.ids)


@api_router.get("/awemes/{aweme_id}/syncs", tags=["awemes", "sync"])
def aweme_syncs(aweme_id: str, services: Services) -> Any:
    return services.sync.list(aweme_id=aweme_id)


@api_router.get("/groups", tags=["groups"])
def list_groups(services: Services, include_disabled: bool = False) -> Any:
    values = services.groups.list()
    if include_disabled:
        return values
    return [
        item for item in values if _value(getattr(item, "status", "active")) == "active"
    ]


@api_router.post("/groups", status_code=status.HTTP_201_CREATED, tags=["groups"])
def create_group(payload: GroupCreate, services: Services) -> Any:
    return services.groups.create(**payload.model_dump())


@api_router.get("/groups/{group_id}", tags=["groups"])
def get_group(group_id: str, services: Services) -> Any:
    return services.groups.get(group_id)


@api_router.patch("/groups/{group_id}", tags=["groups"])
def update_group(group_id: str, payload: GroupUpdate, services: Services) -> Any:
    return services.groups.update(group_id, **payload.model_dump(exclude_unset=True))


@api_router.delete(
    "/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["groups"]
)
def delete_group(group_id: str, services: Services) -> Response:
    services.groups.delete(group_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.put("/groups/{group_id}/members", tags=["groups"])
def set_group_members(
    group_id: str, payload: GroupMembersUpdate, services: Services
) -> Any:
    return services.groups.set_members(
        group_id,
        aweme_ids=payload.aweme_ids,
        account_ids=payload.account_ids,
    )


@api_router.put("/groups/{group_id}/data-storers", tags=["groups"])
def set_group_data_storers(group_id: str, payload: IdList, services: Services) -> Any:
    return services.groups.set_stores(group_id, payload.ids)


@api_router.get("/groups/{group_id}/stores", tags=["groups", "stores"])
def get_group_stores(group_id: str, services: Services) -> Any:
    return {
        "ids": [
            str(store_id)
            for store_id in services.groups.store_ids(group_id)
        ]
    }


@api_router.put("/groups/{group_id}/stores", tags=["groups", "stores"])
def set_group_stores(group_id: str, payload: IdList, services: Services) -> Any:
    services.groups.set_stores(group_id, payload.ids)
    return {"group_id": group_id, "ids": payload.ids}


@api_router.get("/store-providers", tags=["stores"])
def list_store_providers(services: Services) -> Any:
    return services.store_providers.list()


@api_router.get("/stores/defaults", tags=["stores"])
def list_default_stores(services: Services) -> Any:
    default_store_ids = services.stores.default_ids()
    default_ids = set(default_store_ids)
    return [
        _store_view(
            services,
            services.stores.get(store_id),
            default_ids=default_ids,
        )
        for store_id in default_store_ids
    ]


@api_router.get("/stores", tags=["stores"])
def list_stores(services: Services, include_disabled: bool = False) -> Any:
    default_ids = set(services.stores.default_ids())
    return [
        _store_view(services, item, default_ids=default_ids)
        for item in services.stores.list(include_disabled)
    ]


@api_router.post("/stores", status_code=status.HTTP_201_CREATED, tags=["stores"])
def create_store(payload: StoreCreate, services: Services) -> Any:
    item = services.stores.add(
        type=payload.type,
        name=payload.name,
        setup_values=payload.values,
        default=payload.default,
    )
    return _store_view(services, item)


@api_router.get("/stores/{store_id}", tags=["stores"])
def get_store(store_id: str, services: Services) -> Any:
    return _store_view(services, services.stores.get(store_id))


@api_router.patch("/stores/{store_id}", tags=["stores"])
def update_store(store_id: str, payload: StoreUpdate, services: Services) -> Any:
    item = services.stores.update(
        store_id,
        **payload.model_dump(exclude_unset=True),
    )
    return _store_view(services, item)


@api_router.delete(
    "/stores/{store_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["stores"],
)
def delete_store(store_id: str, services: Services) -> Response:
    services.stores.delete(store_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.post("/stores/{store_id}/setup", tags=["stores"])
def setup_store(store_id: str, payload: StoreSetup, services: Services) -> Any:
    return services.store_providers.setup(store_id, payload.values)


@api_router.get("/stores/{store_id}/status", tags=["stores"])
def store_status(store_id: str, services: Services) -> Any:
    return services.stores.status(store_id)


@api_router.put("/stores/{store_id}/default", tags=["stores"])
def set_default_store(store_id: str, services: Services) -> Any:
    return services.stores.set_default(store_id, True)


@api_router.delete("/stores/{store_id}/default", tags=["stores"])
def unset_default_store(store_id: str, services: Services) -> Any:
    return services.stores.set_default(store_id, False)


@api_router.get("/data-storer-types", tags=["data-storers"])
def list_data_storer_types(services: Services) -> Any:
    return services.stores.types()


@api_router.get("/data-storers", tags=["data-storers"])
def list_data_storers(services: Services, include_disabled: bool = False) -> Any:
    values = services.stores.list()
    if include_disabled:
        return values
    return [
        item
        for item in values
        if _value(getattr(item, "status", "active")) != "disabled"
    ]


@api_router.post(
    "/data-storers", status_code=status.HTTP_201_CREATED, tags=["data-storers"]
)
def create_data_storer(payload: DataStorerCreate, services: Services) -> Any:
    data = payload.model_dump()
    for name in (
        "connection_config",
        "container_config",
        "field_mapping",
        "attachment_policy",
    ):
        data[f"{name}_json"] = data.pop(name)
    return services.stores.create(**data)


@api_router.get("/data-storers/{data_storer_id}", tags=["data-storers"])
def get_data_storer(data_storer_id: str, services: Services) -> Any:
    return services.stores.get(data_storer_id)


@api_router.patch("/data-storers/{data_storer_id}", tags=["data-storers"])
def update_data_storer(
    data_storer_id: str, payload: DataStorerUpdate, services: Services
) -> Any:
    data = payload.model_dump(exclude_unset=True)
    for name in (
        "connection_config",
        "container_config",
        "field_mapping",
        "attachment_policy",
    ):
        if name in data:
            data[f"{name}_json"] = data.pop(name)
    return services.stores.update(data_storer_id, **data)


@api_router.delete(
    "/data-storers/{data_storer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["data-storers"],
)
def delete_data_storer(data_storer_id: str, services: Services) -> Response:
    services.stores.delete(data_storer_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.post("/data-storers/{data_storer_id}/validate", tags=["data-storers"])
def validate_data_storer(data_storer_id: str, services: Services) -> Any:
    return services.stores.validate(data_storer_id)


@api_router.get("/video-transcriptions", tags=["video-transcriptions"])
def list_transcriptions(
    services: Services,
    status_filter: str | None = Query(default=None, alias="status"),
    aweme_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    values = services.transcriptions.list()
    if aweme_id:
        values = [
            item for item in values if str(getattr(item, "aweme_id", "")) == aweme_id
        ]
    return _filtered_page(
        values, status_value=status_filter, limit=limit, offset=offset
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
    status_filter: str | None = Query(default=None, alias="status"),
) -> Any:
    return _filtered_page(
        services.sync.list(aweme_id=aweme_id, account_id=account_id),
        status_value=status_filter,
    )


@api_router.get("/sync/{sync_id}", tags=["sync"])
def get_sync(sync_id: str, services: Services) -> Any:
    return services.sync.get(sync_id)


@api_router.post(
    "/sync/{sync_id}/retry", status_code=status.HTTP_202_ACCEPTED, tags=["sync"]
)
def retry_sync(sync_id: str, services: Services) -> Any:
    return services.sync.retry(sync_id)


@api_router.post("/sync/{sync_id}/enable", tags=["sync"])
def enable_sync(sync_id: str, services: Services) -> Any:
    return services.sync.enable(sync_id)


@api_router.post("/sync/{sync_id}/disable", tags=["sync"])
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
