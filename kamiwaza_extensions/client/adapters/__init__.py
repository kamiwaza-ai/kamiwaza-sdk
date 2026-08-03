"""CLI and SDK adapters for the object-storage client."""

from kamiwaza_extensions.client.adapters.base import BaseAdapter
from kamiwaza_extensions.client.adapters.boto3 import Boto3Adapter
from kamiwaza_extensions.client.auth.credentials import Credentials

__all__ = ["BaseAdapter", "Boto3Adapter", "Credentials"]
