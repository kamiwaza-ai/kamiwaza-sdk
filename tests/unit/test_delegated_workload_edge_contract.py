"""ENG-8454: fail-closed contract for delegated workload UAT."""

from pathlib import Path
from types import SimpleNamespace

import pytest

try:
    from tests.integration import required_delegated_workload_edge as required_edge
    from tests.integration import test_federation_delegated_workload_live as live_edge
except ImportError:  # exact-parent RED: the delegated workload lane is absent
    required_edge = SimpleNamespace()
    live_edge = SimpleNamespace()

pytestmark = pytest.mark.unit


def _required_item(nodeid: str) -> SimpleNamespace:
    config = SimpleNamespace(
        getoption=lambda name: name == "require_delegated_workload_edge"
    )
    return SimpleNamespace(
        config=config,
        fspath=Path(required_edge.REQUIRED_EDGE_FILE),
        keywords={
            "requires_two_clusters": True,
            "requires_shared_idp": True,
            "requires_owned_shared_realm": True,
            "requires_delegated_workload": True,
        },
        nodeid=nodeid,
    )


def test_required_delegated_workload_edge_has_one_substantive_case() -> None:
    expected = {"test_shared_idp_delegated_job_installs_approved_package"}

    assert required_edge.REQUIRED_EDGE_CASES == expected
    assert callable(live_edge.test_shared_idp_delegated_job_installs_approved_package)

    items = [
        _required_item(f"tests/integration/{required_edge.REQUIRED_EDGE_FILE}::{case}")
        for case in expected
    ]
    required_edge.assert_required_cases(items)

    with pytest.raises(pytest.UsageError, match="missing contract cases"):
        required_edge.assert_required_cases([])


def test_required_delegated_workload_edge_promotes_skip_to_failure() -> None:
    item = _required_item(
        "tests/integration/"
        f"{required_edge.REQUIRED_EDGE_FILE}::"
        "test_shared_idp_delegated_job_installs_approved_package"
    )
    report = SimpleNamespace(
        longrepr="delegated job package catalog is not configured",
        outcome="skipped",
        skipped=True,
        when="setup",
    )

    required_edge.promote_skip(item, report)

    assert report.outcome == "failed"
    assert "package catalog" in report.longrepr


def test_delegated_workload_live_case_carries_all_capability_markers() -> None:
    marker_names = {marker.name for marker in live_edge.pytestmark}

    assert {
        "integration",
        "live",
        "requires_two_clusters",
        "requires_shared_idp",
        "requires_owned_shared_realm",
        "requires_delegated_workload",
    } <= marker_names


def test_delegated_workload_plugin_is_registered_at_pytest_root() -> None:
    root_conftest = Path("conftest.py").read_text()

    assert "tests.integration.required_delegated_workload_edge" in root_conftest


def test_delegated_workload_package_fixture_requires_a_dependency_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "KAMIWAZA_DELEGATED_TEST_PACKAGES_JSON",
        '["humanize==4.13.0", "kamiwaza-sdk==1.1.0"]',
    )
    monkeypatch.setenv(
        "KAMIWAZA_DELEGATED_TEST_IMPORTS_JSON",
        '["humanize", "kamiwaza_sdk"]',
    )
    monkeypatch.setenv("KAMIWAZA_DELEGATED_TEST_PACKAGE", "legacy==1.0.0")
    monkeypatch.setenv("KAMIWAZA_DELEGATED_TEST_IMPORT", "legacy")

    assert live_edge._delegated_package_config() == (
        ("humanize==4.13.0", "kamiwaza-sdk==1.1.0"),
        ("humanize", "kamiwaza_sdk"),
        {"humanize": "4.13.0", "kamiwaza-sdk": "1.1.0"},
    )


def test_delegated_result_requires_exact_installed_versions() -> None:
    result = SimpleNamespace(
        status="SUCCEEDED",
        result={
            "probe": "marker",
            "package_imports": ["humanize", "kamiwaza_sdk"],
            "package_versions": {
                "humanize": "4.13.0",
                "kamiwaza-sdk": "1.1.0",
            },
        },
    )

    live_edge._assert_delegated_result(
        result,
        "marker",
        ("humanize", "kamiwaza_sdk"),
        {"humanize": "4.13.0", "kamiwaza-sdk": "1.1.0"},
    )

    result.result["package_versions"]["humanize"] = "4.12.0"
    with pytest.raises(AssertionError):
        live_edge._assert_delegated_result(
            result,
            "marker",
            ("humanize", "kamiwaza_sdk"),
            {"humanize": "4.13.0", "kamiwaza-sdk": "1.1.0"},
        )


def test_delegated_package_fixture_must_change_the_base_environment() -> None:
    expected = {"humanize": "4.13.0", "kamiwaza-sdk": "1.1.0"}
    missing_from_base = SimpleNamespace(
        status="SUCCEEDED",
        result={
            "probe": "baseline",
            "package_versions": {"humanize": None, "kamiwaza-sdk": "1.1.0"},
        },
    )
    already_in_base = SimpleNamespace(
        status="SUCCEEDED",
        result={"probe": "baseline", "package_versions": expected},
    )

    live_edge._assert_package_fixture_changes_environment(
        missing_from_base,
        "baseline",
        expected,
    )
    with pytest.raises(AssertionError, match="already contains every exact package"):
        live_edge._assert_package_fixture_changes_environment(
            already_in_base,
            "baseline",
            expected,
        )
