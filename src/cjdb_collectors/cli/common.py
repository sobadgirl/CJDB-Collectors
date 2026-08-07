from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer


def get_services():
    from cjdb_collectors.settings import load_settings
    from cjdb_collectors.db import migrate_database
    from cjdb_collectors.services import build_services

    settings = load_settings()
    migrate_database(settings)
    return build_services(settings=settings)


def parse_values(
    values: list[str],
    *,
    values_file: Path | None = None,
    unlink_values_file: bool = False,
) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    if values_file:
        loaded = json.loads(values_file.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise typer.BadParameter("--values-file must contain a JSON object")
        parsed.update(loaded)
        if unlink_values_file:
            values_file.unlink(missing_ok=True)
    for item in values:
        key, separator, raw_value = item.partition("=")
        if not separator or not key:
            raise typer.BadParameter("values must use KEY=VALUE")
        try:
            parsed[key] = json.loads(raw_value)
        except json.JSONDecodeError:
            parsed[key] = raw_value
    return parsed


def page_offset(page: int, size: int) -> int:
    return (page - 1) * size
