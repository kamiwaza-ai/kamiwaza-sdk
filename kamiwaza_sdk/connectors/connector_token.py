"""Schemas for connector-scoped ephemeral credential minting.

The request a subscribed connector sends to mint a short-lived,
scope-restricted credential for a subject it acts for, and the response carrying
that credential. The connector authenticates as its own workload identity (see the
route); the acting subject is named here.

The acting subject is established from a *verified* token: the connector presents
the acting user's ``subject_token`` and core's verification of it (its ``sub``) is
authoritative. ``subject_id`` is a self-asserted fallback honored only under an
explicit env gate; ``subject_type`` is an audit hint. Neither is a trust input. The
credential source is chosen by the connector's admin-declared auth model
(``PerUserOAuth`` vs ``ServiceToken``). See design.md "Principal-typed subjects".
"""

from pydantic import BaseModel, ConfigDict, Field


class ConnectorMintRequest(BaseModel):
    """Request to mint a credential for a connector's acting subject."""

    model_config = ConfigDict(from_attributes=True)

    subject_token: str | None = Field(
        None,
        repr=False,
        description="Acting user's bearer token; its verified `sub` is authoritative",
    )
    subject_id: str | None = Field(
        None,
        description="Self-asserted subject; honored only under the env-gated PoC fallback",
    )
    subject_type: str | None = Field(
        None,
        description="Audit hint only (e.g. user / group / npe); NOT a trust input",
    )
    scope_subset: list[str] | None = Field(
        None,
        description="Optional subset of the subject's granted scopes (defaults to all)",
    )
    lease_duration: int = Field(
        300,
        ge=60,
        le=900,
        description="Broker lease TTL in seconds (1-15 min, default 5 min)",
    )


class ConnectorMintResponse(BaseModel):
    """Response carrying a short-lived provider access token."""

    model_config = ConfigDict(from_attributes=True)

    access_token: str = Field(
        ..., description="Provider access token (use directly against the provider API)"
    )
    lease_id: str = Field(..., description="Lease identifier for tracking / revocation")
    granted_scopes: list[str] = Field(
        ..., description="Scopes the minted token is restricted to"
    )
    expires_in: int = Field(
        ..., description="Provider token expiry in seconds (refreshed in core)"
    )
    broker_lease_expires_in: int = Field(
        ..., description="Broker lease expiry in seconds"
    )
    token_type: str = Field(default="Bearer", description="Token type")
