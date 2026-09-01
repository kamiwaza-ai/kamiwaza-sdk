"""Platform gate protocol imports for the SDK-owned fixture package.

The wheel is loaded inside a Kamiwaza runtime, where the platform protocol is
available.  The SDK repository also passes changed Python files directly to
pytest during its pre-push gate; keep source collection importable there
without pretending the platform protocol is installed on the SDK host.
"""

from __future__ import annotations

from typing import Any

try:
    from kamiwaza.services.authz.gates.protocol import (
        AttributeGate,
        AttributeSpec,
        AuthorizationDecision,
        AuthzOutcome,
        ExecutionGate,
        GateAuditEntry,
        GateResult,
    )
except ModuleNotFoundError as exc:
    if exc.name != "kamiwaza":
        raise

    class _UnavailableProtocol:
        """Import-only stand-in; real gates require the platform runtime."""

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(
                "acme-gates fixture protocols require a Kamiwaza runtime"
            )

    AttributeGate = _UnavailableProtocol
    AttributeSpec = _UnavailableProtocol
    AuthorizationDecision = _UnavailableProtocol
    ExecutionGate = _UnavailableProtocol
    GateAuditEntry = _UnavailableProtocol
    GateResult = _UnavailableProtocol

    class AuthzOutcome:
        ALLOW = "allow"
        DENY = "deny"
        UNKNOWN = "unknown"


__all__ = [
    "AttributeGate",
    "AttributeSpec",
    "AuthorizationDecision",
    "AuthzOutcome",
    "ExecutionGate",
    "GateAuditEntry",
    "GateResult",
]
