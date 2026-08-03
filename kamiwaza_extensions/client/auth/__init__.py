"""Authentication resolution for the object-storage client."""

from kamiwaza_extensions.client.auth.chain import resolve_credentials
from kamiwaza_extensions.client.auth.credentials import Credentials
from kamiwaza_extensions.client.auth.provider import CredentialProvider
from kamiwaza_extensions.client.auth.static import StaticCredentialProvider

__all__ = [
    "Credentials",
    "CredentialProvider",
    "resolve_credentials",
    "StaticCredentialProvider",
]
