"""Unit tests for the scenario execution harness itself.

These pin the contract that the per-scenario drivers (and the upcoming T3.3
dry-run) rely on. Each test exists because a real review would have caught
the underlying bug — keeping them in place prevents regression.
"""

from __future__ import annotations

import json
import re

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

    def test_async_handler_is_awaited_not_recorded_as_coroutine_object(self):
        """Regression: previously, an ``async def`` handler returned a
        coroutine that was never awaited; the step recorded ``passed`` with
        ``<coroutine object ...>`` as detail and the body never ran. This
        produced silently false-green sign-off artifacts the moment T3.3
        plugged in real SDK calls (which are async)."""

        async def async_step():
            return "actually executed"

        result = run_scenario(
            _runbook([{"name": "x", "description": "..."}]),
            {"x": async_step},
        )
        assert result.steps[0].status == "passed"
        assert result.steps[0].detail == "actually executed"

    def test_async_handler_failure_is_recorded(self):
        """An async handler that raises should record ``failed``, not
        ``passed`` with a coroutine object."""

        async def async_boom():
            raise RuntimeError("async kaboom")

        result = run_scenario(
            _runbook([{"name": "x", "description": "..."}]),
            {"x": async_boom},
        )
        assert result.steps[0].status == "failed"
        assert "async kaboom" in result.steps[0].detail

    def test_async_handlers_share_a_single_event_loop_across_steps(self):
        """Regression: previously, ``run_scenario`` called ``asyncio.run``
        per step, creating a new event loop each time. Any async resource
        captured in a closure (e.g. ``httpx.AsyncClient``, ``AsyncOpenAI``)
        and reused across steps would fail on step 2+ with cross-loop /
        closed-loop errors. T3.3 drivers absolutely depend on a single
        loop for the whole scenario."""
        import asyncio

        # A "client" that is bound to whatever event loop creates it and
        # raises if used from a different loop — same failure mode that
        # ``httpx.AsyncClient`` and ``AsyncOpenAI`` exhibit.
        class LoopBoundClient:
            def __init__(self):
                self.loop = asyncio.get_event_loop()
                self.calls = 0

            async def call(self):
                if asyncio.get_event_loop() is not self.loop:
                    raise RuntimeError(
                        "client used from a different event loop than "
                        "the one it was created on"
                    )
                self.calls += 1
                return self.calls

        async def step_a(client):
            n = await client.call()
            return f"step_a={n}"

        async def step_b(client):
            n = await client.call()
            return f"step_b={n}"

        runbook = _runbook(
            [
                {"name": "open_client", "description": "..."},
                {"name": "use_a", "description": "..."},
                {"name": "use_b", "description": "..."},
            ]
        )

        # The client must be created *on the harness's loop*, which only
        # exists inside the loop. The first step opens it; subsequent
        # steps use it. This pins the cross-step-loop sharing contract.
        client_holder: dict = {}

        async def open_client():
            client_holder["c"] = LoopBoundClient()
            return "opened"

        result = run_scenario(
            runbook,
            {
                "open_client": open_client,
                "use_a": lambda: step_a(client_holder["c"]),
                "use_b": lambda: step_b(client_holder["c"]),
            },
        )

        assert [s.status for s in result.steps] == ["passed", "passed", "passed"], (
            f"cross-step async resource broke (per-step asyncio.run regression): "
            f"{[(s.name, s.status, s.detail) for s in result.steps]}"
        )
        assert result.steps[1].detail == "step_a=1"
        assert result.steps[2].detail == "step_b=2"

    def test_keyboard_interrupt_propagates(self):
        """Regression: previously ``except BaseException`` swallowed Ctrl-C
        and recorded it as a failed step, preventing a clean abort. The
        harness must let ``KeyboardInterrupt`` and ``SystemExit`` propagate."""

        def interrupted():
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            run_scenario(
                _runbook([{"name": "x", "description": "..."}]),
                {"x": interrupted},
            )

    def test_system_exit_propagates(self):
        def quit_now():
            raise SystemExit(1)

        with pytest.raises(SystemExit):
            run_scenario(
                _runbook([{"name": "x", "description": "..."}]),
                {"x": quit_now},
            )


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

    def test_preserves_human_authored_sign_off(self, monkeypatch, tmp_path):
        """Regression: previously, a same-day re-run would erase a
        human-authored sign-off (e.g. Preston's filled-in markdown). The
        renderer must preserve content once the canonical fill-in markers
        are gone (the actor has signed it)."""
        sign_off_dir = tmp_path / "sign-off"
        sign_off_dir.mkdir()
        template = sign_off_dir / "TEMPLATE.md"
        template.write_text("# {{SCENARIO_ID}} stub — _(fill in)_\n")
        monkeypatch.setattr(harness, "SIGN_OFF_DIR", sign_off_dir)
        monkeypatch.setattr(harness, "SIGN_OFF_TEMPLATE", template)

        result = run_scenario(
            _runbook([{"name": "x", "description": "..."}]),
            {"x": lambda: "ok"},
        )
        out = render_sign_off(result)
        # Simulate Preston filling it in (replaces the fill-in marker).
        out.write_text("# S1 — signed off by @preston (PASS)\n")

        # Re-render on the same day — must NOT erase Preston's input.
        out2 = render_sign_off(result)
        assert out2 == out
        assert "signed off by @preston" in out.read_text()

    def test_step_detail_with_pipes_and_newlines_does_not_break_table(
        self, monkeypatch, tmp_path
    ):
        """Regression: ``StepResult.detail`` interpolated raw into a Markdown
        table cell. A pipe character or a newline (realistic for a captured
        command transcript or a multi-line exception message) split the cell
        or broke out of the row entirely, producing a malformed sign-off."""
        sign_off_dir = tmp_path / "sign-off"
        sign_off_dir.mkdir()
        template = sign_off_dir / "TEMPLATE.md"
        template.write_text(
            "| Step | Status | Duration | Detail |\n"
            "|------|--------|----------|--------|\n"
            "{{STEPS_TABLE}}\n"
            "_(fill in)_\n"
        )
        monkeypatch.setattr(harness, "SIGN_OFF_DIR", sign_off_dir)
        monkeypatch.setattr(harness, "SIGN_OFF_TEMPLATE", template)

        def piped():
            return "user|admin via x|y\nstderr: KAMIWAZA_ENDPOINT=https://x"

        result = run_scenario(
            _runbook([{"name": "x", "description": "..."}]),
            {"x": piped},
        )
        out = render_sign_off(result)
        text = out.read_text()

        # The template marker must be replaced.
        assert "{{STEPS_TABLE}}" not in text
        # Locate the data row and assert it is a single line of correct shape.
        data_row = next(line for line in text.splitlines() if line.startswith("| `x`"))
        # The row must be exactly one Markdown row — 5 unescaped pipes
        # (4 columns → 5 separators). Escaped `\|` is rendered as a literal
        # pipe inside a cell and must not be counted as a separator.
        unescaped_pipes = re.findall(r"(?<!\\)\|", data_row)
        assert (
            len(unescaped_pipes) == 5
        ), f"row has wrong column count after escape; row={data_row!r}"
        # The pipe in the detail must be escaped, not break the cell.
        assert "user\\|admin" in data_row
        # The newline in the detail must be soft-wrapped, not split the row.
        assert "<br>" in data_row
        assert "\n" not in data_row

    def test_refreshes_unedited_stub_on_rerun(self, monkeypatch, tmp_path):
        """Regression: my prior fix over-corrected — if the first run produced
        a stub (because steps failed or were pending) and a later run
        succeeds, the renderer must update the file. Refresh while the
        canonical fill-in marker is still present; preserve once it's gone."""
        sign_off_dir = tmp_path / "sign-off"
        sign_off_dir.mkdir()
        template = sign_off_dir / "TEMPLATE.md"
        template.write_text(
            "# {{SCENARIO_ID}} — {{SCENARIO_NAME}}\n"
            "Steps:\n{{STEPS_TABLE}}\n"
            "| Decision | _(fill in)_ |\n"
        )
        monkeypatch.setattr(harness, "SIGN_OFF_DIR", sign_off_dir)
        monkeypatch.setattr(harness, "SIGN_OFF_TEMPLATE", template)

        # Run 1: some steps pending.
        result1 = run_scenario(
            _runbook(
                [
                    {"name": "x", "description": "..."},
                    {"name": "y", "description": "..."},
                ]
            ),
            {"x": lambda: "ok"},  # y is pending
        )
        out = render_sign_off(result1)
        first_text = out.read_text()
        assert "pending" in first_text, "first render should reflect pending step"

        # Run 2: same day, all green. Stub is still unedited
        # (fill-in marker present), so renderer should refresh.
        result2 = run_scenario(
            _runbook(
                [
                    {"name": "x", "description": "..."},
                    {"name": "y", "description": "..."},
                ]
            ),
            {"x": lambda: "ok", "y": lambda: "also ok"},
        )
        out2 = render_sign_off(result2)
        assert out2 == out, "same-day path"
        refreshed = out.read_text()
        assert (
            "pending" not in refreshed
        ), "renderer should refresh an unedited stub with the latest run"
        assert "also ok" in refreshed


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
