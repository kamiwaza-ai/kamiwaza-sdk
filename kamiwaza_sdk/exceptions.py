

class KamiwazaError(Exception):
    """Base exception for Kamiwaza SDK errors."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class APIError(KamiwazaError):
    """Raised when the API returns an error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str | None = None,
        response_data: object | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.response_data = response_data


class AuthenticationError(KamiwazaError):
    """Raised when authentication fails."""


class AuthorizationError(KamiwazaError):
    """Raised when the caller lacks permission for an operation."""


class NotFoundError(KamiwazaError):
    """Raised when a requested resource is not found."""


class DatasetNotFoundError(NotFoundError):
    """Raised when a catalog dataset cannot be located."""


class ValidationError(KamiwazaError):
    """Raised when input validation fails."""


class TimeoutError(KamiwazaError):
    """Raised when a request times out."""


class NonAPIResponseError(KamiwazaError):
    """Raised when the server returns a non-API response (e.g., HTML dashboard)."""

    def __init__(self, message: str | None = None):
        default_msg = "Non-API response received. Did you forget to append '/api' to your base URL?"
        super().__init__(message or default_msg)


class TransportNotSupportedError(KamiwazaError):
    """Raised when a retrieval transport cannot satisfy the request."""


class VectorDBUnavailableError(APIError):
    """Raised when the VectorDB service reports no backend is configured."""
