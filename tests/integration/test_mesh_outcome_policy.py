"""The mesh-outcome policy, pinned without a cluster (ENG-9664).

Deliberately carries NO pytest markers, so it runs under the default
``make test`` selection (-m "not integration and not live and not e2e") on
every PR. The classifier that let a total mesh outage report green was only
reachable behind a two-cluster rig; the rules it encodes are pure functions and
belong under a gate that always runs.
"""

from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError, AuthenticationError
from tests.integration.mesh_outcome import (
    FAIL,
    PASS,
    SKIP,
    MeshFailure,
    MeshPolicy,
    classify,
    reason_of,
)

# Asserts the credential is accepted at ingress; anything past that is out of
# scope for the assertion.
TIER2 = MeshPolicy(
    identity_arranged=True,
    admission_is_the_assertion=True,
    context="tier2 receiver_realm",
)
# Pairs but enrols no guest, and asserts something downstream of admission.
WALKTHROUGH = MeshPolicy(
    identity_arranged=False,
    admission_is_the_assertion=False,
    context="walkthrough",
)


def _failure(*, is_401=False, status=None, reason=None, blob=""):
    return MeshFailure(is_401=is_401, status=status, reason=reason, blob=blob)


class TestTheDefectThisModuleExistsFor:
    def test_a_401_fails_even_though_it_carries_no_status(self):
        """ENG-9664: the exact shape that could not go red.

        A 401 arrives as AuthenticationError, which has no ``status_code``, so
        the old ``getattr(exc, "status_code", None)`` classifier saw None,
        matched no branch, and fell through to a terminal skip -- through a
        total mesh outage, on every run.
        """
        assert classify(_failure(is_401=True, status=None), TIER2) == FAIL

    def test_sdk_401_carries_no_status_code(self):
        """Pin the invariant the module's design depends on.

        If someone "fixes" 401s by making them status-bearing, that silently
        re-routes ~17 ``except APIError`` sites across the SDK. This test makes
        that a signal rather than a surprise.
        """
        exc = AuthenticationError("nope")
        assert not isinstance(exc, APIError)
        assert getattr(exc, "status_code", None) is None


class TestIdentityArrangedDecidesA401:
    def test_401_skips_when_the_suite_arranged_no_credential(self):
        assert classify(_failure(is_401=True), WALKTHROUGH) == SKIP

    def test_401_fails_when_the_suite_arranged_one(self):
        assert classify(_failure(is_401=True), TIER2) == FAIL


class TestAuthLayer403IsNeverThePositiveSignal:
    @pytest.mark.parametrize(
        "reason",
        [
            "unauthorized_guest",
            "revoked_guest",
            "receiver_realm_sub_missing",
            "peer_jwt_validation_failed",
            "invalid_signature",
        ],
    )
    def test_receiver_refusing_the_credential_fails(self, reason):
        """A 403 whose reason is the receiver rejecting the CREDENTIAL is the
        same failure a 401 is, wearing a different code."""
        assert classify(_failure(status=403, reason=reason), TIER2) == FAIL

    def test_brokered_allowlist_reasons_stay_a_skip(self):
        """The documented "this walkthrough allowlisted nobody" precondition.

        Folding the brokered family into AUTH_LAYER_REASONS would flip a
        legitimate shared_idp skip into a failure.
        """
        failure = _failure(status=403, reason="unauthorized_brokered_user")
        assert classify(failure, WALKTHROUGH) == SKIP

    def test_substring_fallback_when_detail_is_not_structured(self):
        failure = _failure(status=403, blob="apierror('peer_jwt required')")
        assert classify(failure, TIER2) == FAIL


class TestAdmissionAssertionDecidesAPlain403:
    @pytest.mark.parametrize("status", [403, 404])
    def test_plain_denial_is_the_positive_signal_for_tier2(self, status):
        """Admitted at ingress, then declined on the merits -- exactly what a
        bare guest with no cluster:viewer grant should get."""
        assert classify(_failure(status=status), TIER2) == PASS

    @pytest.mark.parametrize("status", [403, 404])
    def test_plain_denial_is_an_unmet_precondition_for_the_walkthrough(self, status):
        assert classify(_failure(status=status), WALKTHROUGH) == SKIP


class TestNothingElseIsTolerated:
    @pytest.mark.parametrize("status", [500, 502, 504, None])
    @pytest.mark.parametrize("policy", [TIER2, WALKTHROUGH], ids=["tier2", "walk"])
    def test_server_and_transport_errors_always_fail(self, status, policy):
        assert classify(_failure(status=status), policy) == FAIL


class TestReasonExtraction:
    def test_nested_detail_dict(self):
        exc = APIError("x", status_code=403, response_data={"detail": {"reason": "revoked_guest"}})
        assert reason_of(exc) == "revoked_guest"

    def test_string_detail_yields_none(self):
        exc = APIError("x", status_code=403, response_data={"detail": "nope"})
        assert reason_of(exc) is None

    def test_absent_body_yields_none(self):
        assert reason_of(AuthenticationError("x")) is None


class TestSuitesAreWiredToTheRightPolicy:
    """The policy being correct is useless if a suite passes the wrong one.

    These import the live modules' module-level constants (import only — the
    live tests themselves stay deselected without a peer rig) and assert the
    axis that decides whether a 401 reds.
    """

    def test_receiver_realm_tier2_is_armed(self):
        from tests.integration.test_federation_receiver_realm_live import (
            _TIER2_POLICY,
        )

        # identity_arranged=True is what turns a 401 into a failure. If this
        # flips, ENG-9664 is back: a mesh outage reports green.
        assert _TIER2_POLICY.identity_arranged is True
        assert classify(_failure(is_401=True), _TIER2_POLICY) == FAIL

    def test_walkthrough_still_skips_a_401(self):
        from tests.integration.test_federation_two_cluster_live import (
            _WALKTHROUGH_POLICY,
        )

        # This suite pairs but enrols no guest, so a 401 is the designed answer.
        # Arming it would red every run and send a triager hunting a live bug.
        assert _WALKTHROUGH_POLICY.identity_arranged is False
        assert classify(_failure(is_401=True), _WALKTHROUGH_POLICY) == SKIP

    def test_shared_idp_keeps_its_401_hard_fail(self):
        from tests.integration.test_federation_shared_idp_gated_retrieval_live import (
            _SHARED_IDP_POLICY,
        )

        assert _SHARED_IDP_POLICY.identity_arranged is True
        assert classify(_failure(is_401=True), _SHARED_IDP_POLICY) == FAIL
