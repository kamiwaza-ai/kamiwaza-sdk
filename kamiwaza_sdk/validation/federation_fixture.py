"""Small, source-local fixture data for the shared-IdP scenario.

Keeping this data in the SDK provider makes direct invocation independent of
the Kamiwaza source tree.  The server-side gate package remains configurable;
the provider uses this same known-answer inventory when checking returned rows.
"""

from __future__ import annotations

from typing import Any

DEFAULT_TENANT_ID = "__default__"
GATE_CLASSPATH = "acme_gates.mini_clearance_gate.MiniClearanceGate"
GATE_NAME = "mini_clearance_gate"
GATE_PACKAGE_NAME = "acme-gates"
GATE_PACKAGE_SPEC = "acme-gates==1.1.0"

PERSONAS = {"U": "fed-clr-u", "S": "fed-clr-s", "TS": "fed-clr-ts"}
UNONBOARDED_PERSONA = "fed-clr-unonboarded"
TENANT_NEGATIVE_PERSONAS: dict[str, tuple[str, dict[str, str]]] = {
    "missing-canonical": ("fed-tenant-missing", {"clearance": "U"}),
    "legacy-only": (
        "fed-tenant-legacy-only",
        {"clearance": "U", "tenant": DEFAULT_TENANT_ID},
    ),
    "canonical-nondefault": (
        "fed-tenant-nondefault",
        {"clearance": "U", "tenant_id": "tenant-a"},
    ),
}

KNOWN: dict[str, tuple[int, set[str]]] = {
    "U": (3, {"U"}),
    "S": (4, {"U", "S"}),
    "TS": (5, {"U", "S", "TS"}),
}


def records() -> tuple[dict[str, Any], ...]:
    """Return the deterministic five-row clearance fixture."""

    return (
        {"id": "clearance-1", "classification": "U", "text": "public"},
        {"id": "clearance-2", "classification": "U", "text": "internal"},
        {"id": "clearance-3", "classification": "U", "text": "routine"},
        {"id": "clearance-4", "classification": "S", "text": "sensitive"},
        {"id": "clearance-5", "classification": "TS", "text": "restricted"},
    )
