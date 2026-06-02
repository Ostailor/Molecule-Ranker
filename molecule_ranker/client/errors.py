from __future__ import annotations

from typing import Any


class MoleculeRankerClientError(Exception):
    """Base class for SDK errors."""


class MoleculeRankerAPIError(MoleculeRankerClientError):
    """Raised when the molecule-ranker API returns a non-2xx response."""

    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        request_id: str | None = None,
        error_code: str | None = None,
        details: Any | None = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.request_id = request_id
        self.error_code = error_code
        self.details = details
        suffix = f" request_id={request_id}" if request_id else ""
        code = f" {error_code}" if error_code else ""
        super().__init__(f"{status_code}{code}: {message}{suffix}")


class AuthenticationError(MoleculeRankerAPIError):
    """Raised for authentication failures."""


class PermissionDeniedError(MoleculeRankerAPIError):
    """Raised for authorization failures."""


class NotFoundError(MoleculeRankerAPIError):
    """Raised when a requested resource is missing."""


class ValidationError(MoleculeRankerAPIError):
    """Raised for API validation errors."""

