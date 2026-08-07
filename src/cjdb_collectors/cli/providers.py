from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime
import json
import os
from pathlib import Path
import sys

import typer

from .common import get_services, parse_values
from .logs import provider_log_path, show_log_file
from .output import (
    CLIResult,
    OutputFormat,
    cli_result,
    format_option,
    output_command,
)
from cjdb_collectors.services.logger import LogType

app = typer.Typer(
    no_args_is_help=True,
    help="列出 Provider 实现，并为服务选择和配置 Provider。",
)


class _Tee:
    def __init__(self, *streams) -> None:
        self._streams = streams

    def write(self, value: str) -> int:
        for stream in self._streams:
            stream.write(value)
        return len(value)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()

PROVIDER_TYPE_LABELS = {
    "douyin_aweme_collect": "抖音数据采集",
    "xiaohongshu_aweme_collect": "小红书数据采集",
    "wechat_channels_aweme_collect": "视频号数据采集",
    "wechat_mp_aweme_collect": "公众号数据采集",
    "douyin_comment_collect": "抖音评论采集",
    "xiaohongshu_comment_collect": "小红书评论下载",
    "wechat_channels_comment_collect": "视频号评论采集",
    "wechat_mp_comment_collect": "公众号评论采集",
    "douyin_account_collect": "抖音账号采集",
    "xiaohongshu_account_collect": "小红书账号采集",
    "wechat_channels_account_collect": "视频号账号采集",
    "wechat_mp_account_collect": "公众号账号采集",
    "video_transcription": "视频文字转写",
}


def _provider_summary(provider: dict | None) -> dict | None:
    if not provider:
        return None
    return {
        "name": provider.get("name"),
        "namespace": provider.get("namespace"),
    }


def _provider_list_result(catalog: dict) -> CLIResult:
    implementations: dict[str, dict] = {}
    for provider in catalog.get("providers", []):
        namespace = str(provider["namespace"])
        provider_type = str(provider["type"])
        implementation = implementations.setdefault(
            namespace,
            {
                "name": provider["name"],
                "namespace": namespace,
                "supported_types": [],
            },
        )
        supported_types = implementation["supported_types"]
        capability = {
            "type": provider_type,
            "label": PROVIDER_TYPE_LABELS.get(provider_type, provider_type),
        }
        if capability not in supported_types:
            supported_types.append(capability)

    providers = list(implementations.values())
    value = {
        "providers": providers,
        "count": len(providers),
    }
    return cli_result(
        catalog,
        view="provider_list",
        text_value=value,
        json_value=value,
    )


def _provider_status_result(value: dict) -> CLIResult:
    services = value.get("services") if "services" in value else [value]
    compact = [
        {
            "type": item["type"],
            "label": item.get("label")
            or PROVIDER_TYPE_LABELS.get(item["type"], item["type"]),
            "selected": item.get("selected"),
            "provider": _provider_summary(item.get("provider")),
            "status": item["status"],
            "ready": item.get("ready", item.get("status") == "ready"),
            "message": item.get("message"),
            "details": item.get("details", {}),
            "checked_at": item.get("checked_at"),
            "setup_pid": item.get("setup_pid"),
        }
        for item in services
    ]
    result = {"services": compact} if "services" in value else compact[0]
    return cli_result(
        value,
        view="provider_status",
        text_value=result,
        json_value=result,
    )


def _provider_selection_result(catalog: dict) -> CLIResult:
    selected_namespace = catalog.get("selected")
    provider = next(
        (
            item
            for item in catalog.get("providers", [])
            if item.get("namespace") == selected_namespace
        ),
        None,
    )
    provider_type = str(catalog.get("type") or "")
    value = {
        "type": provider_type,
        "label": PROVIDER_TYPE_LABELS.get(provider_type, provider_type),
        "selected": selected_namespace,
        "provider": _provider_summary(provider),
    }
    return cli_result(
        catalog,
        view="provider_selection",
        text_value=value,
        json_value=value,
    )


def _provider_setup_result(value: dict) -> CLIResult:
    return cli_result(
        value,
        view="provider_setup",
        text_value=value,
        json_value=value,
    )


def _provider_setup_command_result(value: dict) -> CLIResult:
    return cli_result(
        value,
        view="generic",
        text_value=value,
        json_value=value,
    )


def _provider_setup_requirements_result(value: dict) -> CLIResult:
    return cli_result(
        value,
        view="provider_setup_requirements",
        text_value=value,
        json_value=value,
    )


