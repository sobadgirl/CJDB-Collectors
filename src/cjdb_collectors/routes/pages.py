"""Template and form routes."""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from cjdb_collectors.api.dependencies import get_services
from cjdb_collectors.exceptions import InvalidOperationError, NotFoundError
from cjdb_collectors.models import ContentType, Platform
from cjdb_collectors.models.display import (
    display_count,
    display_gender,
    display_location,
    display_registered_at,
)
from cjdb_collectors.models.enums import (
    display_content_type,
    display_platform,
    display_task_status,
)
from cjdb_collectors.domains.provider import ProviderType

Services = Annotated[Any, Depends(get_services)]
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates")
)
pages_router = APIRouter(include_in_schema=False)
_TAB_PATHS = {"awemes": "/awemes", "accounts": "/accounts"}
_COLLECT_PROVIDER_TYPES = {
    "awemes": (
        ProviderType.DOUYIN_AWEME_COLLECT,
        ProviderType.XIAOHONGSHU_AWEME_COLLECT,
        ProviderType.WECHAT_CHANNELS_AWEME_COLLECT,
        ProviderType.WECHAT_MP_AWEME_COLLECT,
    ),
    "accounts": (
        ProviderType.DOUYIN_ACCOUNT_COLLECT,
        ProviderType.XIAOHONGSHU_ACCOUNT_COLLECT,
        ProviderType.WECHAT_CHANNELS_ACCOUNT_COLLECT,
        ProviderType.WECHAT_MP_ACCOUNT_COLLECT,
    ),
    "video_transcription": (ProviderType.VIDEO_TRANSCRIPTION,),
}
_V1_HIDDEN_FEATURE_NOTE = "V1.0 发布隐藏"
_V1_SETTINGS_PROVIDER_TYPES = (
    ProviderType.DOUYIN_AWEME_COLLECT,
    ProviderType.XIAOHONGSHU_AWEME_COLLECT,
    ProviderType.WECHAT_CHANNELS_AWEME_COLLECT,
    ProviderType.WECHAT_MP_AWEME_COLLECT,
    ProviderType.VIDEO_TRANSCRIPTION,
)
_ACTIVE_PROJECT_COOKIE = "cjdb_active_project_id"


def _local_media_url(path: str | None) -> str | None:
    if not path:
        return None
    return f"/api/media?path={quote(str(path), safe='')}"


def _media_display_src(remote_url: str | None, local_path: str | None) -> str | None:
    return _local_media_url(local_path) or remote_url


def _photo_local_path_map(photo_paths: Any) -> dict[str, str]:
    if not isinstance(photo_paths, list):
        return {}
    mapped: dict[str, str] = {}
    for item in photo_paths:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        local_path = item.get("local_path")
        if url and local_path:
            mapped[str(url)] = str(local_path)
    return mapped


def _merge_photo_media(photos: Any, photo_paths: Any) -> list[dict[str, str | None]]:
    remote_urls = [str(url) for url in photos or [] if url]
    local_by_url = _photo_local_path_map(photo_paths)
    legacy_local_paths = [
        str(path) for path in photo_paths or [] if isinstance(path, str) and path
    ]
    items: list[dict[str, str | None]] = []
    for index, url in enumerate(remote_urls):
        local_path = local_by_url.get(url)
        if local_path is None and index < len(legacy_local_paths):
            local_path = legacy_local_paths[index]
        items.append(
            {
                "url": url,
                "local_path": local_path,
                "src": _media_display_src(url, local_path),
            }
        )
    return items


def _photo_local_paths(photo_paths: Any) -> list[str]:
    if not isinstance(photo_paths, list):
        return []
    paths: list[str] = []
    for item in photo_paths:
        if isinstance(item, str) and item:
            paths.append(item)
        elif isinstance(item, dict) and item.get("local_path"):
            paths.append(str(item["local_path"]))
    return paths


templates.env.globals["local_media_url"] = _local_media_url
templates.env.globals["media_display_src"] = _media_display_src
templates.env.globals["merge_photo_media"] = _merge_photo_media
templates.env.globals["photo_local_paths"] = _photo_local_paths
templates.env.globals["display_platform"] = display_platform
templates.env.globals["display_content_type"] = display_content_type
templates.env.globals["display_task_status"] = display_task_status
templates.env.globals["display_count"] = display_count
templates.env.globals["display_gender"] = display_gender
templates.env.globals["display_location"] = display_location
templates.env.globals["display_registered_at"] = display_registered_at
templates.env.globals["platform_logo_src"] = (
    lambda platform: f"/static/platforms/{getattr(platform, 'value', platform)}.svg"
)


