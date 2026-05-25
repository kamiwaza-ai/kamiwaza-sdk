"""T7.12 / ENG-4766 — Gate-package Pydantic models on the SDK surface.

WS-M5 foundation task. Typed request/response shapes for
``kz.gates.packages.{install, replace, list, get, uninstall}`` typed
wrappers (T7.10) and corresponding server-side handlers (T7.2).

All models opt into ``extra="allow"`` for forward compatibility per
``.ai/knowledge/failures/common-pitfalls.md`` — pinned-wheel customers
must not break when the server adds fields.

Server-side correlate lives at
``kamiwaza/services/authz/gate_packages/schemas.py`` (mirrors these
shapes; the two are kept in sync by code review since they cross a repo
boundary).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


GatePackageStatus = Literal["active", "uninstalling", "failed"]


class GatePackageSpec(BaseModel):
    """Request body for POST /api/authz/gate-packages (install) and
    PUT /api/authz/gate-packages/{name} (replace).

    Per FR-89 + FR-89a, ``hash_digest`` is REQUIRED at MVP — server
    rejects requests without it via the
    ``GatePackageHashRequiredError`` typed exception.
    """

    model_config = ConfigDict(extra="allow")

    package_spec: str = Field(
        ...,
        description=(
            'Pip-installable spec, e.g. "acme-gates==1.2.3". Must include '
            "a pinned version — unpinned specs are rejected by the server."
        ),
    )
    hash_digest: str = Field(
        ...,
        description=(
            'SHA-256 digest of the wheel as published on the index, e.g. '
            '"sha256:abcd...". Server invokes pip with --require-hashes; '
            "mismatches surface as ``GatePackageHashMismatchError``."
        ),
    )
    index_url: Optional[str] = Field(
        default=None,
        description=(
            "Optional override for the pip index URL. Server enforces the "
            "chart-configured ``authz.gatePackages.indexUrl`` allowlist; "
            "client-supplied values outside the allowlist are rejected at "
            "the API layer before any pip subprocess fires."
        ),
    )


class GatePackageState(BaseModel):
    """Response model for GET (single) and PUT/POST (install/replace
    result), and an element of the list response.

    Mirrors ``DBGatePackage`` columns one-to-one.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., description="Package name; the PK on cluster_gate_packages.")
    package_spec: str = Field(..., description="Original pip spec, e.g. 'acme-gates==1.0.0'.")
    version: str = Field(..., description="Resolved version, e.g. '1.0.0'.")
    hash_digest: str = Field(..., description="SHA-256 of the installed wheel.")
    index_url: Optional[str] = Field(default=None, description="Pip index used at install.")
    installed_at: datetime = Field(..., description="Initial install timestamp.")
    installed_by: str = Field(..., description="Actor (Keycloak subject ID) at install.")
    last_replaced_at: Optional[datetime] = Field(
        default=None, description="Most recent PUT-replace timestamp; null until first replace."
    )
    status: GatePackageStatus = Field(default="active", description="Lifecycle state.")
    classpaths: List[str] = Field(
        default_factory=list,
        description="Fully-qualified gate classpaths discovered in the package.",
    )


class GatePackageList(BaseModel):
    """Response model for GET /api/authz/gate-packages (paginated)."""

    model_config = ConfigDict(extra="allow")

    items: List[GatePackageState] = Field(default_factory=list)
    total: int = Field(default=0)
    page: int = Field(default=1)
    per_page: int = Field(default=20)


class GatePackageInstallResult(BaseModel):
    """Response model for POST /api/authz/gate-packages.

    The install result is the freshly-created ``GatePackageState`` plus
    a server-side context block carrying audit-event provenance for the
    customer's audit logs.
    """

    model_config = ConfigDict(extra="allow")

    package: GatePackageState = Field(
        ..., description="The installed package's current state record."
    )
    install_duration_seconds: float = Field(
        ..., description="Wall-clock pip-install elapsed (server-measured)."
    )
    audit_event_id: Optional[str] = Field(
        default=None,
        description=(
            "Identifier of the emitted ``gate_package_lifecycle`` audit "
            "event; customers can cross-reference into their audit stream."
        ),
    )
