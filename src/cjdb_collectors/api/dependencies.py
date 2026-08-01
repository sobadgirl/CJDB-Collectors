"""FastAPI dependencies shared by API and web routes."""

from typing import Any

from fastapi import Request


def get_services(request: Request) -> Any:
    """Return the application service container installed during app startup."""
    return request.app.state.services
