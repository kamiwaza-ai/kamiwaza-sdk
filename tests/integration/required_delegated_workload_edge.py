"""Fail-closed pytest policy for the delegated shared-IDP workload edge."""

from pathlib import Path

import pytest

REQUIRED_EDGE_FILE = "test_federation_delegated_workload_live.py"
REQUIRED_EDGE_CASES = frozenset(
    {"test_shared_idp_delegated_job_installs_approved_package"}
)


def required_edge_enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("require_delegated_workload_edge"))


def is_required_edge_item(item: pytest.Item) -> bool:
    required_markers = {
        "requires_two_clusters",
        "requires_shared_idp",
        "requires_owned_shared_realm",
        "requires_delegated_workload",
    }
    return (
        required_edge_enabled(item.config)
        and Path(str(item.fspath)).name == REQUIRED_EDGE_FILE
        and required_markers <= set(item.keywords)
    )


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
            "required delegated workload edge is missing contract cases: "
            + ", ".join(missing)
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
        "required delegated workload edge selected cases differ: "
        + "; ".join(details)
    )


def promote_skip(item: pytest.Item, report: pytest.TestReport) -> None:
    if not is_required_edge_item(item) or not report.skipped:
        return
    report.outcome = "failed"
    report.longrepr = (
        f"required delegated workload edge skipped during {report.when}: "
        f"{report.longrepr}"
    )


def enforce_collection(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not required_edge_enabled(config):
        return
    assert_required_cases(items)
    if not str(config.getoption("live_peer_base_url")).strip():
        raise pytest.UsageError(
            "--require-delegated-workload-edge needs --live-peer-base-url or "
            "KAMIWAZA_PEER_BASE_URL"
        )


def pytest_collection_finish(session: pytest.Session) -> None:
    if required_edge_enabled(session.config):
        assert_selected_required_cases(list(session.items))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    promote_skip(item, outcome.get_result())
