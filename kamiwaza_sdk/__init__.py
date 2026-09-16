"""Python SDK for the Kamiwaza platform.

Exposes :class:`~kamiwaza_sdk.client.KamiwazaClient` as the entry point to every
platform service, and ``kamiwaza_sdk.agent_tools`` as the descriptor layer that
publishes those services to AI agents.
"""

from importlib.metadata import version, PackageNotFoundError

from .client import KamiwazaClient
from .shared_idp_authentication import (
    SharedIdpAuthConfig as SharedIdpAuthConfig,
    SharedIdpAuthenticator as SharedIdpAuthenticator,
)

# Export as kamiwaza_sdk for the import pattern:
#     from kamiwaza_sdk import KamiwazaClient as kz
kamiwaza_sdk = KamiwazaClient

try:
    __version__ = version("kamiwaza-sdk")
except PackageNotFoundError:
    __version__ = "0.0.0"  # Fallback for editable installs without metadata
