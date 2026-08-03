"""Base types for authentication."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Credentials:
    """Credentials tuple for adapters."""

    access_key_id: str
    secret_access_key: str
    session_token: str | None
    expiry: datetime | None


class CredentialProvider(ABC):
    """Abstract base for credential providers."""

    @abstractmethod
    def can_provide(self) -> bool:
        """Return True if this provider can supply credentials."""
        ...

    @abstractmethod
    def get_credentials(self) -> Credentials | None:
        """Return credentials or None."""
        ...
