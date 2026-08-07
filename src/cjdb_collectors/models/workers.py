from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from .base import enum_column, utc_now
from .enums import TaskStatus, WorkerSubject, WorkerTaskStatus, WorkerTaskType


class WorkerTask(SQLModel, table=True):
    __tablename__ = "worker_tasks"
    __table_args__ = (
        UniqueConstraint("task_type", "subject_id", name="uq_worker_tasks_claim"),
        Index("ix_worker_tasks_timeout", "status", "timeout_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    task_type: WorkerTaskType = Field(
        sa_column=enum_column(WorkerTaskType, index=True)
    )
    subject_type: WorkerSubject = Field(sa_column=enum_column(WorkerSubject, index=True))
    subject_id: UUID = Field(index=True)
    pid: int | None = Field(default=None, index=True)
    process_group_id: int | None = None
    process_started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    run_token: str = Field(index=True, max_length=64)
    status: WorkerTaskStatus = Field(
        default=WorkerTaskStatus.STARTING,
        sa_column=enum_column(WorkerTaskStatus, index=True),
    )
    started_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    heartbeat_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    timeout_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )


__all__ = [
    "TaskStatus",
    "WorkerSubject",
    "WorkerTask",
    "WorkerTaskStatus",
    "WorkerTaskType",
]
