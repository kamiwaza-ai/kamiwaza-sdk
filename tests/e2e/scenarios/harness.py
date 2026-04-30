"""Scenario execution harness — D210 M3 / UAC-16 / EDX-E2E-1.

Loads per-scenario runbook YAML, validates schema, runs registered step
handlers against the configured staging environment, and records a per-run
artifact (`runs/{scenario}-{date}.json`) with timing + per-step pass/fail.
For scenarios with `sign_off_actor` set to a human, a markdown sign-off
artifact (`sign-off/{scenario}-{date}.md`) is rendered from
`sign-off/TEMPLATE.md` and printed for the actor to fill in and commit.

Scenario handler functions are registered against a step name and called
in declared order. A scenario's test file owns its handler registry; the
harness only orchestrates schema, dispatch, timing, and artifact emission.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

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


@dataclass
class StepResult:
    name: str
    status: str  # "passed" | "failed" | "skipped" | "pending"
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
        return all(s.status == "passed" for s in self.steps)


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

    A handler returns a `detail` string on success, or raises `AssertionError`
    (or any exception) on failure. Raising `pytest.skip.Exception` marks the
    step as skipped (e.g. waiting on Preston for a manual confirmation).

    Steps with no registered handler are recorded as ``pending`` — useful
    while drivers are stubbed out before staging access is available.
    """
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    results: list[StepResult] = []

    for step in runbook["steps"]:
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
            results.append(
                StepResult(
                    name=name,
                    status="passed",
                    duration_s=time.monotonic() - s0,
                    detail=str(detail),
                )
            )
        except Exception as exc:  # noqa: BLE001 — we record any failure
            status = "skipped" if exc.__class__.__name__ == "Skipped" else "failed"
            results.append(
                StepResult(
                    name=name,
                    status=status,
                    duration_s=time.monotonic() - s0,
                    detail=f"{exc.__class__.__name__}: {exc}",
                )
            )
            if status == "failed":
                # Halt on first hard failure so subsequent steps don't run
                # against an inconsistent state.
                break

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
    """Persist a scenario result as JSON under ``runs/`` and return the path."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    date = result.finished_at.split("T", 1)[0]
    out = RUNS_DIR / f"{result.scenario_id.lower()}-{date}.json"
    out.write_text(json.dumps(asdict(result), indent=2) + "\n")
    return out


def render_sign_off(result: ScenarioResult) -> Path:
    """Render the sign-off markdown artifact from the template; return path.

    The artifact is a stub — the named ``sign_off_actor`` fills it in (GitHub
    login + timestamp + decision) before D210 sign-off.
    """
    if not SIGN_OFF_TEMPLATE.exists():
        raise FileNotFoundError(f"sign-off template missing: {SIGN_OFF_TEMPLATE}")
    SIGN_OFF_DIR.mkdir(parents=True, exist_ok=True)
    template = SIGN_OFF_TEMPLATE.read_text()
    date = result.finished_at.split("T", 1)[0]
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
    out = SIGN_OFF_DIR / f"{result.scenario_id.lower()}-{date}.md"
    out.write_text(rendered)
    return out


def all_runbook_paths() -> Iterable[Path]:
    return sorted(RUNBOOKS_DIR.glob("s*.yaml"))
