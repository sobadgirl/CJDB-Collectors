"""Transport-level error mapping.

Business services may raise the lightweight exceptions below.  Keeping the
mapping here lets services stay independent from FastAPI.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApplicationError(Exception):
    status_code = 400
    code = "application_error"

    def __init__(self, message: str, *, details: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ResourceNotFound(ApplicationError):
    status_code = 404
    code = "not_found"


class ResourceConflict(ApplicationError):
    status_code = 409
    code = "conflict"


class ServiceUnavailable(ApplicationError):
    status_code = 503
    code = "service_unavailable"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def application_error_handler(
        request: Request, exc: ApplicationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    # Services deliberately do not import FastAPI.  Register their common base
    # exception here so every concrete service error gets the same envelope.
    from cjdb_collectors.services import ServiceError

    @app.exception_handler(ServiceError)
    async def service_error_handler(
        request: Request, exc: ServiceError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "details": None,
                }
            },
        )
