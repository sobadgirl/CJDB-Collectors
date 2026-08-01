from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from cjdb_collectors.api.dependencies import get_services

Services = Annotated[Any, Depends(get_services)]

system_router = APIRouter(prefix="/api/v1/system", tags=["system"])


@system_router.get("/health")
def health(services: Services) -> Any:
    return services.health.ready()


@system_router.get("/checks")
def checks(services: Services) -> Any:
    return {
        "services": services.health.services(),
        "worker": services.worker_tasks.health(),
    }
