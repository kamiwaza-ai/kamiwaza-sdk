"""A run that evidenced nothing must not write a record (ENG-11717).

Split out of ``test_evidence_v2.py``: those tests ask what a
``scenario-evidence.v2`` record must *look* like. These ask a different
question -- when does a run *earn* one at all -- of ``record_run`` and of the
scenario drivers, so they live apart.
"""

from __future__ import annotations

import pytest

from tests.e2e.scenarios import harness
from tests.e2e.scenarios.harness import (
    ScenarioResult,
    StepResult,
    record_run,
    run_scenario,
)

# Version-first: resolve_build_identity refuses a stamp that does not lead
# with a release version. Mirrors the constant in the sibling test modules.
TEST_BUILD = "0.99.0; core@sha256:abc1234; test-fixture"


@pytest.fixture(autouse=True)
def _build_identity_env(monkeypatch):
    """Every harness run needs a build identity (scenario-evidence.v2, G1).

    Both env vars are controlled here rather than inherited: a
    ``KAMIWAZA_RELEASE`` exported in the ambient shell would satisfy the
    identity requirement on its own and quietly change what these tests
    exercise. Mirrors the fixture in ``test_evidence_v2.py``.
    """
    monkeypatch.delenv("KAMIWAZA_RELEASE", raising=False)
    monkeypatch.setenv("KAMIWAZA_BUILD", TEST_BUILD)


def _runbook(steps, *, scenario_id="S1"):
    """A minimal valid runbook; capability_ids is required since ENG-11522."""
    return {
        "id": scenario_id,
        "name": f"Test scenario {scenario_id}",
        "sign_off_actor": "SDK team",
        "uacs": ["UAC-16"],
        "capability_ids": ["workrooms.create"],
        "expected_outcomes": ["something demonstrable"],
        "steps": steps,
    }


@pytest.mark.unit
class TestZeroExecutionRunsAreNotRecorded:
    """A run in which no step made a claim persists nothing (ENG-11717).

    Scoring an all-``pending`` run ``failed`` keeps the *status* honest,
    but a ``failed`` record still asserts "we exercised this capability
    and it broke" about a scenario whose driver registers no handlers at
    all, and it lands in the gap report as a red capability. The honest
    state is silence.

    The sibling producer already refuses this shape --
    ``_evidence_emitter.py::_is_evidence``, whose docstring names the
    hazard exactly -- so the two producers in this repo now agree.

    ``passed`` and ``failed`` are the only statuses that make a claim.
    ``pending`` (no handler registered), ``skipped`` (a handler declined)
    and ``not_reached`` (an earlier step failed) all say nothing about
    the capability.
    """

    def _two_step_runbook(self):
        return _runbook(
            [
                {"name": "a", "description": "..."},
                {"name": "b", "description": "..."},
            ]
        )

    def test_all_pending_run_persists_nothing(self, monkeypatch, tmp_path):
        """The S1/S3/S4/S5 shape: a driver with ``handlers = {}``."""
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr(harness, "RUNS_DIR", runs_dir)
        result = run_scenario(self._two_step_runbook(), {})
        assert [s.status for s in result.steps] == ["pending", "pending"]
        assert record_run(result) is None
        assert not runs_dir.exists() or not list(runs_dir.iterdir())

    def test_all_skipped_run_persists_nothing(self, monkeypatch, tmp_path):
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr(harness, "RUNS_DIR", runs_dir)

        def _decline():
            pytest.skip("does not apply on this host")

        result = run_scenario(self._two_step_runbook(), {"a": _decline, "b": _decline})
        assert [s.status for s in result.steps] == ["skipped", "skipped"]
        assert record_run(result) is None
        assert not runs_dir.exists() or not list(runs_dir.iterdir())

    def test_one_passed_step_among_pending_is_recorded(self, monkeypatch, tmp_path):
        """Control: a partially-implemented driver still evidences what ran."""
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr(harness, "RUNS_DIR", runs_dir)
        result = run_scenario(self._two_step_runbook(), {"a": lambda: "ok"})
        assert [s.status for s in result.steps] == ["passed", "pending"]
        path = record_run(result)
        assert path is not None and path.exists()

    def test_a_failure_is_evidence_and_is_recorded(self, monkeypatch, tmp_path):
        """Control: a failing step is a claim, so it must still persist."""
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr(harness, "RUNS_DIR", runs_dir)

        def _boom():
            raise AssertionError("nope")

        result = run_scenario(self._two_step_runbook(), {"a": _boom})
        assert result.steps[0].status == "failed"
        path = record_run(result)
        assert path is not None and path.exists()

    def test_an_unimplemented_driver_skips_without_claiming_an_artifact(
        self, monkeypatch, tmp_path
    ):
        """Exercise the driver body, which this suite otherwise never runs.

        All five scenario drivers are skipped here by the ``staging_url``
        fixture (``KAMIWAZA_STAGING_URL`` unset), so their handling of
        ``record_run``'s return value is invisible to the rest of the file.
        Call S3 directly instead: an un-updated driver would interpolate the
        new ``None`` straight into its skip message and announce that
        scaffolding was "rendered at None", while a driver still reading a
        pre-ENG-11717 ``record_run`` would leave a record behind.
        """
        from tests.e2e.scenarios import test_s3_operator_connector as s3

        runs_dir = tmp_path / "runs"
        monkeypatch.setattr(harness, "RUNS_DIR", runs_dir)
        monkeypatch.setattr(harness, "SIGN_OFF_DIR", tmp_path / "sign-off")

        with pytest.raises(pytest.skip.Exception) as excinfo:
            s3.test_s3_full_loop("https://staging.invalid", TEST_BUILD)

        message = str(excinfo.value)
        assert "unimplemented steps" in message
        assert "no evidence record written" in message
        assert "None" not in message, "the driver leaked record_run's None"
        assert not runs_dir.exists() or not list(runs_dir.iterdir())

    def test_an_unknown_step_status_is_refused_not_silently_dropped(
        self, monkeypatch, tmp_path
    ):
        """Suppression must not run ahead of validation.

        A typo'd status is in neither ``EVIDENCED_STEP_STATUSES`` nor
        ``STEP_STATUSES``, so a guard placed *before*
        ``validate_evidence_record`` sees "nothing was evidenced", returns
        ``None``, and discards a malformed result silently - turning a loud
        contract violation into lost data. Validation runs first.
        """
        monkeypatch.setattr(harness, "RUNS_DIR", tmp_path / "runs")
        result = ScenarioResult(
            scenario_id="S1",
            scenario_name="t",
            started_at="2026-08-06T17:00:00+00:00",
            finished_at="2026-08-06T17:00:05+00:00",
            duration_s=5.0,
            sign_off_actor="SDK team",
            capability_ids=["workrooms.create"],
            ci_job_url=None,
            build=TEST_BUILD,
            status="passed",
            steps=[StepResult(name="x", status="passsed", duration_s=0.1)],
        )
        with pytest.raises(ValueError, match="status"):
            record_run(result)
