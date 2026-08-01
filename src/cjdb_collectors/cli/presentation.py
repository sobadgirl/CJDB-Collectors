from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from io import StringIO
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table
from rich.text import Text


STATUS_LABELS = {
    "not_requested": "未开始",
    "pending": "待处理",
    "starting": "启动中",
    "running": "运行中",
    "stopping": "停止中",
    "succeeded": "成功",
    "failed": "失败",
    "retry_wait": "等待重试",
    "timeout": "超时",
    "cancelled": "已取消",
    "ready": "可用",
    "unconfigured": "未配置",
    "setting_up": "初始化中",
    "unavailable": "不可用",
    "error": "异常",
    "active": "启用",
    "needs_attention": "需处理",
    "disabled": "停用",
    "stopped": "已停止",
}
STATUS_STYLES = {
    "running": "yellow",
    "starting": "yellow",
    "setting_up": "yellow",
    "pending": "yellow",
    "retry_wait": "yellow",
    "ready": "green",
    "succeeded": "green",
    "active": "green",
    "failed": "red",
    "error": "red",
    "unavailable": "red",
    "timeout": "red",
    "needs_attention": "red",
    "not_requested": "dim",
    "unconfigured": "dim",
    "disabled": "dim",
    "stopped": "dim",
    "cancelled": "dim",
}
PLATFORM_LABELS = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "wechat_mp": "公众号",
    "wechat_channels": "视频号",
}
CONTENT_TYPE_LABELS = {
    "unknown": "自动识别",
    "video": "视频",
    "image": "图文",
    "article": "文章",
    "live": "直播",
}
WORKER_TYPE_LABELS = {
    "data_collect": "数据采集",
    "media_download": "媒体下载",
    "video_transcription": "视频转写",
    "comment_collect": "评论采集",
    "data_sync": "数据同步",
}
MESSAGE_LABELS = {
    "collector API key is not configured": "采集接口密钥未配置",
    "TikHub API key is not configured": "TikHub 接口密钥未配置",
    "faster-whisper is not installed": "Faster Whisper 未安装",
}
_ACTIVE_CONSOLE: ContextVar[Console | None] = ContextVar(
    "cli_active_console",
    default=None,
)


def render_text(data: Any, view: str = "generic") -> str:
    renderers: dict[str, Callable[[Any], None]] = {
        "aweme": _render_aweme,
        "aweme_list": _render_aweme_list,
        "account": _render_account,
        "account_list": _render_account_list,
        "transcription": _render_transcription,
        "transcription_list": _render_transcription_list,
        "group": _render_group,
        "group_list": _render_group_list,
        "store": _render_store,
        "store_list": _render_store_list,
        "store_type_list": _render_store_type_list,
        "store_status": _render_store_status,
        "store_result": _render_store_result,
        "sync": _render_sync,
        "sync_list": _render_sync_list,
        "provider_list": _render_provider_list,
        "provider_status": _render_provider_status,
        "provider_selection": _render_provider_selection,
        "provider_setup": _render_provider_setup,
        "provider_setup_requirements": _render_provider_setup_requirements,
        "runtime": _render_runtime,
        "worker_status": _render_worker_status,
        "settings": _render_settings,
        "setting": _render_setting,
        "id_list": _render_id_list,
        "message": _render_message,
    }
    buffer = StringIO()
    width = max(80, min(shutil.get_terminal_size((100, 20)).columns, 160))
    console = Console(
        file=buffer,
        highlight=False,
        markup=False,
        soft_wrap=False,
        force_terminal=False,
        color_system=None,
        width=width,
    )
    token = _ACTIVE_CONSOLE.set(console)
    try:
        renderers.get(view, _render_generic)(data)
    finally:
        _ACTIVE_CONSOLE.reset(token)
    return buffer.getvalue().rstrip()


def _console() -> Console:
    console = _ACTIVE_CONSOLE.get()
    if console is None:
        raise RuntimeError("text renderer is not active")
    return console


def _status(value: Any) -> Text:
    raw = str(value or "unknown")
    return Text(STATUS_LABELS.get(raw, raw), style=STATUS_STYLES.get(raw, ""))


