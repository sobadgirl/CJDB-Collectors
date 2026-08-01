"""Template and form routes."""

from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from cjdb_collectors.api.dependencies import get_services
from cjdb_collectors.models import ContentType, Platform

Services = Annotated[Any, Depends(get_services)]
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates")
)
pages_router = APIRouter(include_in_schema=False)


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.get("items", []))
    items = getattr(value, "items", None)
    return list(items if items is not None else value)


def _record_groups(service: Any, records: list[Any]) -> dict[str, set[str]]:
    return {
        str(record.id): {str(value) for value in service.group_ids(record.id)}
        for record in records
    }


def _record_syncs(
    services: Any, tab: str, records: list[Any], storers: list[Any]
) -> dict[str, list[dict[str, Any]]]:
    storers_by_id = {str(storer.id): storer for storer in storers}
    syncs_by_record: dict[str, list[dict[str, Any]]] = {
        str(record.id): [] for record in records
    }
    record_ids = [record.id for record in records]
    syncs = (
        services.sync.list_for_awemes(record_ids)
        if tab == "awemes"
        else services.sync.list_for_accounts(record_ids)
    )
    owner_field = "aweme_id" if tab == "awemes" else "account_id"
    for sync in syncs:
        record_id = str(getattr(sync, owner_field))
        syncs_by_record.setdefault(record_id, []).append(
            {
                "status": getattr(sync.status, "value", str(sync.status)),
                "storer": storers_by_id.get(str(sync.data_storer_id)),
                "error_message": sync.error_message,
            }
        )
    return syncs_by_record


def _data_list_context(
    services: Any,
    *,
    tab: str,
    group_id: list[str],
    page: int,
    per_page: int,
) -> dict[str, Any]:
    service = services.awemes if tab == "awemes" else services.accounts
    fetched_records = _items(
        service.list(
            group_ids=group_id,
            limit=per_page + 1,
            offset=(page - 1) * per_page,
        )
    )
    records = fetched_records[:per_page]
    groups = _items(services.groups.list())
    storers = _items(services.stores.list())
    delete_files = (
        {
            str(record.id): services.awemes.deletion_files(record)
            for record in records
        }
        if tab == "awemes"
        else {str(record.id): [] for record in records}
    )
    return {
        "tab": tab,
        "records": records,
        "groups": groups,
        "selected_group_ids": group_id,
        "record_group_ids": _record_groups(service, records),
        "record_syncs": _record_syncs(services, tab, records, storers),
        "record_delete_files": delete_files,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "has_next": len(fetched_records) > per_page,
        },
    }


def _data_list_context_from_next_url(
    services: Any,
    next_url: str,
    *,
    fallback_tab: str = "awemes",
) -> dict[str, Any]:
    parsed = urlparse(_safe_next_url(next_url, f"/?tab={fallback_tab}"))
    params = parse_qs(parsed.query)
    tab = params.get("tab", [fallback_tab])[0]
    if tab not in {"awemes", "accounts"}:
        tab = fallback_tab
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1
    try:
        per_page = min(100, max(1, int(params.get("per_page", ["20"])[0])))
    except ValueError:
        per_page = 20
    return _data_list_context(
        services,
        tab=tab,
        group_id=params.get("group_id", []),
        page=page,
        per_page=per_page,
    )


def _after_list_action(
    request: Request,
    services: Any,
    next_url: str,
    *,
    fallback_tab: str = "awemes",
) -> Response:
    safe_url = _safe_next_url(next_url, f"/?tab={fallback_tab}")
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request,
            "partials/data_list.html",
            _data_list_context_from_next_url(
                services,
                safe_url,
                fallback_tab=fallback_tab,
            ),
        )
    return RedirectResponse(safe_url, status_code=303)


@pages_router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    services: Services,
    tab: str = Query(default="awemes", pattern="^(awemes|accounts)$"),
    group_id: list[str] = Query(default=[]),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> HTMLResponse:
    groups = _items(services.groups.list())
    service = services.awemes if tab == "awemes" else services.accounts
    fetched_records = _items(
        service.list(group_ids=group_id, limit=per_page + 1, offset=(page - 1) * per_page)
    )
    records = fetched_records[:per_page]
    storers = _items(services.stores.list())
    group_storer_ids = {
        str(group.id): {
            str(value) for value in services.groups.store_ids(group.id)
        }
        for group in groups
    }
    delete_files = (
        {
            str(record.id): services.awemes.deletion_files(record)
            for record in records
        }
        if tab == "awemes"
        else {str(record.id): [] for record in records}
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "tab": tab,
            "groups": groups,
            "selected_group_ids": group_id,
            "records": records,
            "record_group_ids": _record_groups(service, records),
            "record_syncs": _record_syncs(services, tab, records, storers),
            "record_delete_files": delete_files,
            "storers": storers,
            "group_storer_ids": group_storer_ids,
            "provider_catalog": services.providers.catalog(include_status=False),
            "pagination": {
                "page": page,
                "per_page": per_page,
                "has_next": len(fetched_records) > per_page,
            },
        },
    )


