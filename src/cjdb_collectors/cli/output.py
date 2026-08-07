from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum, StrEnum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, ParamSpec
from uuid import UUID

import typer

from .presentation import render_text


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class CLIResult:
    text: str
    json: Any


_UNSET = object()
P = ParamSpec("P")


def format_option() -> Any:
    return typer.Option(
        OutputFormat.TEXT,
        "--format",
        help="输出格式：text（人类阅读）或 json（程序读取）。",
        case_sensitive=False,
        show_default=True,
    )


def serializable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return serializable(asdict(value))
    if isinstance(value, dict):
        return {str(key): serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serializable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (UUID, Path, datetime, date)):
        return str(value)
    return value


def cli_result(
    value: Any,
    *,
    view: str = "generic",
    text_value: Any = _UNSET,
    json_value: Any = _UNSET,
) -> CLIResult:
    default_data = serializable(value)
    selected_text = (
        default_data if text_value is _UNSET else serializable(text_value)
    )
    selected_json = (
        default_data if json_value is _UNSET else serializable(json_value)
    )
    return CLIResult(
        text=render_text(selected_text, view),
        json=selected_json,
    )


def emit_result(
    result: CLIResult,
    output_format: OutputFormat,
) -> None:
    if output_format == OutputFormat.JSON:
        typer.echo(
            json.dumps(
                serializable(result.json),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    typer.echo(result.text)


def output_command(
    function: Callable[P, CLIResult | None],
) -> Callable[P, None]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            from cjdb_collectors.settings import SettingsFileNotFoundError

            if not isinstance(exc, SettingsFileNotFoundError):
                raise
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
        if result is None:
            return
        output_format = kwargs.get("output_format", OutputFormat.TEXT)
        emit_result(result, OutputFormat(output_format))

    return wrapped


__all__ = [
    "CLIResult",
    "OutputFormat",
    "cli_result",
    "emit_result",
    "format_option",
    "output_command",
    "serializable",
]
