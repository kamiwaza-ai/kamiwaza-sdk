"""Live SDK evidence for attributable ReBAC allow decisions (ENG-12434).

Goes beyond the grant/revoke loop in ``test_authz_live.py``: real attribute-bearing
subjects rather than synthetic ids, a non-empty ``reason`` on each asserted allow, a
policy-shaped ``detail`` on each asserted deny, and a distinct ``decision_id`` per
decision rather than per policy state.

What this test deliberately does NOT claim, each settled against the platform rather than
taken from the capability document. The supporting analysis is tracked under ENG-12296
with the capability owner rather than restated here, because this repository is public:

* **Attribution on denies.** An allow returns ``decision_id`` in the response body. A
  deny returns it in a response HEADER, and this SDK's error classes carry no headers, so
  it is unreachable from here. Closing that is an SDK change, filed separately.
* **The decision audit.** That history is populated elsewhere in the platform, not by
  this endpoint, so asserting the export would pin behaviour the endpoint lacks.
* **Attribute gates and clearance.** The deny here is relationship-shaped — the subject
  holds no tuple. The attributes exercise subject administration only. The gated
  retrieval leg is ``test_mini_clearance_gate_retrieval_live.py``.
* **The non-admin subject restriction.** A test asserting that refusal was written, and
  it passed for an unrelated reason — a fresh object denies every subject by default — so
  it was removed rather than repaired.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import suppress

import pytest

from kamiwaza_sdk.exceptions import KamiwazaError
from kamiwaza_sdk.schemas.authz import (
    CheckRequest,
    CheckResponse,
    ObjectModel,
    RelationshipObjectDelete,
    RelationshipTuple,
    RelationshipTupleDelete,
    SubjectModel,
)
from kamiwaza_sdk.schemas.federation import Subject

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

RELATION = "viewer"

# Covers a decision-cache expiry plus grant propagation. The server caches a decision
# per subject/relation/object for a short TTL whose default is 250ms, so this is ~12x it.
SETTLE_SECONDS = 3

# Subject-deletion visibility is a separate clock with NO measured basis: same order of
# magnitude, named separately so retuning one cannot silently retune the other.
SUBJECT_ABSENCE_SECONDS = 3

# Why the absence probe gave up, keyed by username. The probe must not raise, so it has
# nowhere else to put this.
_ABSENCE_OBSERVED: dict[str, str] = {}

# The detail the platform's check route returns on a policy deny. A wording change fails
# this test loudly, which is the safe direction.
POLICY_DENY_DETAIL = "access_denied"


def _skip_if_rebac_disabled(error: KamiwazaError) -> None:
    """Turn the deployment-level 404 into a skip, as the sibling module does.

    Applied at every ``check_access`` call site, so ReBAC-off yields one verdict rather
    than a skip from one call and a hard failure from the next. It does NOT cover the
    fixtures' subject and attribute administration, which runs first: on a host that gates
    those endpoints behind the same switch, setup fails instead — and a setup failure is
    terminal for the emitter, so an environmental fact would publish status=failed.
    """
    if error.status_code == 404 and "rebac_disabled" in str(error):
        pytest.skip("ReBAC is disabled on this deployment")


def _refusal_detail(error: KamiwazaError) -> str | None:
    """Extract a refusal's machine-readable detail, whichever shape it arrives in.

    Reads ``body``, not ``response_data``: the SDK sets ``response_data`` only on
    ``APIError`` itself, while a reason-coded refusal raises a *sibling* class carrying
    ``body`` alone. ``body`` is present on JSON refusals only, so the ``isinstance`` check
    returns None for a non-JSON or transport failure and the caller fails loudly.
    """
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        reason = detail.get("reason")
        return reason if isinstance(reason, str) else None
    return None


def _assert_policy_deny(error: KamiwazaError) -> None:
    """Assert a refusal is a ReBAC policy deny, not merely some 403.

    Without this, any 403 on the path — session expiry, a proxy, a rate limiter — would
    satisfy the assertion. Compared exactly rather than as a substring, because
    ``access_denied`` is also a standard OAuth2 code: a gateway or IdP refusal body
    containing it would pass a substring test.
    """
    assert error.status_code == 403, f"expected a 403 deny, got {error.status_code}"
    detail = _refusal_detail(error)
    assert (
        detail == POLICY_DENY_DETAIL
    ), f"a refusal was not a policy deny: detail={detail!r}, body={getattr(error, 'body', None)!r}"


def _wait_for_allow(client, request: CheckRequest) -> CheckResponse:
    """Poll until the grant is visible, past the short-lived decision cache.

    Exits on ``allow`` alone. It deliberately does **not** also require a
    ``decision_id``: gating the loop on the same fields the caller then asserts would
    make those assertions unfailable, and would misreport a missing id as "no allow
    ever arrived".
    """
    deadline = time.monotonic() + SETTLE_SECONDS
    last_observation = "no decision"
    while True:
        try:
            decision = client.authz.check_access(request)
        except KamiwazaError as error:
            _skip_if_rebac_disabled(error)
            if error.status_code != 403:
                raise
            last_observation = "HTTP 403"
        else:
            if decision.allow:
                return decision
            last_observation = "a decision with allow=False"
        if time.monotonic() >= deadline:
            pytest.fail(f"Expected an allow; last observed {last_observation}")
        time.sleep(0.1)


def _wait_for_deny(client, request: CheckRequest) -> None:
    """Poll until the revocation is visible as a policy deny."""
    deadline = time.monotonic() + SETTLE_SECONDS
    last_observation = "no decision"
    while True:
        try:
            decision = client.authz.check_access(request)
        except KamiwazaError as error:
            _skip_if_rebac_disabled(error)
            if error.status_code != 403:
                raise
            _assert_policy_deny(error)
            return
        else:
            # A deny is expressed as a 403, so ANY returned decision means the grant is
            # still in force — including the unexpected 200/allow=False shape.
            last_observation = f"a 200 response with allow={decision.allow}"
        if time.monotonic() >= deadline:
            pytest.fail(f"Expected a 403 deny; last observed {last_observation}")
        time.sleep(0.1)


def _wait_for_distinct_decision(
    client, request: CheckRequest, previous_id: str
) -> CheckResponse:
    """Obtain an allow whose ``decision_id`` differs from ``previous_id``.

    A decision id identifies a decision, not a policy state, so two checks must be
    separately attributable. This polls rather than asserting on the immediately next
    call because the decision cache can legitimately serve a repeat inside its TTL: the
    claim under test is that a distinct id is obtainable, not that every adjacent pair
    of calls differs.
    """
    # Deadline starts after the first inner poll, so the outer window is not already
    # spent. It does not fully de-nest the budgets — a later iteration's _wait_for_allow
    # still carries its own inside this one — which is tolerable only because the grant is
    # already visible, so that inner poll returns on its first request.
    deadline = None
    last_observation = "no second decision"
    while True:
        decision = _wait_for_allow(client, request)
        if deadline is None:
            deadline = time.monotonic() + SETTLE_SECONDS
        if decision.decision_id and decision.decision_id != previous_id:
            return decision
        # An EMPTY id is a malformed response; a repeated one is the cache still
        # serving. Collapsing them reproduces the misreport this module avoids elsewhere.
        if not decision.decision_id:
            last_observation = "the second allow carried an EMPTY decision_id"
        else:
            last_observation = (
                f"decision_id={decision.decision_id!r}, same as the first"
            )
        if time.monotonic() >= deadline:
            pytest.fail(
                f"Expected a separately attributable second decision; {last_observation}"
            )
        time.sleep(0.1)


def _absent_within_window(client, username: str) -> bool:
    """Poll for a deleted subject's absence; report it, never raise an outcome.

    Returns a bool and raises nothing, because the best-effort teardown sweep calls it:
    ``pytest.fail``/``skip`` raise ``Failed``/``Skipped``, which derive from
    ``BaseException``, so no ``suppress`` would contain them and one expiring poll would
    abort the sweep and leak every subject after it. The asserting wrapper is
    :func:`_wait_for_absent`, for the test body.
    """
    deadline = time.monotonic() + SUBJECT_ABSENCE_SECONDS
    observed = "still readable"
    while True:
        try:
            client.subjects.get(username)
        # Broad on purpose: anything the read can raise — including a response the SDK
        # cannot model — must not escape and abort the caller's sweep.
        except Exception as error:  # noqa: BLE001
            if getattr(error, "status_code", None) == 404:
                return True
            observed = (
                f"{type(error).__name__} {getattr(error, 'status_code', '')}".strip()
            )
        if time.monotonic() >= deadline:
            _ABSENCE_OBSERVED[username] = observed
            return False
        time.sleep(0.1)


def _wait_for_absent(client, username: str) -> None:
    """Assert a deleted subject reads back as 404, within a bounded window.

    Bounded rather than immediate: every other transition in this module is polled, and
    subject deletion is not documented as read-your-write. An immediate read that raced
    the delete would flake, and a flake here writes a FAILED evidence record rather than
    merely re-running.
    """
    if not _absent_within_window(client, username):
        pytest.fail(
            f"deleted subject {username} did not read back as 404; last observed "
            f"{_ABSENCE_OBSERVED.get(username, 'nothing')}"
        )


def _withdraw_tolerating_held_subjects(client, name: str) -> str | None:
    """Withdraw an attribute, absorbing a transient "subjects still hold values".

    That condition arrives in two shapes — a 409 refusal, and a success body reporting a
    non-zero ``subjects_holding_value`` — and both are retried on ONE deadline because
    both measure the same lagging state. A real orphan still fails past it.

    Retrying rather than failing at once matters because a failed teardown is folded into
    the emitted evidence record's status: failing on a single stale read would publish a
    status=failed compliance record for a capability the run may not have exercised.

    Returns the lifecycle state the DELETE reported (``""`` when the response carried
    none), or None when the attribute was already absent. The caller needs it because the
    lifecycle is linear — declared, deprecated, withdrawn — and this same DELETE advances
    it: if a retry ever lands on an already-``withdrawn`` attribute, a forced step after
    it would raise and publish a failed record.
    """
    deadline = time.monotonic() + SETTLE_SECONDS
    while True:
        holders = None
        try:
            state = client.cluster.withdraw_attribute(name)
        except KamiwazaError as error:
            # 404 means the declare never landed, so there is nothing to withdraw — the
            # cost of arranging cleanup before the create. Reported so the caller skips
            # the forced step rather than 404ing on it too.
            if error.status_code == 404:
                return None
            if error.status_code != 409 or time.monotonic() >= deadline:
                raise
        else:
            # Annotated ``Dict[str, Any]`` but the client returns ``None`` for a 204, so
            # the shape is checked before it is read.
            holders = (
                state.get("subjects_holding_value") if isinstance(state, dict) else None
            )
            if not holders:
                return state.get("state", "") if isinstance(state, dict) else ""
            if time.monotonic() >= deadline:
                # The unforced DELETE is the declared -> deprecated transition, so the
                # attribute is left at ``deprecated`` — named exactly, so anyone sweeping
                # the orphan looks for the right state.
                pytest.fail(
                    f"attribute {name} still held by {holders} subject(s) at teardown; "
                    "left at 'deprecated' rather than silently forcing withdrawal"
                )
        time.sleep(0.1)


@pytest.fixture
def declared_attribute(live_kamiwaza_client) -> Iterator[str]:
    """Declare a disposable attribute, because the realm vocabulary is closed.

    ``subjects.upsert`` refuses an undeclared name with 400
    ``attribute_not_registered``, and only ``declared``-state attributes accept new
    values. The name is unique per run so a failed teardown leaves one identifiable
    orphan rather than colliding on a shared name.
    """
    client = live_kamiwaza_client
    name = f"sdk_eng12434_cohort_{uuid.uuid4().hex[:10]}"
    # try/finally rather than a plain yield: a declare that commits server-side and THEN
    # fails (lost response, a body the SDK cannot model) would otherwise raise before the
    # yield, and pytest never runs a teardown for a fixture that failed in setup — so the
    # attribute would leak. The name is known before the call, so cleanup is arranged
    # first and the withdrawal tolerates "nothing was created".
    try:
        client.cluster.declare_attribute(name=name, type="string")

        yield name
    finally:
        # Two guarded steps: the unforced DELETE refuses with 409 while subjects hold
        # values — which is why ``disposable_subject`` depends on this fixture, so
        # pytest tears the subjects down first — and reaching ``withdrawn`` then needs
        # force=True. Not suppressed: swallowing it would accumulate one orphaned
        # attribute per run, the only reason this teardown exists.
        state = _withdraw_tolerating_held_subjects(client, name)
        # Skip the forced step when there is nothing left to force: the attribute was
        # already absent (None), or the unforced DELETE has itself reached ``withdrawn``.
        if state is not None and state != "withdrawn":
            client.cluster.withdraw_attribute(name, force=True)


@pytest.fixture
def disposable_subject(
    live_kamiwaza_client, declared_attribute: str
) -> Iterator[Callable[[str], Subject]]:
    """Mint attribute-bearing subjects, and sweep them on the failure path.

    The test deletes its own subjects and asserts their absence, because a record
    reflects the whole test including its cleanup. This sweep is only the net for the
    path where an assertion fails before that cleanup runs.
    """
    client = live_kamiwaza_client
    created: list[str] = []

    def _make(cohort: str) -> Subject:
        username = f"sdk-eng12434-{uuid.uuid4().hex[:12]}"
        # Recorded BEFORE the create, so an upsert that commits and then fails still
        # leaves the username on the sweep list rather than leaking it.
        created.append(username)
        return client.subjects.upsert(username, attributes={declared_attribute: cohort})

    yield _make

    remaining: list[str] = []
    for username in reversed(created):
        # Best-effort per subject: one may already be gone, and a sweep error must not
        # replace the test's own failure. Every subject gets its turn even if an earlier
        # one misbehaves, which is why the probe returns a bool. The probe also gives each
        # deletion its own settle budget, so the withdrawal's single window does not have
        # to cover N deletions — where a real orphan starts to look like a race.
        with suppress(KamiwazaError):
            client.subjects.delete(username, cascade_grants=True)
        if not _absent_within_window(client, username):
            remaining.append(username)

    # The probe's verdict is the ONLY leak signal available. The attribute withdrawal
    # that runs next cannot substitute for it: it sends subjects_holding_value from the
    # caller's own default of 0 rather than discovering live holders, so it can return
    # success-with-zero and then force-withdraw the schema while a subject still holds
    # the value. Discarding this would turn a confirmed leak into a silent one.
    if remaining:
        pytest.fail(
            "subjects still present after their deletion window: "
            + ", ".join(
                f"{u} ({_ABSENCE_OBSERVED.get(u, 'unknown')})" for u in remaining
            )
        )


def test_rebac_decisions_are_explicit_and_attributable(
    live_kamiwaza_client, disposable_subject, declared_attribute: str
) -> None:
    """An allow is attributable and reasoned; a second principal is denied by policy.

    The deny half asserts the refusal is policy-SHAPED, not that it is attributable:
    see the module docstring for why a deny's decision id cannot be read from the SDK.
    """
    client = live_kamiwaza_client

    # Attributes distinguish the principals and exercise subject administration. They are
    # deliberately NOT clearance-shaped: no attribute gate is in play, so a
    # clearance-looking name would imply an enforcement this run cannot show.
    granted = disposable_subject("granted")
    withheld = disposable_subject("withheld")
    assert granted.id and withheld.id
    assert granted.id != withheld.id
    assert granted.attributes.get(declared_attribute) == "granted"
    assert withheld.attributes.get(declared_attribute) == "withheld"

    object_ref = ObjectModel(namespace="dataset", id=f"sdk-eng12434-{uuid.uuid4().hex}")
    granted_subject = SubjectModel(namespace="user", id=granted.id)
    withheld_subject = SubjectModel(namespace="user", id=withheld.id)
    allow_check = CheckRequest(
        subject=granted_subject, relation=RELATION, object=object_ref
    )
    deny_check = CheckRequest(
        subject=withheld_subject, relation=RELATION, object=object_ref
    )

    cleaned = False
    try:
        # A fresh synthetic object must not already allow: deny-by-default.
        with pytest.raises(KamiwazaError) as unprimed:
            client.authz.check_access(allow_check)
        _skip_if_rebac_disabled(unprimed.value)
        _assert_policy_deny(unprimed.value)

        client.authz.upsert_tuple(
            RelationshipTuple(
                subject=granted_subject, relation=RELATION, object=object_ref
            )
        )

        first = _wait_for_allow(client, allow_check)
        # Both fields are required ``str`` on CheckResponse, so pydantic already rejects
        # absent or null; these assertions add that neither arrives EMPTY.
        assert first.decision_id, "an allow carried no decision id"
        assert first.reason, "an allow carried no reason"

        second = _wait_for_distinct_decision(client, allow_check, first.decision_id)
        assert second.reason, "the second allow carried no reason"

        # The withheld subject holds no tuple on this object: a relationship deny,
        # under the same policy state that just allowed its sibling.
        with pytest.raises(KamiwazaError) as denied:
            client.authz.check_access(deny_check)
        _skip_if_rebac_disabled(denied.value)
        _assert_policy_deny(denied.value)

        # Revoking restores the deny, so the grant was load-bearing for the allow.
        client.authz.delete_tuple(
            RelationshipTupleDelete(
                subject=granted_subject, relation=RELATION, object=object_ref
            )
        )
        _wait_for_deny(client, allow_check)

        # Cleanup is part of the claim the evidence record carries, so it is asserted
        # here rather than suppressed in the finally below.
        client.authz.delete_object(RelationshipObjectDelete(object=object_ref))
        for subject in (granted, withheld):
            client.subjects.delete(subject.username, cascade_grants=True)
            _wait_for_absent(client, subject.username)
        cleaned = True
    finally:
        if not cleaned:
            # Failure path only: keep the tuple store free of the synthetic object
            # without masking whichever assertion above actually failed.
            with suppress(KamiwazaError):
                client.authz.delete_object(RelationshipObjectDelete(object=object_ref))
