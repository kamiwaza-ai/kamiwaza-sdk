"""One decision point for what a mesh call's outcome means (ENG-9664).

Three live suites each grew their own ad-hoc classifier for "did the receiver
admit this mesh call". They disagreed, and the receiver_realm one could not go
red at all: it read ``getattr(exc, "status_code", None)``, but a 401 arrives as
``AuthenticationError``, which is a SIBLING of ``APIError`` and therefore carries
no ``status_code``. The classifier saw ``None``, matched no branch, and fell
through to a terminal ``pytest.skip``. A total mesh-data-plane outage
(ENG-9663) reported green through it on every run.

The lesson encoded here: **a skip is an assertion**. It must state a falsifiable
precondition, and it must be able to tell "not applicable to this suite" apart
from "broken". Where it cannot, it is an untested path wearing a green badge.

Two axes decide the verdict, and they are properties of the SUITE, not of the
error:

``identity_arranged``
    This suite arranged a credential it expects the receiver to accept. A 401
    then means the credential was refused -> FAIL. A walkthrough that pairs but
    enrols no guest has arranged nothing, so a 401 is the documented shape of
    "no credential to present" -> SKIP.

``admission_is_the_assertion``
    The assertion is that the caller was ADMITTED at ingress; per-resource authz
    beyond that is out of scope. A plain 403/404 is then the positive signal
    (admitted, then declined on the merits) -> PASS. Suites asserting something
    downstream treat it as an unmet precondition -> SKIP.

An auth-layer 403 is never PASS: those reasons are the receiver refusing the
credential itself, which is the same failure a 401 is, wearing a different code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import pytest

from kamiwaza_sdk.exceptions import AuthenticationError, KamiwazaError

PASS = "pass"
SKIP = "skip"
FAIL = "fail"

# Reasons the RECEIVER emits when it refuses the credential itself. A 403
# carrying one of these is an auth-layer rejection, not a per-resource authz
# decision, so it can never be the positive "admitted" signal.
#
# Deliberately EXCLUDED: unauthorized_brokered_user / revoked_brokered_user.
# Those are the documented "this walkthrough never allowlisted anyone"
# precondition, and folding them in here would flip a legitimate shared_idp
# skip into a failure.
AUTH_LAYER_REASONS = frozenset(
    {
        "peer_jwt_required",
        "peer_jwt_validation_failed",
        "peer_jwks",
        "shared_idp_issuer_untrusted",
        "identity_mode_unsupported",
        "federation_not_active",
        "brokering_required_for_receiver_controlled",
        "receiver_realm_misconfigured",
        "receiver_realm_client_missing",
        "receiver_realm_kc_unreachable",
        "receiver_realm_sub_missing",
        "unauthorized_guest",
        "revoked_guest",
        "invalid_signature",
    }
)


@dataclass(frozen=True)
class MeshPolicy:
    """What a given suite is actually asserting about a mesh call."""

    identity_arranged: bool
    admission_is_the_assertion: bool
    context: str


@dataclass(frozen=True)
class MeshFailure:
    """A mesh call's failure, reduced to what the verdict depends on."""

    is_401: bool
    status: Optional[int]
    reason: Optional[str]
    blob: str


def reason_of(exc: BaseException) -> Optional[str]:
    """Best-effort receiver ``reason`` from an SDK error.

    The receiver returns ``{"detail": {"reason": ...}}``; some layers flatten it
    to a string. Falls back to None so the caller keeps its own signals.
    """
    body = getattr(exc, "body", None) or getattr(exc, "response_data", None)
    if isinstance(body, dict):
        detail = body.get("detail", body)
        if isinstance(detail, dict):
            value = detail.get("reason")
            return str(value) if value is not None else None
    return None


def is_auth_layer(failure: MeshFailure) -> bool:
    """True when the receiver refused the CREDENTIAL rather than the request."""
    if failure.reason is not None:
        return failure.reason in AUTH_LAYER_REASONS
    # No structured reason: fall back to the substrings the earlier ad-hoc
    # classifiers keyed on, so behaviour is preserved where detail is a string.
    return any(marker in failure.blob for marker in ("peer_jwt", "signature"))


def classify(failure: MeshFailure, policy: MeshPolicy) -> str:
    """PASS / SKIP / FAIL for one failed mesh call under one suite's policy."""
    if failure.is_401:
        # The bug this module exists for: a 401 carries no status_code, so a
        # status-based classifier cannot see it at all.
        return FAIL if policy.identity_arranged else SKIP
    if failure.status in (403, 404):
        if is_auth_layer(failure):
            return FAIL
        return PASS if policy.admission_is_the_assertion else SKIP
    # 5xx, transport errors, anything unrecognised: never silently tolerated.
    return FAIL


def _failure_from(exc: KamiwazaError) -> MeshFailure:
    # is_401 keys off the TYPE, never the status: AuthenticationError is a
    # sibling of APIError and has no status_code. Keying on status is exactly
    # the defect ENG-9664 records.
    return MeshFailure(
        is_401=isinstance(exc, AuthenticationError),
        status=getattr(exc, "status_code", None),
        reason=reason_of(exc),
        blob=repr(exc).lower(),
    )


def _message(failure: MeshFailure, policy: MeshPolicy, verdict: str) -> str:
    shape = "401" if failure.is_401 else f"status={failure.status}"
    reason = f" reason={failure.reason}" if failure.reason else ""
    return f"[{policy.context}] mesh call {verdict}: {shape}{reason}"


def mesh_call(call: Callable[[], Any], policy: MeshPolicy) -> Optional[Any]:
    """Run ``call``; return its result, skip, or fail per ``policy``.

    Returns None when the outcome was PASS-without-a-value (admitted, then
    declined downstream) so callers can guard their assertions on it.
    """
    try:
        return call()
    except KamiwazaError as exc:
        failure = _failure_from(exc)
        verdict = classify(failure, policy)
        if verdict == PASS:
            return None
        if verdict == SKIP:
            pytest.skip(_message(failure, policy, "skipped") + f": {exc!r}")
        pytest.fail(_message(failure, policy, "FAILED") + f": {exc!r}")
