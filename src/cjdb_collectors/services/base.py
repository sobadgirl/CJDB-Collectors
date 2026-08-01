from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import UUID

from sqlmodel import Session

from cjdb_collectors.exceptions import (
    ConflictError as ConflictError,
    InvalidOperationError,
    NotFoundError as NotFoundError,
    ServiceError as ServiceError,
)

SessionFactory = Callable[[], AbstractContextManager[Session]]
ModelT = TypeVar("ModelT")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise InvalidOperationError(f"invalid UUID: {value}") from exc


def apply_changes(instance: Any, changes: dict[str, Any], allowed: set[str]) -> Any:
    unknown = set(changes) - allowed
    if unknown:
        raise InvalidOperationError(
            f"fields cannot be changed: {', '.join(sorted(unknown))}"
        )
    for key, value in changes.items():
        setattr(instance, key, value)
    if hasattr(instance, "updated_at"):
        instance.updated_at = now_utc()
    return instance
