from __future__ import annotations

from pathlib import Path
import os
from uuid import UUID

from cjdb_collectors.settings import load_settings
from cjdb_collectors.db import assert_database_current, create_db_engine
from cjdb_collectors.models import WorkerTask
from cjdb_collectors.services import _default_session_factory, build_services
from cjdb_collectors.services.execution import ExecutionService
from cjdb_collectors.services.logger import LoggerService, LogType


def run_worker_task(worker_task_id: str, config_path: str) -> None:
    if hasattr(os, "setsid"):
        try:
            os.setsid()
        except PermissionError:
            # The scheduler already starts task processes with start_new_session=True.
            # Some managed environments reject a second setsid() call.
            pass
    settings = load_settings(Path(config_path), force_reload=True)
    LoggerService.configure(settings=settings)
    assert_database_current(settings)
    engine = create_db_engine(settings.app.database_path)
    sessions = _default_session_factory(engine)
    with sessions() as session:
        worker_task = session.get(WorkerTask, UUID(worker_task_id))
        task_type = worker_task.task_type.value if worker_task else "unknown"
    task_logger = LoggerService.get_logger(LogType.WORKER_TASKS, task_type)
    task_logger.info("Worker task 启动：id=%s type=%s", worker_task_id, task_type)
    services = build_services(settings, sessions, db_engine=engine)
    try:
        ExecutionService(sessions, services).run(UUID(worker_task_id))
        task_logger.info("Worker task 完成：id=%s", worker_task_id)
    except Exception:
        task_logger.exception("Worker task 失败：id=%s", worker_task_id)
        raise
