from __future__ import annotations

from typing import Any


class CJDBError(RuntimeError):
    status_code = 400
    code = "cjdb_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        data: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or type(self).code
        self.data = dict(data or {})
        self.status_code = status_code or type(self).status_code


class ServiceError(CJDBError):
    status_code = 400
    code = "service_error"


class NotFoundError(ServiceError):
    status_code = 404
    code = "not_found"


class ConflictError(ServiceError):
    status_code = 409
    code = "conflict"


class InvalidOperationError(ServiceError):
    status_code = 422
    code = "invalid_operation"
