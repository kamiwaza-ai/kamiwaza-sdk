"""Driver for Appendix A Scenario 5 — Data enrichment constrained to a context graph.

SDK-team automated. UACs 9c / 9d / 16. Boundary-enforcement scenario;
overlaps with the UAC-9d failure-class track.
"""

from __future__ import annotations

import pytest

from tests.e2e.scenarios.harness import (
    load_runbook,
    record_run,
    render_sign_off,
    run_scenario,
)

SCENARIO_ID = "S5"


@pytest.mark.e2e
def test_s5_full_loop(staging_url, build_id):
    runbook = load_runbook(SCENARIO_ID)

    handlers = {
        # Implement during T3.3 dry-run.
    }

    result = run_scenario(runbook, handlers, build=build_id)
    artifact = record_run(result)
    sign_off = render_sign_off(result)

    if result.failed_steps:
        failed = [s.name for s in result.failed_steps]
        pytest.fail(
            f"S5 failed: artifact={artifact}, sign-off={sign_off}, failed steps={failed}"
        )
    if result.pending_steps:
        pending = [s.name for s in result.pending_steps]
        # `artifact` is None when the run evidenced nothing -- every
        # step `pending` means no handler ran, so `record_run` writes
        # no record rather than claim this capability was exercised
        # (ENG-11717). A partly-implemented driver still records what
        # it ran, so both outcomes are reachable here.
        recorded = (
            f"evidence record at {artifact}"
            if artifact is not None
            else "no evidence record written (the run evidenced nothing)"
        )
        pytest.skip(
            f"S5 driver has unimplemented steps: {pending}. "
            f"{recorded}; sign-off scaffolding at {sign_off}."
        )
    assert result.passed, (
        f"S5 unexpected non-passing result: artifact={artifact}, sign-off={sign_off}, "
        f"steps={[(s.name, s.status) for s in result.steps]}"
    )