def _platform(value: Any) -> str:
    raw = str(value or "")
    return PLATFORM_LABELS.get(raw, raw or "—")


def _content_type(value: Any) -> str:
    raw = str(value or "")
    return CONTENT_TYPE_LABELS.get(raw, raw or "—")


def _text(value: Any, *, empty: str = "—", limit: int | None = None) -> str:
    if value is None or value == "":
        return empty
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        result = ", ".join(_text(item) for item in value) or empty
    elif isinstance(value, dict):
        result = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        result = str(value)
    if limit and len(result) > limit:
        return f"{result[: limit - 1]}…"
    return result


def _number(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return _text(value)


def _datetime(value: Any) -> str:
    result = _text(value)
    return result.replace("T", " ", 1) if result != "—" else result


def _message(value: Any) -> str:
    raw = _text(value)
    if raw in MESSAGE_LABELS:
        return MESSAGE_LABELS[raw]
    prefix = "transcription model is not prepared:"
    if raw.startswith(prefix):
        return f"转写模型未准备：{raw.removeprefix(prefix).strip()}"
    if raw.startswith("[Errno"):
        return f"无法连接服务：{raw}"
    return raw


def _source(item: dict[str, Any]) -> str:
    return _text(
        item.get("source_url") or item.get("video_path") or item.get("aweme_id"),
        limit=44,
    )


def _table(
    title: str,
    rows: Iterable[dict[str, Any]],
    columns: list[tuple[str, Callable[[dict[str, Any]], Any]]],
    *,
    empty: str,
) -> None:
    values = list(rows)
    console = _console()
    if not values:
        console.print(empty, style="dim")
        return
    console.print(f"{title}（{len(values)}）", style="bold")
    table = Table(
        box=None,
        show_edge=False,
        pad_edge=False,
        collapse_padding=True,
        header_style="bold",
    )
    for label, _ in columns:
        table.add_column(label, overflow="fold")
    for item in values:
        cells = [renderer(item) for _, renderer in columns]
        table.add_row(
            *(cell if isinstance(cell, Text) else Text(str(cell)) for cell in cells)
        )
    console.print(table)


def _details(title: str, rows: Iterable[tuple[str, Any]]) -> None:
    console = _console()
    console.print(title, style="bold")
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column(overflow="fold")
    for label, value in rows:
        if value is None or value == "" or value == []:
            continue
        table.add_row(
            Text(label),
            value if isinstance(value, Text) else Text(_text(value)),
        )
    console.print(table)


def _record_list(
    title: str,
    rows: Iterable[dict[str, Any]],
    render: Callable[[dict[str, Any]], list[Any]],
    *,
    empty: str,
) -> None:
    values = list(rows)
    console = _console()
    if not values:
        console.print(empty, style="dim")
        return
    console.print(f"{title}（{len(values)}）", style="bold")
    for index, item in enumerate(values):
        if index:
            console.print()
        lines = render(item)
        for line_index, line in enumerate(lines):
            prefix = "" if line_index == 0 else "  "
            if isinstance(line, Text):
                value = Text(prefix)
                value.append(line)
                console.print(value)
            elif line:
                console.print(f"{prefix}{line}")


def _render_aweme_list(data: Any) -> None:
    def render(item: dict[str, Any]) -> list[Any]:
        header = Text(_text(item.get("id")), style="bold")
        header.append(
            f"  {_platform(item.get('platform'))}"
            f" · {_content_type(item.get('content_type'))} · "
        )
        header.append(_status(item.get("collection_status")))
        return [
            header,
            _text(item.get("title") or item.get("source_url"), limit=72),
            (
                f"播放 {_number(item.get('play_count'))}"
                f" · 点赞 {_number(item.get('like_count'))}"
                f" · 收藏 {_number(item.get('collect_count'))}"
                f" · 评论 {_number(item.get('comment_count'))}"
            ),
        ]

    _record_list(
        "作品",
        data or [],
        render,
        empty="暂无作品。",
    )


def _render_aweme(data: dict[str, Any]) -> None:
    _details(
        "作品",
        [
            ("ID", data.get("id")),
            ("平台", _platform(data.get("platform"))),
            ("类型", _content_type(data.get("content_type"))),
            ("标题", data.get("title")),
            ("描述", data.get("description")),
            ("平台作品 ID", data.get("platform_aweme_id")),
            ("来源 URL", data.get("source_url")),
            ("作品 URL", data.get("aweme_url")),
            ("发布时间", _datetime(data.get("published_at"))),
            ("播放量", _number(data.get("play_count"))),
            ("点赞量", _number(data.get("like_count"))),
            ("收藏量", _number(data.get("collect_count"))),
            ("转发量", _number(data.get("share_count"))),
            ("评论量", _number(data.get("comment_count"))),
            ("数据采集", _status(data.get("collection_status"))),
            ("媒体下载", _status(data.get("media_download_status"))),
            ("评论采集", _status(data.get("comment_collection_status"))),
            ("视频转写", _status(data.get("video_transcription_status"))),
            ("视频文件", data.get("video_path")),
            ("封面文件", data.get("cover_path")),
            ("图片文件", data.get("photo_paths")),
            ("最后采集", _datetime(data.get("last_collected_at"))),
            (
                "错误",
                data.get("collection_error")
                or data.get("media_download_error")
                or data.get("comment_collection_error"),
            ),
        ],
    )


def _render_account_list(data: Any) -> None:
    def render(item: dict[str, Any]) -> list[Any]:
        header = Text(_text(item.get("id")), style="bold")
        header.append(f"  {_platform(item.get('platform'))} · ")
        header.append(_status(item.get("collection_status")))
        return [
            header,
            _text(item.get("display_name") or item.get("profile_url"), limit=72),
            (
                f"平台账号 ID {_text(item.get('platform_account_id'))}"
                f" · 最后采集 {_datetime(item.get('last_collected_at'))}"
            ),
        ]

    _record_list(
        "账号",
        data or [],
        render,
        empty="暂无账号。",
    )


def _render_account(data: dict[str, Any]) -> None:
    _details(
        "账号",
        [
            ("ID", data.get("id")),
            ("平台", _platform(data.get("platform"))),
            ("名称", data.get("display_name")),
            ("平台账号 ID", data.get("platform_account_id")),
            ("主页 URL", data.get("profile_url")),
            ("头像 URL", data.get("avatar_url")),
            ("头像文件", data.get("avatar_path")),
            ("采集状态", _status(data.get("collection_status"))),
            ("采集次数", data.get("collection_attempt_count")),
            ("最后采集", _datetime(data.get("last_collected_at"))),
            ("错误", data.get("collection_error")),
        ],
    )


def _render_transcription_list(data: Any) -> None:
    def render(item: dict[str, Any]) -> list[Any]:
        header = Text(_text(item.get("id")), style="bold")
        header.append("  ")
        header.append(_status(item.get("status")))
        header.append(
            f" · {float(item.get('progress') or 0) * 100:.0f}%"
        )
        return [
            header,
            f"来源 {_source(item)}",
            (
                f"尝试 {_number(item.get('attempt_count'))}"
                f" · 更新时间 {_datetime(item.get('updated_at'))}"
            ),
        ]

    _record_list(
        "转写任务",
        data or [],
        render,
        empty="暂无转写任务。",
    )


def _render_transcription(data: dict[str, Any]) -> None:
    _details(
        "转写任务",
        [
            ("ID", data.get("id")),
            ("作品 ID", data.get("aweme_id")),
            ("来源 URL", data.get("source_url")),
            ("视频文件", data.get("video_path")),
            ("状态", _status(data.get("status"))),
            ("进度", f"{float(data.get('progress') or 0) * 100:.0f}%"),
            ("尝试次数", data.get("attempt_count")),
            ("开始时间", _datetime(data.get("started_at"))),
            ("完成时间", _datetime(data.get("finished_at"))),
            ("错误", data.get("error_message")),
            ("转写文本", data.get("normalized_text") or data.get("text")),
        ],
    )


def _render_group_list(data: Any) -> None:
    def render(item: dict[str, Any]) -> list[Any]:
        header = Text(_text(item.get("id")), style="bold")
        header.append(f"  {_text(item.get('name'))} · ")
        header.append(_status(item.get("status")))
        details = []
        if item.get("color"):
            details.append(f"颜色 {item['color']}")
        if item.get("description"):
            details.append(_text(item["description"], limit=68))
        return [header, " · ".join(details)]

    _record_list(
        "分组",
        data or [],
        render,
        empty="暂无分组。",
    )


def _render_group(data: dict[str, Any]) -> None:
    _details(
        "分组",
        [
            ("ID", data.get("id")),
            ("名称", data.get("name")),
            ("状态", _status(data.get("status"))),
            ("颜色", data.get("color")),
            ("排序", data.get("sort_order")),
            ("说明", data.get("description")),
            ("创建时间", _datetime(data.get("created_at"))),
            ("更新时间", _datetime(data.get("updated_at"))),
        ],
    )


def _render_store_type_list(data: Any) -> None:
    def capabilities(item: dict[str, Any]) -> str:
        labels = {
            "aweme": "作品",
            "account": "账号",
            "attachments": "附件",
        }
        return "、".join(
            labels.get(key, key)
            for key, enabled in (item.get("capabilities") or {}).items()
            if enabled
        ) or "—"

    _table(
        "可用 Store 类型",
        data or [],
        [
            ("类型", lambda item: _text(item.get("type"))),
            ("名称", lambda item: _text(item.get("name"))),
            ("能力", capabilities),
        ],
        empty="暂无可用的 Store 类型。",
    )


def _render_store_list(data: Any) -> None:
    def render(item: dict[str, Any]) -> list[Any]:
        header = Text(_text(item.get("id")), style="bold")
        header.append(f"  {_text(item.get('name'))} · {_text(item.get('type'))} · ")
        header.append(_status(item.get("status")))
        if item.get("default"):
            header.append(" · 默认 Store")
        return [
            header,
            (
                f"最后检查 {_datetime(item.get('last_validated_at'))}"
                if item.get("last_validated_at")
                else ""
            ),
        ]

    _record_list(
        "Store",
        data or [],
        render,
        empty="暂无 Store。",
    )


def _render_store(data: dict[str, Any]) -> None:
    _details(
        "Store",
        [
            ("ID", data.get("id")),
            ("名称", data.get("name")),
            ("类型", data.get("type")),
            ("状态", _status(data.get("status"))),
            ("默认 Store", "是" if data.get("default") else "否"),
            ("冲突策略", data.get("conflict_policy")),
            ("最后检查", _datetime(data.get("last_validated_at"))),
            ("错误", data.get("validation_error")),
        ],
    )


def _render_store_status(data: dict[str, Any]) -> None:
    store = data.get("store") or {}
    _details(
        "Store 状态",
        [
            ("ID", store.get("id")),
            ("名称", store.get("name")),
            ("类型", store.get("type")),
            ("状态", _status(data.get("status"))),
            ("可用", "是" if data.get("ready") else "否"),
            (
                "说明",
                _message(data.get("message"))
                if data.get("message")
                else None,
            ),
            ("检查时间", _datetime(data.get("checked_at"))),
        ],
    )


def _render_store_result(data: dict[str, Any]) -> None:
    _details(
        "写入结果",
        [
            ("远端记录 ID", data.get("remote_record_id")),
            ("远端 URL", data.get("remote_url")),
            ("附件", data.get("remote_attachment")),
        ],
    )


def _sync_subject(item: dict[str, Any]) -> str:
    if item.get("aweme_id"):
        return f"作品 {item['aweme_id']}"
    if item.get("account_id"):
        return f"账号 {item['account_id']}"
    return "—"


def _render_sync_list(data: Any) -> None:
    def render(item: dict[str, Any]) -> list[Any]:
        header = Text(_text(item.get("id")), style="bold")
        header.append("  ")
        header.append(_status(item.get("status")))
        header.append(" · 已启用" if item.get("enabled") else " · 已停用")
        return [
            header,
            _sync_subject(item),
            (
                f"Store {item.get('data_storer_id')}"
                f" · 尝试 {_number(item.get('attempt_count'))}"
                f" · 最后同步 {_datetime(item.get('last_synced_at'))}"
            ),
        ]

    _record_list(
        "同步任务",
        data or [],
        render,
        empty="暂无同步任务。",
    )


def _render_sync(data: dict[str, Any]) -> None:
    _details(
        "同步任务",
        [
            ("ID", data.get("id")),
            ("对象", _sync_subject(data)),
            ("Store ID", data.get("data_storer_id")),
            ("状态", _status(data.get("status"))),
            ("启用", "是" if data.get("enabled") else "否"),
            ("尝试次数", data.get("attempt_count")),
            ("远端记录 ID", data.get("remote_record_id")),
            ("远端 URL", data.get("remote_url")),
            ("最后同步", _datetime(data.get("last_synced_at"))),
            ("错误", data.get("error_message")),
        ],
    )


def _render_provider_list(data: dict[str, Any]) -> None:
    providers = data.get("providers", [])
    console = _console()
    if not providers:
        console.print("暂无可用的 Provider。", style="dim")
        return

    console.print(f"可用 Provider（{len(providers)}）", style="bold")
    for index, provider in enumerate(providers):
        if index:
            console.print()
        line = Text(_text(provider.get("name")), style="bold")
        line.append(f" [{provider.get('namespace')}]", style="dim")
        console.print(line)
        console.print(f"  命名空间  {_text(provider.get('namespace'))}")
        supported_types = provider.get("supported_types") or []
        labels = "、".join(
            _text(item.get("label") or item.get("type"))
            for item in supported_types
        )
        console.print(f"  支持服务  {labels or '—'}")


def _provider_name(item: dict[str, Any]) -> str:
    provider = item.get("provider")
    if not isinstance(provider, dict):
        return "未选择"
    name = _text(provider.get("name"))
    namespace = provider.get("namespace")
    return f"{name} [{namespace}]" if namespace else name


def _render_provider_status(data: dict[str, Any]) -> None:
    items = data.get("services") if "services" in data else [data]
    def render(item: dict[str, Any]) -> list[Any]:
        header = Text(_text(item.get("label") or item.get("type")), style="bold")
        header.append(" · ")
        header.append(_status(item.get("status")))
        return [
            header,
            f"Provider {_provider_name(item)}",
            (
                f"说明 {_message(item.get('message'))}"
                if item.get("message")
                else ""
            ),
        ]

    _record_list(
        "Provider 状态",
        items or [],
        render,
        empty="暂无 Provider 状态。",
    )


def _render_provider_selection(data: dict[str, Any]) -> None:
    provider = data.get("provider") or {}
    _details(
        "Provider 已切换",
        [
            ("服务", data.get("label") or data.get("type")),
            ("Provider", provider.get("name") or data.get("selected")),
            ("命名空间", provider.get("namespace")),
        ],
    )


def _render_provider_setup(data: dict[str, Any]) -> None:
    status = data.get("status") or {}
    provider = data.get("provider") or status
    _details(
        "Provider 初始化",
        [
            ("服务类型", data.get("type")),
            ("Provider", provider.get("name")),
            ("命名空间", provider.get("namespace")),
            ("状态", _status(status.get("status"))),
            (
                "说明",
                _message(status.get("message"))
                if status.get("message")
                else None,
            ),
        ],
    )
    logs = data.get("logs") or []
    if logs:
        console = _console()
        console.print("日志", style="bold")
        for line in logs:
            console.print(f"  {line}")


def _render_provider_setup_requirements(data: dict[str, Any]) -> None:
    provider = data.get("provider") or {}
    parameters = data.get("parameters") or []
    configured_parameters = data.get("configured_parameters") or {}
    _details(
        "Provider Setup 参数要求",
        [
            ("服务类型", data.get("label") or data.get("type")),
            ("Provider", provider.get("name")),
            ("命名空间", provider.get("namespace")),
            ("示例", data.get("example")),
        ],
    )
    console = _console()
    if not parameters:
        console.print("这个 Provider 不需要额外参数。", style="dim")
        return

    for parameter in parameters:
        key = str(parameter.get("key") or "")
        options = parameter.get("options") or []
        option_text = "、".join(
            _text(option.get("label") or option.get("value"))
            for option in options
        )
        configured = bool(configured_parameters.get(key))
        console.print()
        console.print(f"{key}", style="bold")
        rows = [
            ("类型", parameter.get("type")),
            ("必填", "是" if parameter.get("required") else "否"),
            (
                "默认值",
                _text(parameter.get("default"))
                if parameter.get("default") not in (None, "")
                else "留空",
            ),
            ("当前", "已配置" if configured else "未配置"),
            ("可选值", option_text or None),
            ("说明", parameter.get("help")),
        ]
        for label, value in rows:
            if value is None or value == "":
                continue
            console.print(f"  {label:<6} {_text(value)}")


def _render_runtime(data: dict[str, Any]) -> None:
    _details(
        "服务状态",
        [
            ("状态", _status(data.get("status"))),
            ("PID", data.get("pid")),
            ("日志", data.get("log") or data.get("log_path")),
        ],
    )


def _render_worker_status(data: dict[str, Any]) -> None:
    _details(
        "Worker 状态",
        [
            ("状态", _status(data.get("status"))),
            ("PID", data.get("pid")),
            ("运行任务", data.get("running")),
            ("心跳时间", _datetime(data.get("heartbeat_at"))),
            (
                "心跳延迟",
                (
                    f"{float(data['heartbeat_age_seconds']):.1f} 秒"
                    if data.get("heartbeat_age_seconds") is not None
                    else None
                ),
            ),
            ("心跳异常", "是" if data.get("heartbeat_stale") else "否"),
            ("日志", data.get("log_path")),
        ],
    )
    running = data.get("running_by_type") or {}
    limits = data.get("limits") or {}
    rows = [
        {
            "type": WORKER_TYPE_LABELS.get(key, key),
            "running": running.get(key, 0),
            "limit": limits.get(key, 0),
        }
        for key in limits
    ]
    if rows:
        _table(
            "任务进程",
            rows,
            [
                ("类型", lambda item: _text(item.get("type"))),
                ("运行中", lambda item: _number(item.get("running"))),
                ("上限", lambda item: _number(item.get("limit"))),
            ],
            empty="",
        )


def _render_settings(data: Any) -> None:
    if not isinstance(data, dict):
        _render_generic(data)
        return
    _console().print(
        yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).rstrip()
    )


