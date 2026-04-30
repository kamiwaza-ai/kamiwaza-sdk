"""Scenario execution harness — D210 M3 / UAC-16 / EDX-E2E-1.

Loads per-scenario runbook YAML, validates schema, runs registered step
handlers against the configured staging environment, and records a per-run
artifact (``runs/{scenario}-{date}-{HHMMSS}.json``) with timing + per-step
pass/fail. For scenarios with `sign_off_actor` set to a human, a markdown
sign-off artifact (``sign-off/{scenario}-{date}.md``) is rendered from
``sign-off/TEMPLATE.md`` *only when one does not already exist* — once the
named actor has filled the artifact in, a same-day re-run will not erase
their input.

Scenario handler functions are registered against a step name and called
in declared order. A scenario's test file owns its handler registry; the
harness only orchestrates schema, dispatch, timing, and artifact emission.

Step status semantics:
  * ``passed``      — handler returned without raising
  * ``failed``      — handler raised a non-skip exception; later steps
                      are recorded as ``not_reached``
  * ``skipped``     — handler raised ``pytest.skip.Exception`` (the step
                      decided this run does not apply); execution continues
  * ``pending``     — no handler registered (driver not yet implemented)
  * ``not_reached`` — earlier step failed; this step never executed
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

SCENARIOS_DIR = Path(__file__).parent
RUNBOOKS_DIR = SCENARIOS_DIR / "runbooks"
RUNS_DIR = SCENARIOS_DIR / "runs"
SIGN_OFF_DIR = SCENARIOS_DIR / "sign-off"
SIGN_OFF_TEMPLATE = SIGN_OFF_DIR / "TEMPLATE.md"

REQUIRED_RUNBOOK_FIELDS = (
    "id",
    "name",
    "sign_off_actor",
    "uacs",
    "steps",
    "expected_outcomes",
)
REQUIRED_STEP_FIELDS = ("name", "description")

# All allowed step statuses. Anything else in result.steps is a harness bug.
STEP_STATUSES = frozenset({"passed", "failed", "skipped", "pending", "not_reached"})


@dataclass
class StepResult:
    name: str
    status: str
    duration_s: float
    detail: str = ""


@dataclass
class ScenarioResult:
    scenario_id: str
    scenario_name: str
    started_at: str
    finished_at: str
    duration_s: float
    sign_off_actor: str
    ci_job_url: str | None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True iff no step ``failed`` and no step is ``not_reached``.

        ``skipped`` is non-failing (handler decided the step doesn't apply
        this run) but ``pending`` is intentionally treated as not-yet-passing
        so the driver test can distinguish "harness ran cleanly" from
        "scenario is fully implemented and green."
        """
        if not self.steps:
            return False
        return all(s.status in {"passed", "skipped"} for s in self.steps)

    @property
    def failed_steps(self) -> list[StepResult]:
        return [s for s in self.steps if s.status == "failed"]

    @property
    def pending_steps(self) -> list[StepResult]:
        return [s for s in self.steps if s.status == "pending"]


def load_runbook(scenario_id: str) -> dict:
    """Load and validate a scenario runbook by id (e.g. ``"S1"``)."""
    matches = sorted(RUNBOOKS_DIR.glob(f"{scenario_id.lower()}-*.yaml"))
    if not matches:
        raise FileNotFoundError(
            f"no runbook YAML found for {scenario_id} under {RUNBOOKS_DIR}"
        )
    if len(matches) > 1:
        raise ValueError(f"multiple runbooks match {scenario_id}: {matches}")
    runbook = yaml.safe_load(matches[0].read_text())
    _validate_runbook(runbook, source=matches[0])
    return runbook


def _validate_runbook(runbook: dict, *, source: Path) -> None:
    missing = [f for f in REQUIRED_RUNBOOK_FIELDS if f not in runbook]
    if missing:
        raise ValueError(f"{source.name}: missing required fields {missing}")
    if not isinstance(runbook["steps"], list) or not runbook["steps"]:
        raise ValueError(f"{source.name}: steps must be a non-empty list")
    if (
        not isinstance(runbook["expected_outcomes"], list)
        or not runbook["expected_outcomes"]
    ):
        raise ValueError(f"{source.name}: expected_outcomes must be a non-empty list")
    expected_prefix = f"{runbook['id'].lower()}-"
    if not source.name.startswith(expected_prefix):
        raise ValueError(
            f"{source.name}: filename must start with {expected_prefix!r} "
            f"(matches runbook id {runbook['id']!r})"
        )
    for i, step in enumerate(runbook["steps"]):
        missing_step = [f for f in REQUIRED_STEP_FIELDS if f not in step]
        if missing_step:
            raise ValueError(
                f"{source.name}: step[{i}] missing required fields {missing_step}"
            )


