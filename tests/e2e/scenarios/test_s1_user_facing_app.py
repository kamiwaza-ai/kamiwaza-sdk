"""Driver for Appendix A Scenario 1 — User-facing app with forced login.

Manual sign-off: Preston McGowan. UACs 9 / 9a / 9b / 12 / 16.

Step handlers are intentionally empty stubs until the staging cluster is
available (gated on T3.3 dry-run access). When KAMIWAZA_STAGING_URL is set
and handlers are filled in, ``test_s1_full_loop`` exercises the runbook
end-to-end and writes a sign-off artifact.
"""

from __future__ import annotations

import pytest

from tests.e2e.scenarios.harness import (
    load_runbook,
    record_run,
    render_sign_off,
    run_scenario,
)

SCENARIO_ID = "S1"


@pytest.mark.e2e
def test_s1_full_loop(staging_url):
    runbook = load_runbook(SCENARIO_ID)

    handlers = {
        # Each handler returns a detail string on success or raises on failure.
        # Implement during T3.3 dry-run when staging access is wired up.
        # "scaffold_app": lambda: _kz_ext_create_app(...),
        # "dev_local_with_auth_bridge": lambda: _kz_ext_dev_local_auth(...),
        # ...
    }

    result = run_scenario(runbook, handlers)
    artifact = record_run(result)
    sign_off = render_sign_off(result)

    # Failures are checked before pending so a failure that follows an
    # unimplemented step is not silently masked by a skip.
    if result.failed_steps:
        failed = [s.name for s in result.failed_steps]
        pytest.fail(
            f"S1 failed: artifact={artifact}, sign-off={sign_off}, failed steps={failed}"
        )
    if result.pending_steps:
        pending = [s.name for s in result.pending_steps]
        pytest.skip(
            f"S1 driver has unimplemented steps: {pending}. "
            f"Runbook + sign-off scaffolding rendered at {artifact}, {sign_off}."
        )
    assert result.passed, (
        f"S1 unexpected non-passing result: artifact={artifact}, sign-off={sign_off}, "
        f"steps={[(s.name, s.status) for s in result.steps]}"
    )
