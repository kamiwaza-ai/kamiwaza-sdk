"""Act-approval request and response models on the SDK surface.

An act that needs organisational approval is approved by a human on the
platform's console, not through an MCP client capability: the caller opens an
approval, a second person decides it, and the caller comes back with an opaque
reference to run the act once. These models are the four route bodies and the
two response shapes of that path.

All models opt into ``extra="allow"`` for forward compatibility per
``.ai/knowledge/failures/common-pitfalls.md`` — pinned-wheel customers must not
break when the server adds fields.

Server-side correlate lives at
``kamiwaza/services/console/orchestration_schemas.py``; the two are kept in
sync by code review because they cross a repository boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


#: Which dispatch path the approved act runs on.
ActKind = Literal["operation", "workflow"]

#: Lifecycle of one approval. ``consumed`` is terminal: an approval runs its
#: act once, so a second use of the same approval is refused.
ActApprovalStatus = Literal["pending", "approved", "denied", "expired", "consumed"]

#: What a human decided about the act.
ActDecision = Literal["approved", "denied"]


class ActApprovalCreate(BaseModel):
    """Request body for POST /api/act-approvals.

    ``act_digest`` binds the approval to one exact act. The resolver sends the
    digest it was shown, and the platform refuses the decision when that digest
    is no longer the stored one, so an act cannot be edited after a human read
    it.
    """

    model_config = ConfigDict(extra="allow")

    act_kind: ActKind = Field(
        ...,
        description="Which dispatch path the act runs: an operation or a workflow.",
    )
    act_target: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Published operation or workflow name the act calls.",
    )
    act_digest: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description=(
            "SHA-256 over the canonical act, as 64 hex characters. Minted by "
            "the MCP server and presented back on resolve, which is what binds "
            "a decision to the act the approver read."
        ),
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="The act described in the words the approver reads.",
    )
    act_payload: Dict[str, Any] = Field(
        ...,
        description="The act's arguments, so the approver sees what will happen.",
    )
    reason: Optional[str] = Field(
        default=None,
        max_length=300,
        description="Why the caller wants the act. Optional.",
    )
    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description=(
            "Caller-chosen key. The service salts it per tenant, as it does for "
            "access requests, so a retry reopens the same approval."
        ),
    )
    ttl_seconds: Optional[int] = Field(
        default=None,
        description=(
            "Lifetime of the approval. The platform defaults to 7 days and "
            "refuses more than 30 days."
        ),
    )


class ActApprovalResolve(BaseModel):
    """Request body for POST /api/act-approvals/{id}/resolve.

    ``presented_digest`` is the digest the resolver was shown. The platform
    answers 409 ``act_approval.digest_stale`` when it differs from the stored
    ``act_digest``.
    """

    model_config = ConfigDict(extra="allow")

    decision: ActDecision = Field(
        ..., description="What the human decided: approved or denied."
    )
    presented_digest: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description=(
            "The act digest shown to the resolver, as 64 hex characters. "
            "Rechecked against the stored digest before the decision is kept."
        ),
    )


class ActApprovalView(BaseModel):
    """Response model for GET and for POST .../consume.

    Readable by the requester and by the approver set. ``resolver_id`` and
    ``resolved_at`` record who decided and when, which is the evidence an
    in-client approval cannot produce.
    """

    model_config = ConfigDict(extra="allow")

    id: UUID = Field(..., description="Identifier of the approval.")
    status: ActApprovalStatus = Field(..., description="Lifecycle state.")
    act_kind: ActKind = Field(..., description="Dispatch path of the act.")
    act_target: str = Field(..., description="Operation or workflow the act calls.")
    act_digest: str = Field(..., description="SHA-256 over the canonical act.")
    title: str = Field(..., description="The act as the approver reads it.")
    act_payload: Dict[str, Any] = Field(
        default_factory=dict, description="Arguments the act will run with."
    )
    requester_id: str = Field(..., description="Who opened the approval.")
    created_at: datetime = Field(..., description="When the approval was opened.")
    expires_at: Optional[datetime] = Field(
        default=None,
        description="When a pending approval stops being decidable.",
    )
    resolved_at: Optional[datetime] = Field(
        default=None, description="When a human decided; null while pending."
    )
    resolver_id: Optional[str] = Field(
        default=None,
        description=(
            "Who decided. Always a different member from the requester, "
            "because the platform refuses self-approval."
        ),
    )
    consumed_at: Optional[datetime] = Field(
        default=None,
        description="When the approval was spent on running the act.",
    )


class OperationAccepted(BaseModel):
    """Response model for POST /api/act-approvals and .../resolve.

    Both routes record their work as a console operation, so the caller reads
    the operation feed rather than the write's own return value.
    """

    model_config = ConfigDict(extra="allow")

    id: UUID = Field(..., description="Identifier of the recorded operation.")
    kind: str = Field(..., description="Operation kind, e.g. 'act_approval'.")
    status: str = Field(..., description="Operation status at the time of the reply.")
    origin: str = Field(..., description="Surface the operation was started from.")
    title: str = Field(..., description="The operation described for a reader.")
    steps: List[Dict[str, Any]] = Field(
        default_factory=list, description="Steps recorded for the operation."
    )
    resource_refs: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Resources the operation names, including the approval.",
    )
    created_at: datetime = Field(..., description="When the operation was recorded.")
