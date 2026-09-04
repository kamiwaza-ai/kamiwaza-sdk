from __future__ import annotations

import pytest

from tests.integration import conftest as integration_conftest
from tests.integration import (
    test_federation_onboarding_clearance_gate_live,
    test_federation_receiver_realm_live,
    test_federation_request_approve_live,
    test_federation_shared_idp_gated_retrieval_live,
    test_federation_trust_lifecycle_live,
    test_federation_two_cluster_live,
    test_federation_user_onboarding_live,
)

pytestmark = pytest.mark.unit

_RECEIVER_REALM_MODULES = (
    test_federation_onboarding_clearance_gate_live,
    test_federation_receiver_realm_live,
    test_federation_request_approve_live,
    test_federation_trust_lifecycle_live,
    test_federation_two_cluster_live,
    test_federation_user_onboarding_live,
)


def _marker_names(module: object) -> set[str]:
    return {marker.name for marker in module.pytestmark}


def test_receiver_realm_live_modules_are_capability_gated() -> None:
    for module in _RECEIVER_REALM_MODULES:
        assert "requires_receiver_realm" in _marker_names(module), module.__name__


def test_shared_idp_edge_remains_active_without_receiver_realm() -> None:
    marker_names = _marker_names(test_federation_shared_idp_gated_retrieval_live)

    assert "requires_shared_idp" in marker_names
    assert "requires_receiver_realm" not in marker_names


def test_receiver_realm_marker_is_not_deferred_by_environment() -> None:
    """The capability marker must not hide the supported suite by default."""
    assert not hasattr(integration_conftest, "_mark_deferred_receiver_realm_tests")
