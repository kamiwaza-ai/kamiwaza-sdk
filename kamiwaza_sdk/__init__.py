# kamiwaza_sdk/__init__.py
from importlib.metadata import version, PackageNotFoundError

from .client import KamiwazaClient
from .job_runtime import JobRuntimeClient as JobRuntimeClient
from .schemas.delegated_jobs import (
    DatasetDelegatedAccess as DatasetDelegatedAccess,
    DelegatedAccess as DelegatedAccess,
    ModelDelegatedAccess as ModelDelegatedAccess,
)
from .shared_idp_authentication import (
    SharedIdpAuthConfig as SharedIdpAuthConfig,
    SharedIdpAuthenticator as SharedIdpAuthenticator,
)

# Export as kamiwaza_sdk for the import pattern: from kamiwaza_sdk import KamiwazaClient as kz
kamiwaza_sdk = KamiwazaClient

try:
    __version__ = version("kamiwaza-sdk")
except PackageNotFoundError:
    __version__ = "0.0.0"  # Fallback for editable installs without metadata
