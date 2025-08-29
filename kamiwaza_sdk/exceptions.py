

class KamiwazaError(Exception):
    """Base exception for Kamiwaza SDK"""
    def __init__(self, message):
        self.message = message

class APIError(KamiwazaError):
    """Raised when the API returns an error"""
    def __init__(self, message):
        super().__init__(message)

class AuthenticationError(KamiwazaError):
    """Raised when authentication fails"""

class NotFoundError(KamiwazaError):
    """Raised when a requested resource is not found"""

class ValidationError(KamiwazaError):
    """Raised when input validation fails"""

class TimeoutError(KamiwazaError):
    """Raised when a request times out"""

class NonAPIResponseError(KamiwazaError):
    """Raised when the server returns a non-API response (e.g., HTML dashboard)"""
    def __init__(self, message=None):
        default_msg = "Non-API response received. Did you forget to append '/api' to your base URL?"
        super().__init__(message or default_msg)