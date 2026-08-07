from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def display_count(value: int | str | None) -> str:
    if value is None:
        return "-"
    try:
        count = int(value)
    except (TypeError, ValueError):
        return str(value)
    if count < 10000:
        return str(count)
    value_in_wan = count / 10000
    if round(value_in_wan, 2) < 10:
        return f"{value_in_wan:.2f}万"
    return f"{value_in_wan:.1f}万"


def display_gender(value: int | str | None) -> str:
    if value is None or value == "":
        return "-"
    normalized = str(value).strip().lower()
    if normalized in {"1", "male", "m", "man", "男", "男性"}:
        return "男"
    if normalized in {"2", "female", "f", "woman", "女", "女性"}:
        return "女"
    if normalized in {"0", "unknown", "未知", "保密", "secret"}:
        return "-"
    return str(value)


def display_location(location: str | None, ip_location: str | None) -> str:
    values = [value for value in (location, ip_location) if value]
    if not values:
        return "-"
    return " · ".join(dict.fromkeys(values))


def display_date(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date().isoformat()
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "-"
        if stripped.isdigit():
            return display_date(int(stripped))
        try:
            return datetime.fromisoformat(stripped.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return stripped
    return str(value)


def display_registered_at(extra_data: dict[str, Any] | None) -> str:
    if not isinstance(extra_data, dict):
        return "-"
    for key in (
        "registered_at",
        "register_time",
        "registration_time",
        "account_created_at",
        "account_create_time",
        "created_at",
        "create_time",
    ):
        if key in extra_data:
            return display_date(extra_data.get(key))
    return "-"
