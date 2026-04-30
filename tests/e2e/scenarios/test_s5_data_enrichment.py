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
def test_s5_full_loop(staging_url):
    runbook = load_runbook(SCENARIO_ID)

    handlers = {
        # Implement during T3.3 dry-run.
    }

    result = run_scenario(runbook, handlers)
    artifact = record_run(result)
    sign_off = render_sign_off(result)

    pending = [s.name for s in result.steps if s.status == "pending"]
    if pending:
        pytest.skip(
            f"S5 driver has unimplemented steps: {pending}. "
            f"Runbook + sign-off scaffolding rendered at {artifact}, {sign_off}."
        )
    assert result.passed, (
        f"S5 failed: artifact={artifact}, sign-off={sign_off}, "
        f"failed steps={[s.name for s in result.steps if s.status != 'passed']}"
    )