def _format_setup_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _setup_requirements(services, provider_type: str) -> dict[str, object]:
    providers = getattr(services, "providers", services)
    namespace = providers.selected_namespace(provider_type)
    catalog = providers.catalog(
        provider_type,
        include_status=False,
        include_setup_payload=True,
    )
    provider_item = next(
        (
            item
            for item in catalog.get("providers", [])
            if item.get("namespace") == namespace
        ),
        None,
    )
    setup_payload = (provider_item or {}).get("setup_payload", {})
    parameters = (provider_item or {}).get("parameters", [])
    selected_type = catalog.get("type") or provider_type
    return {
        "type": selected_type,
        "label": PROVIDER_TYPE_LABELS.get(str(selected_type), str(selected_type)),
        "provider": _provider_summary(provider_item),
        "parameters": parameters,
        "configured_parameters": {
            parameter["key"]: parameter["key"] in setup_payload
            and setup_payload.get(parameter["key"]) not in (None, "")
            for parameter in parameters
        },
        "example": " ".join(
            [
                "cjdb",
                "provider",
                "setup",
                provider_type,
                *[
                    (
                        f"{parameter['key']}="
                        f"{_format_setup_value(parameter['default'])}"
                    )
                    for parameter in parameters
                    if parameter.get("default") not in (None, "")
                ],
            ]
        ),
    }


@app.command("list", help="列出所有可选的 Provider 实现。")
@output_command
def list_items(
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    catalog = get_services().providers.catalog(
        include_status=False,
    )
    return _provider_list_result(catalog)


@app.command("status", help="查看服务类型当前所选 Provider 的动态状态。")
@output_command
def status(
    provider_type: str | None = typer.Argument(None, metavar="[TYPE]"),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="忽略缓存并重新检查 Provider 状态。",
    ),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    value = (
        services.providers.status(provider_type, refresh=refresh)
        if provider_type
        else services.providers.service_status(refresh=refresh)
    )
    return _provider_status_result(value)


@app.command("select", help="为服务类型选择一个 Provider 实现。")
@output_command
def select(
    provider_type: str = typer.Argument(..., metavar="TYPE"),
    namespace: str = typer.Argument(..., metavar="NAMESPACE"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return _provider_selection_result(
        get_services().providers.select(provider_type, namespace)
    )


def _is_setup_parameter_error(error: Exception) -> bool:
    message = str(error)
    return (
        "unknown provider parameters" in message
        or "provider parameter is required" in message
    )


def _setup_parameter_error_message(services, provider_type: str, error: Exception) -> str:
    requirements = _provider_setup_requirements_result(
        _setup_requirements(services, provider_type)
    )
    return f"{error}\n\n{requirements.text}"


@app.command(
    "setup",
    help="配置 Provider；使用 --stop 停止正在运行的 setup。",
)
@output_command
def setup(
    provider_type: str,
    values: list[str] = typer.Argument([], metavar="[KEY=VALUE]..."),
    stop: bool = typer.Option(False, "--stop", help="停止正在运行的 setup。"),
    values_file: Path | None = typer.Option(None, "--values-file"),
    unlink_values_file: bool = typer.Option(
        False,
        "--unlink-values-file",
        hidden=True,
    ),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    if stop:
        if values or values_file is not None:
            raise typer.BadParameter("--stop 不能同时传入 setup 参数")
        return _provider_setup_command_result(
            services.providers.stop_setup(provider_type)
        )

    namespace = services.providers.selected_namespace(provider_type)
    log_path = services.logger.get_log_path(LogType.PROVIDER_SETUP, namespace)
    with services.logger.open_text_append(log_path) as handle:
        handle.write(
            f"{datetime.now().astimezone().isoformat()} "
            f"开始设置 {provider_type} ({namespace})\n"
        )
    with services.logger.open_text_append(log_path) as handle:
        try:
            stderr = (
                sys.stderr
                if os.environ.get("CJDB_PROVIDER_SETUP_OUTPUT_REDIRECTED") == "1"
                else _Tee(sys.stderr, handle)
            )
            with redirect_stderr(stderr):
                result = services.providers.setup(
                    provider_type,
                    parse_values(
                        values,
                        values_file=values_file,
                        unlink_values_file=unlink_values_file,
                    ),
                )
        except Exception as exc:
            if _is_setup_parameter_error(exc):
                handle.write(f"设置失败：{exc}\n")
                raise typer.BadParameter(
                    _setup_parameter_error_message(services, provider_type, exc)
                ) from exc
            handle.write(f"设置失败：{exc}\n")
            raise
    with services.logger.open_text_append(log_path) as handle:
        if not result.get("success"):
            handle.write(
                f"设置失败：{result.get('message') or 'Provider 初始化失败'}\n"
            )
    return _provider_setup_result(result)


@app.command("logs")
@output_command
def logs(
    provider_type: str,
    follow: bool = typer.Option(False, "-f", "--follow"),
    lines: int = typer.Option(100, "-n", "--lines", min=0),
    timestamps: bool = typer.Option(False, "-t", "--timestamps"),
    output_format: OutputFormat = format_option(),
) -> CLIResult | None:
    namespace = get_services().providers.selected_namespace(provider_type)
    return show_log_file(
        provider_log_path(namespace),
        follow=follow,
        lines=lines,
        timestamps=timestamps,
        output_format=output_format,
    )