def _render_setting(data: dict[str, Any]) -> None:
    value = data.get("value")
    if isinstance(value, (dict, list)):
        rendered = yaml.safe_dump(
            value,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).rstrip()
    else:
        rendered = _text(value, empty="null")
    _details(
        "配置",
        [
            ("键", data.get("key")),
            ("值", rendered),
            ("已更新", "是" if data.get("updated") else None),
        ],
    )


def _render_id_list(data: dict[str, Any]) -> None:
    values = data.get("items") or []
    console = _console()
    if not values:
        console.print(data.get("empty") or "暂无数据。", style="dim")
        return
    console.print(f"{data.get('title') or 'ID'}（{len(values)}）", style="bold")
    for value in values:
        console.print(f"  - {value}")


def _render_message(data: dict[str, Any]) -> None:
    message = data.get("message") or "操作成功。"
    _console().print(message)


def _render_generic(data: Any) -> None:
    if isinstance(data, dict):
        _details(
            "结果",
            [
                (str(key), value)
                for key, value in data.items()
                if key not in {"details", "parameters"}
            ],
        )
        return
    if isinstance(data, list):
        console = _console()
        if not data:
            console.print("暂无数据。", style="dim")
            return
        for item in data:
            console.print(f"  - {_text(item)}")
        return
    _console().print(_text(data, empty="null"))
