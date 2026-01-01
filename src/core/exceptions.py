"""
Custom exception classes.
"""
from typing import Any, Optional


class AppException(Exception):
    """Base application exception."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        detail: Optional[Any] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(self.message)


class AuthenticationError(AppException):
    """Authentication failed."""

    def __init__(self, message: str = "Authentication failed", detail: Optional[Any] = None):
        super().__init__(message, status_code=401, detail=detail)


class AuthorizationError(AppException):
    """Authorization/permission denied."""

    def __init__(self, message: str = "Permission denied", detail: Optional[Any] = None):
        super().__init__(message, status_code=403, detail=detail)


class NotFoundError(AppException):
    """Resource not found."""

    def __init__(self, message: str = "Resource not found", detail: Optional[Any] = None):
        super().__init__(message, status_code=404, detail=detail)


class ValidationError(AppException):
    """Data validation error."""

    def __init__(self, message: str = "Validation error", detail: Optional[Any] = None):
        super().__init__(message, status_code=422, detail=detail)


class ConflictError(AppException):
    """Resource conflict (duplicate, etc.)."""

    def __init__(self, message: str = "Resource conflict", detail: Optional[Any] = None):
        super().__init__(message, status_code=409, detail=detail)


class BadRequestError(AppException):
    """Bad request error."""

    def __init__(self, message: str = "Bad request", detail: Optional[Any] = None):
        super().__init__(message, status_code=400, detail=detail)


class RateLimitError(AppException):
    """Rate limit exceeded."""

    def __init__(self, message: str = "Rate limit exceeded", detail: Optional[Any] = None):
        super().__init__(message, status_code=429, detail=detail)