def run_scenario(
    runbook: dict,
    handlers: dict[str, Callable[[], str | None]],
    *,
    ci_job_url: str | None = None,
) -> ScenarioResult:
    """Execute a runbook by dispatching each step to its registered handler.

    A handler returns a ``detail`` string on success, or raises on failure.
    Raising ``pytest.skip.Exception`` (e.g. via ``pytest.skip("reason")``)
    marks the step as ``skipped`` and execution continues.

    Steps with no registered handler are recorded as ``pending``. Steps
    after a hard failure are recorded as ``not_reached`` so the artifact
    distinguishes "no handler" from "earlier step blocked us."
    """
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    results: list[StepResult] = []
    halted_at: int | None = None

    steps = runbook["steps"]
    for i, step in enumerate(steps):
        name = step["name"]
        s0 = time.monotonic()
        handler = handlers.get(name)
        if handler is None:
            results.append(
                StepResult(
                    name=name,
                    status="pending",
                    duration_s=0.0,
                    detail="no handler registered (driver not yet implemented)",
                )
            )
            continue
        try:
            detail = handler() or ""
        except pytest.skip.Exception as exc:
            results.append(
                StepResult(
                    name=name,
                    status="skipped",
                    duration_s=time.monotonic() - s0,
                    detail=f"skipped: {exc}",
                )
            )
            continue
        except BaseException as exc:  # noqa: BLE001 — record every failure
            results.append(
                StepResult(
                    name=name,
                    status="failed",
                    duration_s=time.monotonic() - s0,
                    detail=f"{exc.__class__.__name__}: {exc}",
                )
            )
            halted_at = i
            break
        else:
            results.append(
                StepResult(
                    name=name,
                    status="passed",
                    duration_s=time.monotonic() - s0,
                    detail=str(detail),
                )
            )

    if halted_at is not None:
        for step in steps[halted_at + 1 :]:
            results.append(
                StepResult(
                    name=step["name"],
                    status="not_reached",
                    duration_s=0.0,
                    detail="earlier step failed; this step did not execute",
                )
            )

    finished = datetime.now(timezone.utc)
    return ScenarioResult(
        scenario_id=runbook["id"],
        scenario_name=runbook["name"],
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_s=time.monotonic() - t0,
        sign_off_actor=runbook["sign_off_actor"],
        ci_job_url=ci_job_url or os.environ.get("CI_JOB_URL"),
        steps=results,
    )


def record_run(result: ScenarioResult) -> Path:
    """Persist a scenario result as JSON under ``runs/`` and return the path.

    Filenames include a UTC timestamp (``YYYYMMDD-HHMMSS``) so a same-day
    re-run never clobbers an earlier run's evidence.
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = result.finished_at.replace(":", "").replace("-", "").split(".", 1)[0]
    # stamp is now "YYYYMMDDTHHMMSS+0000" or "YYYYMMDDTHHMMSS"; keep only the
    # YYYYMMDD-HHMMSS portion.
    date_part, _, time_part = stamp.partition("T")
    time_part = time_part[:6] if time_part else "000000"
    out = RUNS_DIR / f"{result.scenario_id.lower()}-{date_part}-{time_part}.json"
    out.write_text(json.dumps(asdict(result), indent=2) + "\n")
    return out


def render_sign_off(result: ScenarioResult) -> Path:
    """Render the sign-off markdown artifact from the template; return path.

    Renders only when the per-day sign-off file does not already exist.
    Once the named ``sign_off_actor`` has filled the artifact in (GitHub
    login + timestamp + decision) and committed it, a same-day re-run will
    return the existing path without overwriting the human-authored content.
    """
    if not SIGN_OFF_TEMPLATE.exists():
        raise FileNotFoundError(f"sign-off template missing: {SIGN_OFF_TEMPLATE}")
    SIGN_OFF_DIR.mkdir(parents=True, exist_ok=True)
    date = result.finished_at.split("T", 1)[0]
    out = SIGN_OFF_DIR / f"{result.scenario_id.lower()}-{date}.md"
    if out.exists():
        return out
    template = SIGN_OFF_TEMPLATE.read_text()
    rendered = (
        template.replace("{{SCENARIO_ID}}", result.scenario_id)
        .replace("{{SCENARIO_NAME}}", result.scenario_name)
        .replace("{{SIGN_OFF_ACTOR}}", result.sign_off_actor)
        .replace("{{RUN_DATE}}", date)
        .replace("{{CI_JOB_URL}}", result.ci_job_url or "(local run)")
        .replace("{{DURATION_S}}", f"{result.duration_s:.1f}")
        .replace(
            "{{STEPS_TABLE}}",
            "\n".join(
                f"| `{s.name}` | {s.status} | {s.duration_s:.2f}s | {s.detail or '—'} |"
                for s in result.steps
            ),
        )
    )
    out.write_text(rendered)
    return out


def all_runbook_paths() -> Iterable[Path]:
    return sorted(RUNBOOKS_DIR.glob("s*.yaml"))
