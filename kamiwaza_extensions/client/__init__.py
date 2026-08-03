"""Canonical SDK client for S3-compatible object storage.

The public API supports boto3 clients and resources, static credentials,
bucket-scoped broker authentication, and lazy multi-bucket access.
"""

from kamiwaza_extensions.client.auth.credentials import Credentials
from kamiwaza_extensions.client.auth.provider import CredentialProvider
from kamiwaza_extensions.client.client import get_client, get_resource

__all__ = ["Credentials", "CredentialProvider", "get_client", "get_resource"]
