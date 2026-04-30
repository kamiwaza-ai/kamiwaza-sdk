"""Unit tests for the scenario execution harness itself.

These pin the contract that the per-scenario drivers (and the upcoming T3.3
dry-run) rely on. Each test exists because a real review would have caught
the underlying bug — keeping them in place prevents regression.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.scenarios import harness
from tests.e2e.scenarios.harness import (
    ScenarioResult,
    StepResult,
    _validate_runbook,
    record_run,
    render_sign_off,
    run_scenario,
)


def _runbook(steps, *, scenario_id="S1"):
    return {
        "id": scenario_id,
        "name": f"Test scenario {scenario_id}",
        "sign_off_actor": "SDK team",
        "uacs": ["UAC-16"],
        "expected_outcomes": ["something demonstrable"],
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# run_scenario — step status semantics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunScenario:
    def test_passing_handler_records_passed_with_returned_detail(self):
        runbook = _runbook([{"name": "step_a", "description": "..."}])
        result = run_scenario(runbook, {"step_a": lambda: "ok"})
        assert [s.status for s in result.steps] == ["passed"]
        assert result.steps[0].detail == "ok"
        assert result.passed is True

    def test_unregistered_handler_records_pending_and_continues(self):
        runbook = _runbook(
            [
                {"name": "step_a", "description": "..."},
                {"name": "step_b", "description": "..."},
            ]
        )
        result = run_scenario(runbook, {"step_b": lambda: "ok"})
        assert [s.status for s in result.steps] == ["pending", "passed"]
        # `passed` is False because pending steps are explicitly not-yet-passing —
        # the driver test must distinguish "fully implemented + green" from
        # "harness ran cleanly with stubs."
        assert result.passed is False
        assert result.pending_steps[0].name == "step_a"

    def test_pytest_skip_inside_handler_marks_step_skipped_and_continues(self):
        """Regression: pytest.skip.Exception inherits from BaseException, not
        Exception. A bare `except Exception:` would not catch it and would
        abort run_scenario before the skipped step was recorded."""
        runbook = _runbook(
            [
                {"name": "step_a", "description": "..."},
                {"name": "step_b", "description": "..."},
            ]
        )

        def skip_a():
            pytest.skip("doesn't apply this run")

        result = run_scenario(runbook, {"step_a": skip_a, "step_b": lambda: "ok"})

        assert [s.status for s in result.steps] == ["skipped", "passed"]
        assert "doesn't apply this run" in result.steps[0].detail
        # skipped is non-failing → result.passed True
        assert result.passed is True
        assert result.failed_steps == []

    def test_failure_halts_execution_and_marks_remaining_steps_not_reached(self):
        """Regression: previously, steps after a failure simply did not appear
        in result.steps; the artifact gave no signal that those steps were
        gated by an earlier failure. They now record explicitly."""
        runbook = _runbook(
            [
                {"name": "step_a", "description": "..."},
                {"name": "step_b", "description": "..."},
                {"name": "step_c", "description": "..."},
            ]
        )

        def boom():
            raise RuntimeError("kaboom")

        result = run_scenario(
            runbook,
            {"step_a": lambda: "ok", "step_b": boom, "step_c": lambda: "ok"},
        )

        assert [s.status for s in result.steps] == ["passed", "failed", "not_reached"]
        assert result.steps[1].detail == "RuntimeError: kaboom"
        assert result.passed is False
        assert [s.name for s in result.failed_steps] == ["step_b"]

    def test_pending_then_failed_does_not_silently_become_a_skip(self):
        """Regression: a driver that checks `pending` before `failed` would
        skip a test that actually had a failure recorded in the same run.
        The harness records both honestly; failures must be checked first."""
        runbook = _runbook(
            [
                {"name": "step_a", "description": "..."},
                {"name": "step_b", "description": "..."},
            ]
        )

        def boom():
            raise AssertionError("nope")

        # step_a is pending (no handler), step_b fails. Harness must record
        # both — the driver test, not the harness, decides the verdict.
        result = run_scenario(runbook, {"step_b": boom})
        assert [s.status for s in result.steps] == ["pending", "failed"]
        assert result.failed_steps  # the driver logic uses this to fail-fast

    def test_ci_job_url_reads_env_when_not_passed(self, monkeypatch):
        monkeypatch.setenv("CI_JOB_URL", "https://ci.test/run/42")
        result = run_scenario(
            _runbook([{"name": "x", "description": "..."}]),
            {"x": lambda: "ok"},
        )
        assert result.ci_job_url == "https://ci.test/run/42"

    def test_handler_returning_none_records_passed_with_empty_detail(self):
        result = run_scenario(
            _runbook([{"name": "x", "description": "..."}]),
            {"x": lambda: None},
        )
        assert result.steps[0].status == "passed"
        assert result.steps[0].detail == ""


# ---------------------------------------------------------------------------
# record_run — same-day re-runs preserve evidence
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecordRun:
    def test_same_day_reruns_do_not_clobber_each_other(self, monkeypatch, tmp_path):
        """Regression: previously, same-day re-runs reused the
        ``{scenario}-{date}.json`` filename and silently overwrote earlier
        evidence. Filenames now include an HHMMSS suffix."""
        monkeypatch.setattr(harness, "RUNS_DIR", tmp_path / "runs")

        first = ScenarioResult(
            scenario_id="S1",
            scenario_name="t",
            started_at="2026-04-30T17:00:00+00:00",
            finished_at="2026-04-30T17:00:05+00:00",
            duration_s=5.0,
            sign_off_actor="SDK team",
            ci_job_url=None,
            steps=[
                StepResult(name="x", status="passed", duration_s=0.1, detail="first")
            ],
        )
        second = ScenarioResult(
            scenario_id="S1",
            scenario_name="t",
            started_at="2026-04-30T19:30:00+00:00",
            finished_at="2026-04-30T19:30:07+00:00",
            duration_s=7.0,
            sign_off_actor="SDK team",
            ci_job_url=None,
            steps=[
                StepResult(name="x", status="passed", duration_s=0.1, detail="second")
            ],
        )

        first_path = record_run(first)
        second_path = record_run(second)

        assert (
            first_path != second_path
        ), f"same-day re-run clobbered prior artifact: {first_path}"
        assert first_path.exists()
        assert second_path.exists()
        assert json.loads(first_path.read_text())["steps"][0]["detail"] == "first"
        assert json.loads(second_path.read_text())["steps"][0]["detail"] == "second"


# ---------------------------------------------------------------------------
# render_sign_off — never erase a human-authored artifact
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderSignOff:
    def test_renders_template_when_artifact_does_not_exist(self, monkeypatch, tmp_path):
        sign_off_dir = tmp_path / "sign-off"
        sign_off_dir.mkdir()
        template = sign_off_dir / "TEMPLATE.md"
        template.write_text(
            "# {{SCENARIO_ID}} — {{SCENARIO_NAME}}\n"
            "Actor: {{SIGN_OFF_ACTOR}}\n"
            "Date: {{RUN_DATE}}\n"
            "CI: {{CI_JOB_URL}}\n"
            "Duration: {{DURATION_S}}\n"
            "{{STEPS_TABLE}}\n"
        )
        monkeypatch.setattr(harness, "SIGN_OFF_DIR", sign_off_dir)
        monkeypatch.setattr(harness, "SIGN_OFF_TEMPLATE", template)

        result = run_scenario(
            _runbook([{"name": "x", "description": "..."}]),
            {"x": lambda: "ok"},
        )
        out = render_sign_off(result)
        text = out.read_text()
        assert "S1" in text
        assert "{{SCENARIO_ID}}" not in text
        assert "Actor: SDK team" in text

    def test_does_not_overwrite_existing_artifact(self, monkeypatch, tmp_path):
        """Regression: previously, a same-day re-run would erase a
        human-authored sign-off (e.g. Preston's filled-in markdown). The
        renderer must now no-op when the per-day artifact exists."""
        sign_off_dir = tmp_path / "sign-off"
        sign_off_dir.mkdir()
        template = sign_off_dir / "TEMPLATE.md"
        template.write_text("# {{SCENARIO_ID}} stub\n")
        monkeypatch.setattr(harness, "SIGN_OFF_DIR", sign_off_dir)
        monkeypatch.setattr(harness, "SIGN_OFF_TEMPLATE", template)

        result = run_scenario(
            _runbook([{"name": "x", "description": "..."}]),
            {"x": lambda: "ok"},
        )
        out = render_sign_off(result)
        # Simulate Preston filling it in and committing.
        out.write_text("# S1 — signed off by @preston (PASS)\n")

        # Re-render on the same day — must NOT erase Preston's input.
        out2 = render_sign_off(result)
        assert out2 == out
        assert "signed off by @preston" in out.read_text()


# ---------------------------------------------------------------------------
# _validate_runbook — schema strictness
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateRunbook:
    def test_missing_required_field_raises(self, tmp_path):
        rb = _runbook([{"name": "x", "description": "..."}])
        del rb["uacs"]
        with pytest.raises(ValueError, match="missing required fields"):
            _validate_runbook(rb, source=tmp_path / "s1-x.yaml")

    def test_empty_steps_list_raises(self, tmp_path):
        rb = _runbook([])
        with pytest.raises(ValueError, match="steps must be a non-empty list"):
            _validate_runbook(rb, source=tmp_path / "s1-x.yaml")

    def test_empty_expected_outcomes_raises(self, tmp_path):
        rb = _runbook([{"name": "x", "description": "..."}])
        rb["expected_outcomes"] = []
        with pytest.raises(
            ValueError, match="expected_outcomes must be a non-empty list"
        ):
            _validate_runbook(rb, source=tmp_path / "s1-x.yaml")

    def test_filename_must_match_runbook_id(self, tmp_path):
        rb = _runbook([{"name": "x", "description": "..."}], scenario_id="S1")
        with pytest.raises(ValueError, match="filename must start with 's1-'"):
            _validate_runbook(rb, source=tmp_path / "s9-different.yaml")

    def test_step_missing_required_field_raises(self, tmp_path):
        rb = _runbook([{"name": "x"}])  # missing description
        with pytest.raises(ValueError, match="missing required fields"):
            _validate_runbook(rb, source=tmp_path / "s1-x.yaml")
