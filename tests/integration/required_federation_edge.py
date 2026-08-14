"""Fail-closed pytest policy for the required two-cluster federation edge."""

from pathlib import Path

import pytest

REQUIRED_EDGE_FILE = "test_federation_shared_idp_gated_retrieval_live.py"
REQUIRED_EDGE_CASES = frozenset(
    {
        "test_required_mesh_retrieval_returns_exact_post_gate_rows[U]",
        "test_required_mesh_retrieval_returns_exact_post_gate_rows[S]",
        "test_required_mesh_retrieval_returns_exact_post_gate_rows[TS]",
        "test_required_mesh_job_reaches_receiver_and_returns_marker",
        "test_unonboarded_shared_idp_user_rejected_by_receiver_allowlist",
    }
)


def required_edge_enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("require_federation_edge"))


def is_required_edge_item(item: pytest.Item) -> bool:
    required_markers = {
        "requires_two_clusters",
        "requires_shared_idp",
        "requires_owned_shared_realm",
    }
    return (
        required_edge_enabled(item.config)
        and Path(str(item.fspath)).name == REQUIRED_EDGE_FILE
        and required_markers <= set(item.keywords)
    )


def fail_or_skip(request: pytest.FixtureRequest, message: str) -> None:
    if is_required_edge_item(request.node):
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


def _edge_cases(items: list[pytest.Item]) -> set[str]:
    return {
        item.nodeid.rsplit("::", 1)[-1]
        for item in items
        if Path(str(item.fspath)).name == REQUIRED_EDGE_FILE
    }


def assert_required_cases(items: list[pytest.Item]) -> None:
    present = _edge_cases(items)
    missing = sorted(REQUIRED_EDGE_CASES - present)
    if missing:
        raise pytest.UsageError(
            "required federation edge is missing contract cases: " + ", ".join(missing)
        )


def assert_selected_required_cases(items: list[pytest.Item]) -> None:
    selected = _edge_cases(items)
    missing = sorted(REQUIRED_EDGE_CASES - selected)
    unexpected = sorted(selected - REQUIRED_EDGE_CASES)
    if not missing and not unexpected:
        return
    details = []
    if missing:
        details.append("missing=" + ",".join(missing))
    if unexpected:
        details.append("unexpected=" + ",".join(unexpected))
    raise pytest.UsageError(
        "required federation edge selected contract cases differ: " + "; ".join(details)
    )


def promote_skip(item: pytest.Item, report: pytest.TestReport) -> None:
    if not is_required_edge_item(item) or not report.skipped:
        return
    report.outcome = "failed"
    report.longrepr = (
        f"required federation edge skipped during {report.when}: {report.longrepr}"
    )


def enforce_collection(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not required_edge_enabled(config):
        return
    assert_required_cases(items)
    if not str(config.getoption("live_peer_base_url")).strip():
        raise pytest.UsageError(
            "--require-federation-edge needs --live-peer-base-url or "
            "KAMIWAZA_PEER_BASE_URL"
        )


def pytest_collection_finish(session: pytest.Session) -> None:
    """Validate the final item set after ``-k`` and marker deselection."""
    if required_edge_enabled(session.config):
        assert_selected_required_cases(list(session.items))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """A selected required federation edge may never become green via skip."""
    outcome = yield
    promote_skip(item, outcome.get_result())
