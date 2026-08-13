"""Validate the delegated workload identity an extension manifest declares.

The declaration governs which authority an unattended workload may be granted,
so a malformed one is rejected here rather than forwarded. Silently dropping it
would be worse than failing: the extension would deploy, the operator would see
no declaration, and the workload would simply never be registered —
indistinguishable from an extension that asked for nothing.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_RESOURCE = "workload_identity_manifest.schema.json"


class WorkloadIdentityDeclarationError(ValueError):
    """A manifest declared delegated workload identity that cannot be honoured."""


def _schema() -> dict[str, Any]:
    resource = files("kamiwaza_extensions.validators").joinpath(SCHEMA_RESOURCE)
    return json.loads(resource.read_bytes())


def declaration_errors(declaration: Any) -> list[str]:
    """Return human-readable schema violations for a ``workload_identity`` block."""
    validator = Draft202012Validator(_schema())
    errors = validator.iter_errors({"workload_identity": declaration})
    return [
        f"workload_identity{''.join(f'[{part!r}]' for part in error.path)}: "
        f"{error.message}"
        for error in sorted(errors, key=lambda e: list(e.path))
    ]


def require_valid_declaration(declaration: Any) -> dict[str, Any]:
    """Return the declaration, raising when it cannot be honoured."""
    errors = declaration_errors(declaration)
    if errors:
        raise WorkloadIdentityDeclarationError(
            "kamiwaza.json declares delegated workload identity that is not "
            "valid, so it cannot be published: " + "; ".join(errors)
        )
    return dict(declaration)
