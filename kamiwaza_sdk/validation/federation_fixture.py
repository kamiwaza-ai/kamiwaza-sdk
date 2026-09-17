"""Small, source-local fixture data for the shared-IdP scenario.

Keeping this data in the SDK provider makes direct invocation independent of
the Kamiwaza source tree.  The server-side gate package remains configurable;
the provider uses this same known-answer inventory when checking returned rows.
"""

from __future__ import annotations

from typing import Any

DEFAULT_TENANT_ID = "__default__"
GATE_CLASSPATH = "acme_gates.access_tier_gate.AccessTierGate"
GATE_NAME = "access_tier_gate"
GATE_PACKAGE_NAME = "acme-gates"
GATE_PACKAGE_SPEC = "acme-gates==1.1.0"

PERSONAS = {
    "basic": "access-basic",
    "standard": "access-standard",
    "advanced": "access-advanced",
}
UNONBOARDED_PERSONA = "access-unonboarded"
TENANT_NEGATIVE_PERSONAS: dict[str, tuple[str, dict[str, str]]] = {
    "missing-canonical": ("tenant-missing", {"access_tier": "basic"}),
    "legacy-only": (
        "tenant-legacy-only",
        {"access_tier": "basic", "tenant": DEFAULT_TENANT_ID},
    ),
    "canonical-nondefault": (
        "tenant-nondefault",
        {"access_tier": "basic", "tenant_id": "tenant-a"},
    ),
}

KNOWN: dict[str, tuple[int, set[str]]] = {
    "basic": (3, {"basic"}),
    "standard": (4, {"basic", "standard"}),
    "advanced": (5, {"basic", "standard", "advanced"}),
}


def records() -> tuple[dict[str, Any], ...]:
    """Return the deterministic five-row access-tier fixture."""

    return (
        {"id": "r1", "required_tier": "basic", "payload": "alpha"},
        {"id": "r2", "required_tier": "basic", "payload": "bravo"},
        {"id": "r3", "required_tier": "basic", "payload": "charlie"},
        {"id": "r4", "required_tier": "standard", "payload": "delta"},
        {"id": "r5", "required_tier": "advanced", "payload": "echo"},
    )
