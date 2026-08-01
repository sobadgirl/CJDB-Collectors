from __future__ import annotations

import typer

from .common import get_services
from .output import (
    CLIResult,
    OutputFormat,
    cli_result,
    format_option,
    output_command,
)

app = typer.Typer(no_args_is_help=True, help="查看和修改运行配置。")


def _value_at(data: dict, key: str):
    value = data
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise typer.BadParameter(f"unknown config path: {key}")
        value = value[part]
    return value


@app.command("show")
@output_command
def show(output_format: OutputFormat = format_option()) -> CLIResult:
    return cli_result(
        get_services().config.show(),
        view="settings",
    )


@app.command("get")
@output_command
def get(
    key: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    config = get_services().config
    value = _value_at(config.show(), key)
    return cli_result(
        {"key": key, "value": value},
        view="setting",
    )


@app.command("set")
@output_command
def set_value(
    key: str,
    value: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    config = get_services().config
    updated = config.set(key, value)
    return cli_result(
        {
            "key": key,
            "value": _value_at(updated, key),
            "updated": True,
        },
        view="setting",
    )


@app.command("validate")
@output_command
def validate(output_format: OutputFormat = format_option()) -> CLIResult:
    from cjdb_collectors.config import load_settings

    settings = load_settings(force_reload=True)
    return cli_result(
        {
            "valid": True,
            "config_path": settings.config_path,
            "message": f"配置有效：{settings.config_path}",
        },
        view="message",
    )
