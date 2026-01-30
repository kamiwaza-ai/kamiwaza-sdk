# kamiwaza_sdk/__init__.py
from importlib.metadata import version, PackageNotFoundError

from .client import KamiwazaClient

# Export as kamiwaza_sdk for the import pattern: from kamiwaza_sdk import KamiwazaClient as kz
kamiwaza_sdk = KamiwazaClient

try:
    __version__ = version("kamiwaza-sdk")
except PackageNotFoundError:
    __version__ = "0.0.0"  # Fallback for editable installs without metadata
