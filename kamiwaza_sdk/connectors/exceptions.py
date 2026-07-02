"""Custom exceptions for connectors service."""


class ConnectorException(Exception):
    """Base exception for connector service."""

    pass


class ConnectorNotFoundException(ConnectorException):
    """Connector not found."""

    pass


class ConnectionNotFoundException(ConnectorException):
    """User connection not found."""

    pass


class ConnectorDisabledException(ConnectorException):
    """Connector is disabled."""

    pass


class ConnectorSurfaceUnavailableException(ConnectorException):
    """A connector surface/op has no deployed implementation yet.

    Raised when a surface (e.g. mail or calendar) is not served by the deployed
    connector, instead of silently falling back to a now-deleted client.
    """

    pass


class TokenEncryptionException(ConnectorException):
    """Token encryption/decryption error."""

    pass


class DeviceCodeExpiredException(ConnectorException):
    """Device code flow expired."""

    pass


class DeviceCodePendingException(ConnectorException):
    """Device code flow still pending user authentication."""

    pass


class DeviceCodeErrorException(ConnectorException):
    """Device code flow error."""

    pass


class ProviderAPIException(ConnectorException):
    """External provider API error carrying an upstream HTTP status code."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TokenRefreshException(ConnectorException):
    """Token refresh failed."""

    pass


class TransientTokenRefreshException(TokenRefreshException):
    """Token refresh failed transiently (network error / upstream 5xx /
    eventually-consistent secret read).

    The stored credentials are still valid — the caller should retry rather than
    force the user to reconnect. Subclasses TokenRefreshException so existing
    handlers still treat it as a refresh failure, while callers that care about
    recoverability can catch it specifically.
    """

    pass


class InvalidConfigException(ConnectorException):
    """Invalid connector configuration."""

    pass


class RateLimitException(ConnectorException):
    """Rate limit exceeded."""

    def __init__(self, message: str, retry_after: int = 5):
        super().__init__(message)
        self.retry_after = retry_after


class ContentTooLargeException(ConnectorException):
    """File content exceeds maximum download size."""

    def __init__(self, message: str, file_size: int, max_size: int):
        super().__init__(message)
        self.file_size = file_size
        self.max_size = max_size


class OAuthException(ConnectorException):
    """Base exception for OAuth flows."""

    pass


class OAuthConfigException(OAuthException):
    """OAuth configuration error."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class OAuthStateException(OAuthException):
    """OAuth state validation error."""

    pass


class OAuthTokenExchangeException(OAuthException):
    """OAuth token exchange failed."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OAuthUserInfoException(OAuthException):
    """OAuth userinfo retrieval failed."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OAuthRequestException(OAuthException):
    """OAuth request blocked or invalid."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OAuthAccessDeniedException(OAuthException):
    """OAuth access denied by user or provider policy."""

    pass


class RemoteConnectorException(ConnectorException):
    """A call to a deployed connector failed (transport or non-2xx)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