@pages_router.get("/api/media")
def local_media_file(path: str = Query(min_length=1)) -> FileResponse:
    selected = Path(path).expanduser()
    if not selected.is_absolute():
        raise InvalidOperationError("media path must be absolute")
    try:
        resolved = selected.resolve(strict=True)
    except OSError as exc:
        raise NotFoundError("media file not found") from exc
    if not resolved.is_file():
        raise InvalidOperationError("media path is not a file")
    return FileResponse(resolved)


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.get("items", []))
    items = getattr(value, "items", None)
    return list(items if items is not None else value)


def _record_projects(service: Any, records: list[Any]) -> dict[str, set[str]]:
    return {
        str(record.id): {str(value) for value in service.project_ids(record.id)}
        for record in records
    }


def _cookie_project_ids(request: Request) -> list[str]:
    value = request.cookies.get(_ACTIVE_PROJECT_COOKIE)
    return [value] if value else []


def _selected_project_ids(
    projects: list[Any],
    selected: list[str],
    fallback_selected: list[str] | None = None,
) -> list[str]:
    active = [str(project.id) for project in projects]
    selected_active = [str(value) for value in selected if str(value) in active]
    if selected_active:
        return selected_active[:1]
    fallback_active = [
        str(value)
        for value in fallback_selected or []
        if str(value) in active
    ]
    if fallback_active:
        return fallback_active[:1]
    return active[:1]


def _current_project(projects: list[Any], selected: list[str]) -> Any:
    selected_id = selected[0] if selected else None
    return next((item for item in projects if str(item.id) == selected_id), None)


def _record_syncs(
    services: Any, tab: str, records: list[Any], storers: list[Any]
) -> dict[str, list[dict[str, Any]]]:
    storers_by_id = {str(storer.id): storer for storer in storers}
    syncs_by_record: dict[str, list[dict[str, Any]]] = {
        str(record.id): [] for record in records
    }
    record_ids = [record.id for record in records]
    if tab == "awemes":
        syncs = services.sync.list_for_awemes(record_ids)
    elif tab == "accounts":
        syncs = services.sync.list_for_accounts(record_ids)
    else:
        syncs = services.sync.list_for_transcriptions(record_ids)
    for sync in syncs:
        record_id = str(sync.object_id)
        syncs_by_record.setdefault(record_id, []).append(
            {
                "id": str(sync.id),
                "status": getattr(sync.status, "value", str(sync.status)),
                "enabled": sync.enabled,
                "storer": storers_by_id.get(str(sync.provider_id)),
                "error_message": sync.error_message,
            }
        )
    return syncs_by_record


def _record_authors(services: Any, tab: str, records: list[Any]) -> dict[str, Any]:
    if tab != "awemes":
        return {}
    keys = [
        (record.platform, record.platform_account_id)
        for record in records
        if getattr(record, "platform_account_id", None)
    ]
    authors = services.accounts.list_by_platform_account_ids(keys)
    return {
        str(record.id): authors.get(
            services.accounts.platform_account_key(
                record.platform,
                record.platform_account_id,
            )
        )
        for record in records
    }


def _record_transcriptions(
    services: Any, tab: str, records: list[Any]
) -> dict[str, Any]:
    if tab != "awemes":
        return {}
    return services.transcriptions.current_by_aweme_ids(record.id for record in records)


def _tab_path(tab: str) -> str:
    return _TAB_PATHS.get(tab, "/awemes")


def _tab_from_path(path: str, fallback: str = "awemes") -> str:
    if path == "/accounts":
        return "accounts"
    if path == "/awemes":
        return "awemes"
    return fallback


def _project_ids_from_url(value: str) -> list[str]:
    parsed = urlparse(value)
    return [item for item in parse_qs(parsed.query).get("project_id", []) if item]


def _list_url(
    tab: str,
    *,
    project_id: list[str] | None = None,
    page: int = 1,
    per_page: int = 20,
) -> str:
    params = [f"page={page}", f"per_page={per_page}"]
    params.extend(f"project_id={value}" for value in project_id or [])
    return f"{_tab_path(tab)}?{'&'.join(params)}"


