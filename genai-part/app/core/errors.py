class RoomSageError(Exception):
    """Base error. Routes turn these into the API's error envelope."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(RoomSageError):
    status_code = 404
    code = "not_found"


class ValidationFailed(RoomSageError):
    status_code = 422
    code = "invalid_request"


class ConflictError(RoomSageError):
    status_code = 409
    code = "conflict"


class UpstreamError(RoomSageError):
    """Person A's service failed or could not be reached."""

    status_code = 502
    code = "upstream_error"


class UnavailableError(RoomSageError):
    status_code = 503
    code = "unavailable"
