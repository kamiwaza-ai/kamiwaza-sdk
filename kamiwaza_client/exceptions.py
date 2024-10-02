class KamiwazaError(Exception):
    """Base exception for Kamiwaza SDK"""

class APIError(KamiwazaError):
    """Raised when the API returns an error"""

class AuthenticationError(KamiwazaError):
    """Raised when authentication fails"""

class NotFoundError(KamiwazaError):
    """Raised when a requested resource is not found"""

class ValidationError(KamiwazaError):
    """Raised when input validation fails"""