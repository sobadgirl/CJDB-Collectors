class ServiceError(RuntimeError):
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
