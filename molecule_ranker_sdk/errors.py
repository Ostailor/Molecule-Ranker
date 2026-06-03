from __future__ import annotations

from typing import Any


class MoleculeRankerSDKError(Exception):
    """Base SDK exception with response context safe for logs and support bundles."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        error_code: str | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.error_code = error_code
        self.response_body = response_body


class APIError(MoleculeRankerSDKError):
    """Unexpected non-success API response."""


class AuthenticationError(APIError):
    """Authentication failed or no valid bearer token was supplied."""


class PermissionDeniedError(APIError):
    """The authenticated principal lacks the required permission."""


class NotFoundError(APIError):
    """The requested resource does not exist or is not visible to this caller."""


class ConflictError(APIError):
    """The request conflicts with current server state."""


class ValidationError(APIError):
    """The request failed server-side validation."""


class RetryExhaustedError(MoleculeRankerSDKError):
    """A retryable request failed after the configured retry budget."""
