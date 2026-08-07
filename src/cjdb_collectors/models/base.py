from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enum_type(enum_class: type[Enum]) -> SAEnum:
    return SAEnum(
        enum_class,
        values_callable=lambda values: [item.value for item in values],
        native_enum=False,
        validate_strings=True,
    )


def enum_column(enum_class: type[Enum], *, index: bool = False) -> Column[Any]:
    return Column(
        enum_type(enum_class),
        nullable=False,
        index=index,
    )


class TimestampMixin(SQLModel):
    # Mixins must let SQLModel create a fresh Column for every concrete table.
    # Reusing a ``sa_column=Column(...)`` instance across subclasses makes
    # SQLAlchemy attach the same Column object to multiple tables.
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        sa_column_kwargs={"onupdate": utc_now},
    )
