"""Unified FastAPI route entrypoint."""

from fastapi import APIRouter

from .api import api_router
from .pages import pages_router
from .system import system_router

app_router = APIRouter()
app_router.include_router(api_router)
app_router.include_router(system_router)
app_router.include_router(pages_router)

__all__ = ["app_router"]
