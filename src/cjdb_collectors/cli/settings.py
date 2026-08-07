from __future__ import annotations

from pathlib import Path

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
            raise typer.BadParameter(f"unknown settings path: {key}")
        value = value[part]
    return value


@app.command("init")
@output_command
def init(
    path: Path | None = typer.Option(
        None,
        "--path",
        help="配置文件路径；默认使用 CJDB_CONFIG 或项目根目录的 config.yaml。",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="覆盖已有配置文件。",
    ),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    from cjdb_collectors.settings import init_settings_file

    try:
        config_path = init_settings_file(path, force=force)
    except FileExistsError as exc:
        raise typer.BadParameter(
            f"{exc} Use `./cjdb settings init --force` to overwrite it."
        ) from exc
    return cli_result(
        {
            "created": True,
            "config_path": config_path,
            "message": f"已创建默认配置文件：{config_path}",
        },
        view="message",
    )


@app.command("show")
@output_command
def show(output_format: OutputFormat = format_option()) -> CLIResult:
    return cli_result(
        get_services().settings.show(),
        view="settings",
    )


@app.command("get")
@output_command
def get(
    key: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    settings = get_services().settings
    value = _value_at(settings.show(), key)
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
    settings = get_services().settings
    updated = settings.set(key, value)
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
    from cjdb_collectors.settings import load_settings

    settings = load_settings(force_reload=True)
    return cli_result(
        {
            "valid": True,
            "config_path": settings.config_path,
            "message": f"配置有效：{settings.config_path}",
        },
        view="message",
    )
