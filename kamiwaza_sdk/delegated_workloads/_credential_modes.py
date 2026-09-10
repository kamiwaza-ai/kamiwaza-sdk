"""Internal closed credential-mode dispatch for trusted SDK adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from kamiwaza_sdk.delegated_workloads.errors import DelegatedIdentityError

_ResultT = TypeVar("_ResultT")
_SUPPORTED_MODES = frozenset(("brokered", "ephemeral_token"))


class UnsupportedCredentialMode(DelegatedIdentityError):
    """A mode outside the version-one credential contract was requested."""

    def __init__(self) -> None:
        super().__init__("unsupported_mode")


def resolve_credential_mode(
    mode: object,
    resolver: Callable[[str], _ResultT],
) -> _ResultT:
    """Reject an unknown mode before transport or provider resolution."""
    if not isinstance(mode, str) or mode not in _SUPPORTED_MODES:
        raise UnsupportedCredentialMode
    return resolver(mode)
