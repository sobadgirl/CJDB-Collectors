from __future__ import annotations

from pathlib import Path
import os
from uuid import UUID

from cjdb_collectors.config import load_settings
from cjdb_collectors.db import create_db_engine
from cjdb_collectors.services import _default_session_factory, build_services
from cjdb_collectors.services.execution import ExecutionService


def run_worker_task(worker_task_id: str, config_path: str) -> None:
    if hasattr(os, "setsid"):
        os.setsid()
    settings = load_settings(Path(config_path), force_reload=True)
    engine = create_db_engine(settings.app.database_path)
    sessions = _default_session_factory(engine)
    services = build_services(settings, sessions, db_engine=engine)
    ExecutionService(sessions, services).run(UUID(worker_task_id))
