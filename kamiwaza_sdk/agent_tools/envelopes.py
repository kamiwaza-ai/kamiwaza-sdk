"""Success and failure payloads for agent-facing calls.

One success shape, one failure shape, five failure kinds, produced in exactly
one place so they cannot drift apart. Per the MCP server's FR-008 and FR-039.

These are payloads, not protocol messages. The consuming server decides the
outer wire shape its protocol revision requires and puts one of these inside;
nothing here imports a protocol library or names a wire field, so the same
payloads serve a custom agent with no MCP in sight.

The kinds exist because the correct next step differs for each, and collapsing
any two produces a specific bad behaviour:

* an entitlement refusal reported as a platform fault makes an agent retry a
  call that will never succeed;
* a platform fault reported as an entitlement refusal makes it give up on one
  that would have worked;
* an expired wait reported as a plain failure produces a second deployment
  beside the first, which is the most expensive mistake this module prevents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "FailureKind",
    "Failure",
    "Success",
    "entitlement_refusal",
    "expired_wait",
    "platform_fault",
    "rejected_input",
    "unmet_prerequisite",
]


class FailureKind(str, Enum):
    """Why a call did not produce data.

    Five kinds, never collapsed and never substituted. An empty result is not a
    failure: a query that found nothing succeeded, and reporting it as a refusal
    sends an agent looking for permissions it already has.
    """

    REJECTED_INPUT = "rejected_input"
    ENTITLEMENT_REFUSAL = "entitlement_refusal"
    UNMET_PREREQUISITE = "unmet_prerequisite"
    EXPIRED_WAIT = "expired_wait"
    PLATFORM_FAULT = "platform_fault"

    @property
    def retryable(self) -> bool:
        """Whether retrying the identical call could ever succeed.

        An entitlement refusal is not retryable: the boundary does not move
        because an agent asked twice. An expired wait is not retryable either —
        it is *resumable*, which is a different action, because retrying creates
        a duplicate of whatever already exists.
        """
        return self in (FailureKind.UNMET_PREREQUISITE, FailureKind.PLATFORM_FAULT)


@dataclass(frozen=True, slots=True)
class Success:
    """A completed call's result.

    Carries the result under one key and nothing else. A caller that has to
    inspect the envelope to learn whether the call succeeded has been given two
    contracts instead of one.

    Attributes:
        data: The operation's result. Permissive by design: a field the platform
            adds later must not break a working integration, which is why only
            arguments are validated strictly (FR-007).
    """

    data: Any

    def as_payload(self) -> dict[str, Any]:
        """Return the payload as a plain mapping for a transport to carry."""
        return {"data": self.data}


@dataclass(frozen=True, slots=True)
class Failure:
    """A call that did not produce data, and what to do about it.

    Attributes:
        message: Written for an agent to act on. Never a stack trace, and never
            a raw platform error passed through unread.
        kind: Which of the five kinds this is.
        status: Status reported by the layer that failed.
        code: Stable machine-readable code for this failure.
        timeout_ms: The budget that applied, so an expired wait is
            interpretable rather than mysterious.
        source: Which layer produced the failure.
        request_id: Ties this failure to its call record.
        resource_id: Identifier of the resource involved, when the underlying
            error carries one. Required for an expired wait, because resuming
            without it is impossible and creating a second resource is the
            failure mode.
        detail: Kind-specific facts — the offending field, what would grant
            access, what was missing.
    """

    message: str
    kind: FailureKind
    status: str
    code: str
    timeout_ms: int
    source: str
    request_id: str
    resource_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject a failure that cannot be acted on.

        Raises:
            ValueError: If an expired wait carries no resource identifier. The
                only correct response to an expired wait is to resume what
                already exists, and an agent cannot resume what it cannot name.
        """
        if self.kind is FailureKind.EXPIRED_WAIT and not self.resource_id:
            raise ValueError(
                "an expired_wait failure must carry the resource_id of what "
                "already exists, otherwise the caller can only create a duplicate"
            )
        if not self.message.strip():
            raise ValueError("a failure must carry a message an agent can act on")

    @property
    def retryable(self) -> bool:
        """Whether retrying the identical call could succeed."""
        return self.kind.retryable

    def as_payload(self) -> dict[str, Any]:
        """Return the payload as a plain mapping for a transport to carry.

        Optional fields are omitted rather than sent as null, so a consumer can
        test presence without distinguishing "absent" from "explicitly nothing".
        """
        payload: dict[str, Any] = {
            "message": self.message,
            "kind": self.kind.value,
            "status": self.status,
            "code": self.code,
            "timeout_ms": self.timeout_ms,
            "source": self.source,
            "request_id": self.request_id,
        }
        if self.resource_id is not None:
            payload["resource_id"] = self.resource_id
        if self.detail:
            payload["detail"] = self.detail
        return payload


