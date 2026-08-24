from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from tests.integration import test_federation_onboarding_clearance_gate_live
from tests.integration import test_federation_receiver_realm_live
from tests.integration import test_federation_request_approve_live
from tests.integration import test_federation_shared_idp_gated_retrieval_live
from tests.integration import test_federation_trust_lifecycle_live
from tests.integration import test_federation_two_cluster_live
from tests.integration import test_federation_user_onboarding_live

pytestmark = pytest.mark.unit

_RECEIVER_REALM_MODULES = (
    test_federation_onboarding_clearance_gate_live,
    test_federation_receiver_realm_live,
    test_federation_request_approve_live,
    test_federation_trust_lifecycle_live,
    test_federation_two_cluster_live,
    test_federation_user_onboarding_live,
)


def _integration_conftest() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "integration" / "conftest.py"
    spec = importlib.util.spec_from_file_location(
        "_integration_conftest_receiver_realm_under_test", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _marker_names(module: ModuleType) -> set[str]:
    return {marker.name for marker in module.pytestmark}


def test_receiver_realm_live_modules_are_capability_gated() -> None:
    for module in _RECEIVER_REALM_MODULES:
        assert "requires_receiver_realm" in _marker_names(module), module.__name__


def test_shared_idp_edge_remains_active_without_receiver_realm() -> None:
    marker_names = _marker_names(
        test_federation_shared_idp_gated_retrieval_live
    )

    assert "requires_shared_idp" in marker_names
    assert "requires_receiver_realm" not in marker_names


def test_receiver_realm_marker_is_skipped_until_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration_conftest = _integration_conftest()
    item = SimpleNamespace(
        keywords={"requires_receiver_realm": object()},
        added_markers=[],
    )
    item.add_marker = item.added_markers.append
    monkeypatch.delenv("KAMIWAZA_TEST_RECEIVER_REALM", raising=False)

    integration_conftest._mark_deferred_receiver_realm_tests([item])

    assert len(item.added_markers) == 1
    skip_marker = item.added_markers[0]
    assert skip_marker.name == "skip"
    assert "ENG-10585 / ENG-9808" in skip_marker.kwargs["reason"]


def test_receiver_realm_marker_runs_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration_conftest = _integration_conftest()
    item = SimpleNamespace(
        keywords={"requires_receiver_realm": object()},
        added_markers=[],
    )
    item.add_marker = item.added_markers.append
    monkeypatch.setenv("KAMIWAZA_TEST_RECEIVER_REALM", "1")

    integration_conftest._mark_deferred_receiver_realm_tests([item])

    assert item.added_markers == []