def _data_list_context(
    services: Any,
    *,
    tab: str,
    project_id: list[str],
    fallback_project_id: list[str] | None = None,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    projects = _items(services.projects.list())
    project_id = _selected_project_ids(projects, project_id, fallback_project_id)
    service = services.awemes if tab == "awemes" else services.accounts
    fetched_records = _items(
        service.list(
            project_ids=project_id,
            limit=per_page + 1,
            offset=(page - 1) * per_page,
        )
    )
    records = fetched_records[:per_page]
    subject_type = "aweme" if tab == "awemes" else "account"
    current_project_id = project_id[0] if project_id else None
    storers = _items(
        services.stores.list(
            subject_type=subject_type,
            project_id=current_project_id,
        )
    )
    delete_files = (
        {
            str(record.id): services.awemes.deletion_files(record)
            for record in records
        }
        if tab == "awemes"
        else {str(record.id): [] for record in records}
    )
    # V1.0 发布隐藏：自动轮询会重建列表 DOM，导致横向/纵向滚动位置丢失。
    # 先关闭自动刷新，用户需要最新状态时手动刷新页面。
    poll_interval = None
    poll_params = [f"tab={tab}", f"page={page}", f"per_page={per_page}"]
    poll_params.extend(f"project_id={value}" for value in project_id)
    current_list_url = _list_url(
        tab,
        project_id=project_id,
        page=page,
        per_page=per_page,
    )
    return {
        "tab": tab,
        "base_path": _tab_path(tab),
        "current_list_url": current_list_url,
        "records": records,
        "projects": projects,
        "current_project": _current_project(projects, project_id),
        "selected_project_ids": project_id,
        "record_project_ids": _record_projects(service, records),
        "record_syncs": _record_syncs(services, tab, records, storers),
        "record_authors": _record_authors(services, tab, records),
        "record_transcriptions": _record_transcriptions(services, tab, records),
        "record_delete_files": delete_files,
        "worker_health": services.worker_tasks.health(),
        "storers": storers,
        "poll_interval": poll_interval,
        "poll_url": f"/partials/data-list?{'&'.join(poll_params)}",
        "pagination": {
            "page": page,
            "per_page": per_page,
            "has_next": len(fetched_records) > per_page,
        },
    }


def _data_list_context_from_next_url(
    request: Request,
    services: Any,
    next_url: str,
    *,
    fallback_tab: str = "awemes",
) -> dict[str, Any]:
    parsed = urlparse(_safe_next_url(next_url, _tab_path(fallback_tab)))
    params = parse_qs(parsed.query)
    tab = _tab_from_path(parsed.path, params.get("tab", [fallback_tab])[0])
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
        project_id=params.get("project_id", []),
        fallback_project_id=_cookie_project_ids(request),
        page=page,
        per_page=per_page,
    )


def _set_active_project_cookie(response: Response, project_id: list[str]) -> Response:
    if project_id:
        response.set_cookie(
            _ACTIVE_PROJECT_COOKIE,
            project_id[0],
            httponly=True,
            samesite="lax",
        )
    else:
        response.delete_cookie(_ACTIVE_PROJECT_COOKIE)
    return response


def _after_list_action(
    request: Request,
    services: Any,
    next_url: str,
    *,
    fallback_tab: str = "awemes",
) -> Response:
    safe_url = _safe_next_url(next_url, _tab_path(fallback_tab))
    if request.headers.get("HX-Request") == "true":
        context = _data_list_context_from_next_url(
            request,
            services,
            safe_url,
            fallback_tab=fallback_tab,
        )
        response = templates.TemplateResponse(
            request,
            "partials/data_list.html",
            context,
        )
        return _set_active_project_cookie(response, context["selected_project_ids"])
    response = RedirectResponse(safe_url, status_code=303)
    return _set_active_project_cookie(
        response,
        _project_ids_from_url(safe_url) or _cookie_project_ids(request),
    )


def _account_history_context(services: Any, account: Any) -> dict[str, Any]:
    history_awemes = []
    if getattr(account, "platform_account_id", None):
        history_awemes = _items(
            services.awemes.list(
                platform=account.platform,
                platform_account_id=account.platform_account_id,
                limit=20,
            )
        )
    return {
        "record": account,
        "account_history_awemes": history_awemes,
    }