def rejected_input(
    *,
    message: str,
    field_name: str,
    status: str,
    code: str,
    timeout_ms: int,
    source: str,
    request_id: str,
) -> Failure:
    """Build a failure for arguments that did not validate.

    Args:
        message: What was wrong, phrased so an agent can correct it.
        field_name: The offending field. Required: "invalid arguments" without a
            field name leaves an agent guessing which one to change.
        status: Status from the validating layer.
        code: Stable machine-readable code.
        timeout_ms: The budget that applied.
        source: Which layer rejected the input.
        request_id: Call record identifier.

    Returns:
        The failure, with the field name in ``detail``.
    """
    return Failure(
        message=message,
        kind=FailureKind.REJECTED_INPUT,
        status=status,
        code=code,
        timeout_ms=timeout_ms,
        source=source,
        request_id=request_id,
        detail={"field": field_name},
    )


def entitlement_refusal(
    *,
    message: str,
    required: str,
    status: str,
    code: str,
    timeout_ms: int,
    source: str,
    request_id: str,
    resource_id: str | None = None,
) -> Failure:
    """Build a failure for a caller who may not perform the operation.

    Args:
        message: The boundary, stated plainly.
        required: What would grant access — a scope, a grant, an approval.
            Required, because a refusal an agent cannot report is a refusal it
            will work around.
        status: Status from the authorizing layer.
        code: Stable machine-readable code.
        timeout_ms: The budget that applied.
        source: Which layer refused.
        request_id: Call record identifier.
        resource_id: The resource access was refused to, when there is one.

    Returns:
        The failure, with what would grant access in ``detail``.
    """
    return Failure(
        message=message,
        kind=FailureKind.ENTITLEMENT_REFUSAL,
        status=status,
        code=code,
        timeout_ms=timeout_ms,
        source=source,
        request_id=request_id,
        resource_id=resource_id,
        detail={"required": required},
    )


def unmet_prerequisite(
    *,
    message: str,
    missing: str,
    status: str,
    code: str,
    timeout_ms: int,
    source: str,
    request_id: str,
    resource_id: str | None = None,
) -> Failure:
    """Build a failure for something required that was absent.

    Args:
        message: What could not proceed and why.
        missing: What was absent, so an agent can satisfy it rather than retry
            the same call unchanged.
        status: Status from the failing layer.
        code: Stable machine-readable code.
        timeout_ms: The budget that applied.
        source: Which layer failed.
        request_id: Call record identifier.
        resource_id: The resource involved, when there is one.

    Returns:
        The failure, with the missing prerequisite in ``detail``.
    """
    return Failure(
        message=message,
        kind=FailureKind.UNMET_PREREQUISITE,
        status=status,
        code=code,
        timeout_ms=timeout_ms,
        source=source,
        request_id=request_id,
        resource_id=resource_id,
        detail={"missing": missing},
    )


def expired_wait(
    *,
    message: str,
    resource_id: str,
    status: str,
    code: str,
    timeout_ms: int,
    source: str,
    request_id: str,
    resume_with: str | None = None,
) -> Failure:
    """Build a failure for work still converging when the budget ran out.

    The resource identifier is a required argument rather than an optional one:
    this failure means "it may exist and still be settling", and the only
    correct response is to resume it.

    Args:
        message: What was waited on and for how long.
        resource_id: Identifier of what already exists.
        status: Status from the waiting layer.
        code: Stable machine-readable code.
        timeout_ms: The budget that expired.
        source: Which layer timed out.
        request_id: Call record identifier.
        resume_with: The operation to call to resume, when one is known.

    Returns:
        The failure, carrying the identifier and the resume hint.
    """
    detail: dict[str, Any] = {"resumable": True}
    if resume_with is not None:
        detail["resume_with"] = resume_with
    return Failure(
        message=message,
        kind=FailureKind.EXPIRED_WAIT,
        status=status,
        code=code,
        timeout_ms=timeout_ms,
        source=source,
        request_id=request_id,
        resource_id=resource_id,
        detail=detail,
    )


def platform_fault(
    *,
    message: str,
    status: str,
    code: str,
    timeout_ms: int,
    source: str,
    request_id: str,
    resource_id: str | None = None,
    idempotent: bool = False,
) -> Failure:
    """Build a failure for a platform-side error.

    Args:
        message: What the platform reported, rewritten for an agent to act on.
        status: Status the platform returned.
        code: Stable machine-readable code.
        timeout_ms: The budget that applied.
        source: Which layer surfaced the fault.
        request_id: Call record identifier.
        resource_id: The resource involved, when there is one.
        idempotent: Whether the attempted operation is idempotent. Carried
            because "retry only if idempotent" is advice an agent cannot follow
            without knowing which it called.

    Returns:
        The failure, with the retry-safety fact in ``detail``.
    """
    return Failure(
        message=message,
        kind=FailureKind.PLATFORM_FAULT,
        status=status,
        code=code,
        timeout_ms=timeout_ms,
        source=source,
        request_id=request_id,
        resource_id=resource_id,
        detail={"safe_to_retry": idempotent},
    )
