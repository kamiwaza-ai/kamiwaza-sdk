"""Abstract adapter interface for credential consumers.

Adapters consume Credentials from CredentialProvider and produce output
in their native format (boto3 client, rclone config, env vars, etc.).

To add a new adapter (e.g., rclone):
  1. Create CredentialProvider(config=..., **cred_kwargs)
  2. Call provider.get_credentials() to get Credentials
  3. Transform into your format (config file, env vars, etc.)
"""

from __future__ import annotations

from kamiwaza_extensions.client.auth.credentials import Credentials
from kamiwaza_extensions.client.auth.provider import CredentialProvider


class BaseAdapter:
    """Abstract base for CLI/SDK adapters.

    Each adapter uses a CredentialProvider and produces output
    in its native format (e.g., boto3 client, rclone config path).
    """

    def __init__(self, credential_provider: CredentialProvider) -> None:
        self._credential_provider = credential_provider

    def get_credentials(self) -> Credentials:
        """Return raw credentials. Override if adapter needs custom logic."""
        return self._credential_provider.get_credentials()
