"""Custom HTTP exceptions for Kamiwaza auth.

These exceptions provide consistent error responses across all
App Garden applications using Kamiwaza authentication.
"""

from fastapi import HTTPException


class SessionExpiredError(HTTPException):
    """Error when user's Kamiwaza session/token is invalid.

    Returns 401 status with a structured error response that
    frontends can detect and handle appropriately.

    Example:
        if not identity.user_id:
            raise SessionExpiredError()

        # Response:
        # {
        #     "detail": {
        #         "error": "session_expired",
        #         "message": "Your session has expired. Please log in again."
        #     }
        # }
    """

    def __init__(
        self, message: str = "Your session has expired. Please log in again."
    ) -> None:
        super().__init__(
            status_code=401,
            detail={"error": "session_expired", "message": message},
        )


class UpstreamAuthError(HTTPException):
    """Error when external service returns auth failure.

    Returns 502 status to indicate a gateway error, with details
    about which service failed.

    Example:
        try:
            response = await client.get(external_api_url, headers=headers)
            if response.status_code == 401:
                raise UpstreamAuthError(
                    message="External API rejected credentials",
                    service="external-api"
                )
        except UpstreamAuthError:
            raise

        # Response:
        # {
        #     "detail": {
        #         "error": "upstream_auth_failed",
        #         "message": "External API rejected credentials",
        #         "service": "external-api"
        #     }
        # }
    """

    def __init__(
        self,
        message: str = "Authentication failed with external service.",
        service: str | None = None,
    ) -> None:
        detail: dict[str, str] = {"error": "upstream_auth_failed", "message": message}
        if service:
            detail["service"] = service
        super().__init__(status_code=502, detail=detail)