def _record_detail_context(services: Any, tab: str, record_id: str) -> dict[str, Any]:
    if tab == "awemes":
        record = services.awemes.get(record_id)
        photo_media = _merge_photo_media(record.photos, record.photo_paths)
        photo_sources = [item["src"] for item in photo_media if item.get("src")]
        cover_source = _media_display_src(record.cover_url, record.cover_path)
        preview_images = photo_sources + (
            [cover_source] if cover_source and not photo_sources else []
        )
        author = None
        if record.platform_account_id:
            author = services.accounts.list_by_platform_account_ids(
                [(record.platform, record.platform_account_id)]
            ).get(
                services.accounts.platform_account_key(
                    record.platform,
                    record.platform_account_id,
                )
            )
        return {
            "tab": tab,
            "record": record,
            "current_transcription": services.transcriptions.current_by_aweme_ids(
                [record.id]
            ).get(str(record.id)),
            "content_type": (
                record.content_type.value
                if getattr(record.content_type, "value", None)
                else record.content_type
            ),
            "collection_status": (
                record.collection_status.value
                if getattr(record.collection_status, "value", None)
                else record.collection_status
            ),
            "collection_status_display": record.collection_status_display,
            "cover_source": cover_source,
            "preview_images": preview_images,
            "author": author,
        }
    record = services.accounts.get(record_id)
    return {
        **_account_history_context(services, record),
        "tab": tab,
        "collection_status": (
            record.collection_status.value
            if getattr(record.collection_status, "value", None)
            else record.collection_status
        ),
        "collection_status_display": record.collection_status_display,
    }


def _transcription_detail_context(services: Any, transcription_id: str) -> dict[str, Any]:
    item = services.transcriptions.get(transcription_id)
    return {
        "item": item,
        "status_value": (
            item.status.value if getattr(item.status, "value", None) else item.status
        ),
        "status_display": item.status_display,
    }


@pages_router.get("/")
def root_dashboard() -> RedirectResponse:
    return RedirectResponse("/awemes", status_code=303)


def _dashboard_response(
    request: Request,
    services: Any,
    *,
    tab: str,
    project_id: list[str],
    page: int,
    per_page: int,
) -> HTMLResponse:
    projects = _items(services.projects.list())
    project_id = _selected_project_ids(projects, project_id, _cookie_project_ids(request))
    service = services.awemes if tab == "awemes" else services.accounts
    fetched_records = _items(
        service.list(project_ids=project_id, limit=per_page + 1, offset=(page - 1) * per_page)
    )
    records = fetched_records[:per_page]
    subject_type = "aweme" if tab == "awemes" else "account"
    storers = _items(services.stores.list(subject_type=subject_type))
    project_provider_ids = {
        str(project.id): {
            str(value) for value in services.projects.provider_ids(project.id)
        }
        for project in projects
    }
    delete_files = (
        {
            str(record.id): services.awemes.deletion_files(record)
            for record in records
        }
        if tab == "awemes"
        else {str(record.id): [] for record in records}
    )
    list_context = _data_list_context(
        services,
        tab=tab,
        project_id=project_id,
        fallback_project_id=_cookie_project_ids(request),
        page=page,
        per_page=per_page,
    )
    response = templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "tab": tab,
            "base_path": _tab_path(tab),
            "current_list_url": _list_url(
                tab,
                project_id=project_id,
                page=page,
                per_page=per_page,
            ),
            "section_title": (
                "对标作品管理" if tab == "awemes" else "对标账号管理"
            ),
            "section_subtitle": (
                "管理对标作品的采集、下载、转写和同步。"
                if tab == "awemes"
                else "管理对标账号的采集、项目归属和同步。"
            ),
            "projects": projects,
            "current_project": _current_project(projects, project_id),
            "selected_project_ids": project_id,
            "records": records,
            "record_project_ids": _record_projects(service, records),
            "record_syncs": _record_syncs(services, tab, records, storers),
            "record_authors": list_context["record_authors"],
            "record_transcriptions": list_context["record_transcriptions"],
            "record_delete_files": delete_files,
            "worker_health": list_context["worker_health"],
            "storers": storers,
            "project_provider_ids": project_provider_ids,
            "provider_catalog": services.providers.catalog_for_types(
                _COLLECT_PROVIDER_TYPES[tab],
                include_status=False,
            ),
            "poll_interval": list_context["poll_interval"],
            "poll_url": list_context["poll_url"],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "has_next": len(fetched_records) > per_page,
            },
        },
    )
    return _set_active_project_cookie(response, project_id)


