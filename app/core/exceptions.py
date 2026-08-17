from __future__ import annotations


class AppException(Exception):
    status_code: int = 500
    detail: str = "Internal server error"

    def __init__(self, detail: str | None = None) -> None:
        if detail:
            self.detail = detail
        super().__init__(self.detail)


class OAuthException(AppException):
    status_code = 400

    def __init__(self, detail: str = "OAuth error occurred") -> None:
        super().__init__(detail)


class LinkedInAPIException(AppException):
    status_code = 502

    def __init__(self, detail: str = "LinkedIn API error") -> None:
        super().__init__(detail)


class DatabaseException(AppException):
    status_code = 500

    def __init__(self, detail: str = "Database error") -> None:
        super().__init__(detail)


class ValidationException(AppException):
    status_code = 422

    def __init__(self, detail: str = "Validation error") -> None:
        super().__init__(detail)


class SchedulerException(AppException):
    status_code = 500

    def __init__(self, detail: str = "Scheduler error") -> None:
        super().__init__(detail)


class NotFoundException(AppException):
    status_code = 404

    def __init__(self, detail: str = "Resource not found") -> None:
        super().__init__(detail)


class ConflictException(AppException):
    """The resource exists but is in a state that forbids the operation.

    Distinct from ValidationException on purpose: the request was well formed,
    it just arrived too late — editing a post that has already gone out, for
    instance. A 422 would suggest the caller should fix the payload and retry.
    """

    status_code = 409

    def __init__(self, detail: str = "Conflicting state") -> None:
        super().__init__(detail)
