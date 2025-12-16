"""
Compatibility alias so legacy code can still import ``kamiwaza_client``.

The canonical package name is ``kamiwaza_sdk`` (install via ``pip install kamiwaza-sdk``),
but older docs referenced ``kamiwaza_client``. Importing this module re-exports the real
SDK package and emits a deprecation warning so callers can migrate at their own pace.
"""
from __future__ import annotations

import importlib
import sys
import warnings
from typing import TYPE_CHECKING

warnings.warn(
    "`kamiwaza_client` is deprecated; please import `kamiwaza_sdk` instead.",
    DeprecationWarning,
    stacklevel=2,
)

_sdk = importlib.import_module("kamiwaza_sdk")

if TYPE_CHECKING:  # pragma: no cover - helps static analyzers
    from kamiwaza_sdk import KamiwazaClient, __version__  # noqa: F401

# Replace this module entry so ``import kamiwaza_client`` returns the real SDK package.
sys.modules[__name__] = _sdk