@pages_router.get("/awemes", response_class=HTMLResponse)
def awemes_page(
    request: Request,
    services: Services,
    project_id: list[str] = Query(default=[]),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> HTMLResponse:
    return _dashboard_response(
        request,
        services,
        tab="awemes",
        project_id=project_id,
        page=page,
        per_page=per_page,
    )


@pages_router.get("/accounts", response_class=HTMLResponse)
def accounts_page(
    request: Request,
    services: Services,
    project_id: list[str] = Query(default=[]),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> HTMLResponse:
    projects = _items(services.projects.list())
    selected_project_ids = _selected_project_ids(
        projects,
        project_id,
        _cookie_project_ids(request),
    )
    project_provider_ids = {
        str(project.id): {
            str(value) for value in services.projects.provider_ids(project.id)
        }
        for project in projects
    }
    response = templates.TemplateResponse(
        request,
        "coming_soon.html",
        {
            "section_title": "对标账号管理",
            "section_subtitle": "账号采集与同步将在后续版本开放。",
            "projects": projects,
            "current_project": _current_project(projects, selected_project_ids),
            "selected_project_ids": selected_project_ids,
            "project_provider_ids": project_provider_ids,
            "feature_note": _V1_HIDDEN_FEATURE_NOTE,
        },
    )
    return _set_active_project_cookie(response, selected_project_ids)


@pages_router.get("/partials/data-list", response_class=HTMLResponse)
def data_list(
    request: Request,
    services: Services,
    tab: str = Query(default="awemes", pattern="^(awemes|accounts)$"),
    project_id: list[str] = Query(default=[]),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> HTMLResponse:
    context = _data_list_context(
        services,
        tab=tab,
        project_id=project_id,
        fallback_project_id=_cookie_project_ids(request),
        page=page,
        per_page=per_page,
    )
    response = templates.TemplateResponse(
        request,
        "partials/data_list.html",
        context,
    )
    return _set_active_project_cookie(response, context["selected_project_ids"])


@pages_router.get("/partials/{tab}/{record_id}/detail", response_class=HTMLResponse)
def record_detail(
    request: Request,
    tab: str,
    record_id: str,
    services: Services,
) -> HTMLResponse:
    if tab == "transcriptions":
        return templates.TemplateResponse(
            request,
            "partials/transcription_detail_modal.html",
            _transcription_detail_context(services, record_id),
        )
    if tab not in {"awemes", "accounts"}:
        raise NotFoundError("detail not found")
    return templates.TemplateResponse(
        request,
        "partials/record_detail_modal.html",
        _record_detail_context(services, tab, record_id),
    )


@pages_router.post("/actions/awemes")
def add_aweme(
    request: Request,
    services: Services,
    platform: Annotated[Platform, Form()],
    url: Annotated[str | None, Form()] = None,
    platform_aweme_id: Annotated[str | None, Form()] = None,
    content_type: Annotated[ContentType, Form()] = ContentType.UNKNOWN,
    project_ids: Annotated[list[str] | None, Form()] = None,
    download_video: Annotated[bool, Form()] = False,
    collect_comments: Annotated[bool, Form()] = False,
    comment_max_count: Annotated[int | None, Form()] = None,
    transcribe: Annotated[bool, Form()] = False,
    next_url: Annotated[str, Form()] = "/awemes",
) -> Response:
    if platform == Platform.WECHAT_CHANNELS:
        raise InvalidOperationError("视频号作品采集暂不支持")
    # V1.0 发布隐藏：评论采集入口暂不开放，后端同步忽略手动提交的评论采集参数。
    collect_comments = False
    comment_max_count = None
    aweme = services.awemes.create(
        url=url,
        platform=platform,
        platform_aweme_id=platform_aweme_id,
        content_type=content_type,
        project_ids=project_ids or [],
        download_video=download_video,
        collect_comments=collect_comments,
        comment_max_count=comment_max_count,
        transcribe=transcribe,
    )
    services.awemes.request_collection(aweme.id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/collect")
def collect_aweme(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
) -> Response:
    services.awemes.request_collection(aweme_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/comments/collect")
def collect_aweme_comments(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
    comment_max_count: Annotated[int | None, Form()] = None,
    max_pages: Annotated[int | None, Form()] = None,
    earliest_date: Annotated[str | None, Form()] = None,
) -> Response:
    services.awemes.request_comment_collection(
        aweme_id,
        max_comments=comment_max_count,
        max_pages=max_pages,
        earliest_date=_parse_optional_datetime(earliest_date),
    )
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/collect/cancel")
def cancel_aweme_collection(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
) -> Response:
    services.awemes.cancel_collection(aweme_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/media/download")
def download_aweme_media(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
) -> Response:
    services.awemes.request_media_download(aweme_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/media/cancel")
def cancel_aweme_media_download(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
) -> Response:
    services.awemes.cancel_media_download(aweme_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/transcribe")
def transcribe_aweme(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
) -> Response:
    services.transcriptions.request_aweme_transcription(aweme_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/transcribe/cancel")
def cancel_aweme_transcription(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
) -> Response:
    services.transcriptions.cancel_aweme_transcription(aweme_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/awemes/{aweme_id}/comments/collect/cancel")
def cancel_aweme_comment_collection(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
) -> Response:
    services.awemes.cancel_comment_collection(aweme_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/sync/{sync_id}/retry")
@pages_router.post("/actions/syncs/{sync_id}/retry")
def retry_sync_relation(
    request: Request,
    sync_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
) -> Response:
    services.sync.retry(sync_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/sync/{sync_id}/cancel")
@pages_router.post("/actions/syncs/{sync_id}/cancel")
def cancel_sync_relation(
    request: Request,
    sync_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
) -> Response:
    services.sync.cancel(sync_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/accounts")
def add_account(
    request: Request,
    services: Services,
    platform: Annotated[Platform, Form()],
    url: Annotated[str | None, Form()] = None,
    platform_account_id: Annotated[str | None, Form()] = None,
    project_ids: Annotated[list[str] | None, Form()] = None,
    collect: Annotated[bool, Form()] = False,
    next_url: Annotated[str, Form()] = "/accounts",
) -> Response:
    account = services.accounts.create(
        url=url,
        platform=platform,
        platform_account_id=platform_account_id,
        project_ids=project_ids or [],
    )
    if collect:
        services.accounts.request_collection(account.id)
    return _after_list_action(request, services, next_url, fallback_tab="accounts")


@pages_router.post("/actions/accounts/{account_id}/collect")
def collect_account(
    request: Request,
    account_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/accounts",
) -> Response:
    services.accounts.request_collection(account_id)
    return _after_list_action(request, services, next_url, fallback_tab="accounts")


@pages_router.post("/actions/accounts/{account_id}/published-history")
def fetch_account_published_history(
    request: Request,
    account_id: str,
    services: Services,
    page_size: Annotated[int, Form()] = 20,
    mode: Annotated[str, Form()] = "backfill",
    max_count: Annotated[int | None, Form()] = None,
    max_pages: Annotated[int | None, Form()] = 1,
    earliest_date: Annotated[str | None, Form()] = None,
    next_url: Annotated[str, Form()] = "/accounts",
) -> Response:
    parsed_earliest_date = _parse_optional_datetime(earliest_date)
    services.accounts.request_published_history(
        account_id,
        latest=mode == "latest",
        page_size=page_size,
        max_count=max_count,
        max_pages=max_pages,
        earliest_date=parsed_earliest_date,
    )
    account = services.accounts.get(account_id)
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request,
            "partials/account_history.html",
            _account_history_context(services, account),
        )
    return RedirectResponse(_safe_next_url(next_url, "/accounts"), status_code=303)


@pages_router.post("/actions/awemes/{aweme_id}/delete")
def delete_aweme_action(
    request: Request,
    aweme_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/awemes",
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
    next_url: Annotated[str, Form()] = "/accounts",
) -> Response:
    services.accounts.delete(account_id)
    return _after_list_action(request, services, next_url, fallback_tab="accounts")


@pages_router.post("/actions/awemes/{aweme_id}/projects")
def update_aweme_projects(
    aweme_id: str,
    services: Services,
    project_ids: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse:
    services.awemes.set_projects(aweme_id, project_ids or [])
    return RedirectResponse("/awemes", status_code=303)


@pages_router.post("/actions/accounts/{account_id}/projects")
def update_account_projects(
    account_id: str,
    services: Services,
    project_ids: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse:
    services.accounts.set_projects(account_id, project_ids or [])
    return RedirectResponse("/accounts", status_code=303)


@pages_router.get("/transcriptions", response_class=HTMLResponse)
def transcriptions_page(
    request: Request,
    services: Services,
    project_id: list[str] = Query(default=[]),
) -> HTMLResponse:
    records = services.transcriptions.list(limit=100)
    storers = _items(services.stores.list(subject_type="video_transcription"))
    projects = _items(services.projects.list())
    selected_project_ids = _selected_project_ids(
        projects,
        project_id,
        _cookie_project_ids(request),
    )
    response = templates.TemplateResponse(
        request,
        "transcriptions.html",
        {
            "records": records,
            "storers": storers,
            "projects": projects,
            "current_project": _current_project(projects, selected_project_ids),
            "selected_project_ids": selected_project_ids,
            "record_syncs": _record_syncs(
                services,
                "transcriptions",
                records,
                storers,
            ),
            "provider_catalog": services.providers.catalog(
                "video_transcription",
                include_status=False,
            ),
        },
    )
    return _set_active_project_cookie(response, selected_project_ids)


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
def retry_transcription(
    request: Request,
    transcription_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/transcriptions",
) -> Response:
    services.transcriptions.retry(transcription_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.post("/actions/transcriptions/{transcription_id}/cancel")
def cancel_transcription(
    request: Request,
    transcription_id: str,
    services: Services,
    next_url: Annotated[str, Form()] = "/transcriptions",
) -> Response:
    services.transcriptions.cancel(transcription_id)
    return _after_list_action(request, services, next_url, fallback_tab="awemes")


@pages_router.get("/settings", response_class=HTMLResponse)
def config_page(
    request: Request,
    services: Services,
    project_id: list[str] = Query(default=[]),
) -> HTMLResponse:
    worker_tasks = [
        task
        for task in _items(services.worker_tasks.list())
        if getattr(task, "task_type", None) != "comment_collect"
    ]
    projects = _items(services.projects.list())
    selected_project_ids = _selected_project_ids(
        projects,
        project_id,
        _cookie_project_ids(request),
    )
    runtime_settings = services.settings.show()
    provider_catalog = services.providers.catalog_for_types(
        _V1_SETTINGS_PROVIDER_TYPES,
        include_status=False,
    )
    response = templates.TemplateResponse(
        request,
        "settings.html",
        {
            "worker_tasks": worker_tasks,
            "projects": projects,
            "current_project": _current_project(projects, selected_project_ids),
            "selected_project_ids": selected_project_ids,
            "runtime_settings": runtime_settings,
            "provider_catalog": provider_catalog,
        },
    )
    return _set_active_project_cookie(response, selected_project_ids)


@pages_router.post("/actions/projects")
def add_project(
    services: Services,
    name: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    color: Annotated[str | None, Form()] = None,
    next_url: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    project = services.projects.create(
        name=name, description=description, color=color, sort_order=0
    )
    redirect_url = _safe_next_url(next_url, "/awemes")
    parsed = urlparse(redirect_url)
    if parsed.path in {"/awemes", "/accounts"}:
        params = parse_qs(parsed.query)
        params["project_id"] = [str(project.id)]
        redirect_url = f"{parsed.path}?{urlencode(params, doseq=True)}"
    response = RedirectResponse(redirect_url, status_code=303)
    return _set_active_project_cookie(response, [str(project.id)])


@pages_router.post("/actions/projects/{project_id}")
def update_project(
    project_id: str,
    services: Services,
    name: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    color: Annotated[str | None, Form()] = None,
    next_url: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    services.projects.update(
        project_id,
        name=name,
        description=description,
        color=color,
    )
    return RedirectResponse(_safe_next_url(next_url, "/awemes"), status_code=303)


@pages_router.post("/actions/projects/{project_id}/delete")
def delete_project(project_id: str, services: Services) -> RedirectResponse:
    services.projects.delete(project_id)
    return RedirectResponse("/awemes", status_code=303)


@pages_router.post("/actions/settings")
def update_config(
    services: Services,
    key: Annotated[str, Form()],
    value: Annotated[str, Form()],
) -> RedirectResponse:
    services.settings.set(key, value)
    return RedirectResponse("/settings", status_code=303)


def _safe_next_url(value: str | None, fallback: str) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        parsed = urlparse(value)
        params = parse_qs(parsed.query)
        if parsed.path == "/" and params.get("tab", [None])[0] in _TAB_PATHS:
            tab = params.pop("tab")[0]
            query = urlencode(params, doseq=True)
            return f"{_tab_path(tab)}?{query}" if query else _tab_path(tab)
        return value
    return fallback


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = ["pages_router"]