@pages_router.get("/partials/data-list", response_class=HTMLResponse)
def data_list(
    request: Request,
    services: Services,
    tab: str = Query(default="awemes", pattern="^(awemes|accounts)$"),
    group_id: list[str] = Query(default=[]),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/data_list.html",
        _data_list_context(
            services,
            tab=tab,
            group_id=group_id,
            page=page,
            per_page=per_page,
        ),
    )


@pages_router.post("/actions/awemes")
def add_aweme(
    services: Services,
    url: Annotated[str, Form()],
    platform: Annotated[Platform, Form()],
    content_type: Annotated[ContentType, Form()] = ContentType.UNKNOWN,
    group_ids: Annotated[list[str] | None, Form()] = None,
    download_video: Annotated[bool, Form()] = False,
    collect_comments: Annotated[bool, Form()] = False,
    transcribe: Annotated[bool, Form()] = False,
) -> RedirectResponse:
    services.awemes.create(
        url=url,
        platform=platform,
        content_type=content_type,
        group_ids=group_ids or [],
        download_video=download_video,
        collect_comments=collect_comments,
        transcribe=transcribe,
    )
    return RedirectResponse("/", status_code=303)


@pages_router.post("/actions/awemes/{aweme_id}/collect")
def collect_aweme(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/",
) -> Response:
    aweme = services.awemes.get(aweme_id)
    try:
        services.awemes.fetch_data(aweme)
    except Exception:
        # The service records the failure; return to the status panel.
        pass
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/comments/collect")
def collect_aweme_comments(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/",
) -> Response:
    aweme = services.awemes.get(aweme_id)
    try:
        services.awemes.fetch_comments(aweme)
    except Exception:
        # The service records the failure; return to the status panel.
        pass
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/collect/cancel")
def cancel_aweme_collection(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/",
) -> Response:
    services.awemes.cancel_collection(aweme_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/comments/collect/cancel")
def cancel_aweme_comment_collection(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/",
) -> Response:
    services.awemes.cancel_comment_collection(aweme_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/accounts")
def add_account(
    services: Services,
    url: Annotated[str, Form()],
    platform: Annotated[Platform, Form()],
    group_ids: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse:
    services.accounts.create(url=url, platform=platform, group_ids=group_ids or [])
    return RedirectResponse("/?tab=accounts", status_code=303)


@pages_router.post("/actions/accounts/{account_id}/collect")
def collect_account(
    request: Request,
    account_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/?tab=accounts",
) -> Response:
    services.accounts.request_collection(account_id)
    return _after_list_action(request, services, next_url, fallback_tab="accounts")


@pages_router.post("/actions/awemes/{aweme_id}/delete")
def delete_aweme_action(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/?tab=awemes",
    delete_downloaded_files: Annotated[bool, Form()] = False,
) -> Response:
    services.awemes.delete(
        aweme_id,
        delete_downloaded_files=delete_downloaded_files,
    )
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/accounts/{account_id}/delete")
def delete_account_action(
    request: Request,
    account_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/?tab=accounts",
) -> Response:
    services.accounts.delete(account_id)
    return _after_list_action(request, services, next_url, fallback_tab="accounts")


@pages_router.post("/actions/awemes/{aweme_id}/groups")
def update_aweme_groups(
    aweme_id: str,
    services: Services,
    group_ids: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse:
    services.awemes.set_groups(aweme_id, group_ids or [])
    return RedirectResponse("/", status_code=303)


@pages_router.post("/actions/accounts/{account_id}/groups")
def update_account_groups(
    account_id: str,
    services: Services,
    group_ids: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse:
    services.accounts.set_groups(account_id, group_ids or [])
    return RedirectResponse("/?tab=accounts", status_code=303)


@pages_router.get("/transcriptions", response_class=HTMLResponse)
def transcriptions_page(request: Request, services: Services) -> HTMLResponse:
    records = _items(services.transcriptions.list())
    return templates.TemplateResponse(
        request,
        "transcriptions.html",
        {
            "records": records,
            "provider_catalog": services.providers.catalog(
                "video_transcription",
                include_status=False,
            ),
        },
    )


@pages_router.post("/actions/transcriptions")
def add_transcription(
    services: Services,
    url: Annotated[str | None, Form()] = None,
    local_root_id: Annotated[str | None, Form()] = None,
    local_path: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    video_path = None
    if local_root_id and local_path:
        video_path = str(services.local_files.resolve_file(local_root_id, local_path))
    services.transcriptions.create(video_path=video_path, url=url or None)
    return RedirectResponse("/transcriptions", status_code=303)


@pages_router.post("/actions/transcriptions/{transcription_id}/retry")
def retry_transcription(transcription_id: str, services: Services) -> RedirectResponse:
    services.transcriptions.retry(transcription_id)
    return RedirectResponse("/transcriptions", status_code=303)


@pages_router.post("/actions/transcriptions/{transcription_id}/cancel")
def cancel_transcription(transcription_id: str, services: Services) -> RedirectResponse:
    services.transcriptions.cancel(transcription_id)
    return RedirectResponse("/transcriptions", status_code=303)


@pages_router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, services: Services) -> HTMLResponse:
    storers = _items(services.stores.list())
    worker_tasks = _items(services.worker_tasks.list())
    groups = _items(services.groups.list())
    types = services.stores.types()
    runtime_config = services.config.show()
    provider_catalog = services.providers.catalog(include_status=False)
    group_storer_ids = {
        str(group.id): {
            str(value) for value in services.groups.store_ids(group.id)
        }
        for group in groups
    }
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "storers": storers,
            "worker_tasks": worker_tasks,
            "groups": groups,
            "storer_types": types,
            "runtime_config": runtime_config,
            "provider_catalog": provider_catalog,
            "group_storer_ids": group_storer_ids,
        },
    )


@pages_router.post("/actions/groups")
def add_group(
    services: Services,
    name: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    color: Annotated[str | None, Form()] = None,
    next_url: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    services.groups.create(
        name=name, description=description, color=color, sort_order=0
    )
    redirect_url = _safe_next_url(next_url, "/")
    return RedirectResponse(redirect_url, status_code=303)


@pages_router.post("/actions/groups/{group_id}")
def update_group(
    group_id: str,
    services: Services,
    name: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    color: Annotated[str | None, Form()] = None,
    data_storer_ids: Annotated[list[str] | None, Form()] = None,
    next_url: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    services.groups.update(
        group_id,
        name=name,
        description=description,
        color=color,
    )
    services.groups.set_stores(group_id, data_storer_ids or [])
    return RedirectResponse(_safe_next_url(next_url, "/"), status_code=303)


@pages_router.post("/actions/groups/{group_id}/delete")
def delete_group(group_id: str, services: Services) -> RedirectResponse:
    services.groups.delete(group_id)
    return RedirectResponse("/", status_code=303)


@pages_router.post("/actions/groups/{group_id}/data-storers")
def update_group_data_storers(
    group_id: str,
    services: Services,
    data_storer_ids: Annotated[list[str] | None, Form()] = None,
    next_url: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    services.groups.set_stores(group_id, data_storer_ids or [])
    return RedirectResponse(_safe_next_url(next_url, "/settings"), status_code=303)


@pages_router.post("/actions/data-storers")
def add_data_storer(
    services: Services,
    name: Annotated[str, Form()],
    secret_ref: Annotated[str, Form()],
    database_id: Annotated[str, Form()],
    group_id: Annotated[str | None, Form()] = None,
    next_url: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    storer = services.stores.create(
        name=name,
        type="notion",
        secret_ref=secret_ref,
        connection_config_json={},
        container_config_json={"database_id": database_id},
        field_mapping_json={},
        attachment_policy_json={},
        conflict_policy="upsert",
    )
    if group_id:
        existing_ids = {
            str(value) for value in services.groups.store_ids(group_id)
        }
        services.groups.set_stores(group_id, [*existing_ids, str(storer.id)])
    return RedirectResponse(_safe_next_url(next_url, "/settings"), status_code=303)


@pages_router.post("/actions/config")
def update_config(
    services: Services,
    key: Annotated[str, Form()],
    value: Annotated[str, Form()],
) -> RedirectResponse:
    services.config.set(key, value)
    return RedirectResponse("/settings", status_code=303)


def _safe_next_url(value: str | None, fallback: str) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return fallback

__all__ = ["pages_router"]
