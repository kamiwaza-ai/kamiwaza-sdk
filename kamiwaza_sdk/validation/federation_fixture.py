"""Small, source-local fixture data for the shared-IdP scenario.

Keeping this data in the SDK provider makes direct invocation independent of
the Kamiwaza source tree.  The server-side gate package remains configurable;
the provider uses this same known-answer inventory when checking returned rows.
"""

from __future__ import annotations

from typing import Any

DEFAULT_TENANT_ID = "__default__"
GATE_CLASSPATH = "acme_gates.mini_access_tier_gate.MiniAccessTierGate"
GATE_NAME = "mini_access_tier_gate"
GATE_PACKAGE_NAME = "acme-gates"
GATE_PACKAGE_SPEC = "acme-gates==1.1.0"

PERSONAS = {
    "PUBLIC": "fed-tier-public",
    "PRIVATE": "fed-tier-private",
    "CONFIDENTIAL": "fed-tier-confidential",
}
UNONBOARDED_PERSONA = "fed-tier-unonboarded"
TENANT_NEGATIVE_PERSONAS: dict[str, tuple[str, dict[str, str]]] = {
    "missing-canonical": ("fed-tenant-missing", {"access_tier": "PUBLIC"}),
    "legacy-only": (
        "fed-tenant-legacy-only",
        {"access_tier": "PUBLIC", "tenant": DEFAULT_TENANT_ID},
    ),
    "canonical-nondefault": (
        "fed-tenant-nondefault",
        {"access_tier": "PUBLIC", "tenant_id": "tenant-a"},
    ),
}

KNOWN: dict[str, tuple[int, set[str]]] = {
    "PUBLIC": (3, {"PUBLIC"}),
    "PRIVATE": (4, {"PUBLIC", "PRIVATE"}),
    "CONFIDENTIAL": (5, {"PUBLIC", "PRIVATE", "CONFIDENTIAL"}),
}


def records() -> tuple[dict[str, Any], ...]:
    """Return the deterministic five-row access_tier fixture."""

    return (
        {"id": "r1", "tier": "PUBLIC", "payload": "public-alpha"},
        {"id": "r2", "tier": "PUBLIC", "payload": "public-bravo"},
        {"id": "r3", "tier": "PUBLIC", "payload": "public-charlie"},
        {"id": "r4", "tier": "PRIVATE", "payload": "private-delta"},
        {"id": "r5", "tier": "CONFIDENTIAL", "payload": "confidential-echo"},
    )
