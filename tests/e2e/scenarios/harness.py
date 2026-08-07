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

Evidence record (``scenario-evidence.v2`` — ENG-9748, sales-developer-release-kit
design §3.6/§4.6): the run artifact is the versioned successor to the original
harness record. It adds ``build`` (the build identity the run executed against —
the harness *refuses to run* without one, closing gap G1), ``method``
(``automated`` for harness runs), ``capability_ids`` (copied from the optional
runbook field of the same name), ``evidence_provenance``, and a scenario-level
three-valued ``status`` (``passed`` / ``passed_with_notes`` / ``failed``)
matching the sign-off template's decision vocabulary. Emitted records are
validated against ``schemas/scenario-evidence.v2.schema.json`` before writing.
Pre-existing v1 artifacts under ``runs/`` (no ``schema`` field) stay untouched.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
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
SCHEMAS_DIR = SCENARIOS_DIR / "schemas"
EVIDENCE_SCHEMA_PATH = SCHEMAS_DIR / "scenario-evidence.v2.schema.json"

# scenario-evidence.v2 vocabulary. Must stay in lockstep with
# schemas/scenario-evidence.v2.schema.json (pinned by a sync test in
# test_harness.py).
EVIDENCE_SCHEMA_ID = "scenario-evidence.v2"
SCENARIO_STATUSES = frozenset({"passed", "passed_with_notes", "failed"})
EVIDENCE_METHODS = frozenset({"automated", "manual"})
EVIDENCE_PROVENANCES = frozenset({"pre-existing", "cycle-authored"})
# Kebab-case segments, optionally dot-namespaced as area.capability
# (e.g. "workroom-app-launch", "workrooms.create") — ENG-9749 spike.
CAPABILITY_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*(\.[a-z0-9]+(-[a-z0-9]+)*)*$")

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
    # scenario-evidence.v2 fields (ENG-9748). ``build`` and ``status`` have
    # placeholder defaults only so hand-built results read naturally in
    # tests; ``record_run`` validates and refuses to persist a record whose
    # ``build`` is empty or whose ``status`` is not a valid scenario status.
    schema: str = EVIDENCE_SCHEMA_ID
    build: str = ""
    method: str = "automated"
    capability_ids: list[str] = field(default_factory=list)
    evidence_provenance: str = "cycle-authored"
    status: str = ""
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
    _validate_capability_ids(runbook, source=source)


def _validate_capability_ids(runbook: dict, *, source: Path) -> None:
    """Validate the OPTIONAL ``capability_ids`` runbook field (ENG-9748).

    When present it must be a list of capability identifiers — kebab-case
    segments, optionally dot-namespaced (``workrooms.create``). The harness
    copies it verbatim into the scenario-evidence.v2 record; absent means
    the mapping has not been authored yet and the record carries ``[]``.
    """
    cap_ids = runbook.get("capability_ids")
    if cap_ids is None:
        return
    if not isinstance(cap_ids, list):
        raise ValueError(f"{source.name}: capability_ids must be a list of strings")
    non_strings = [c for c in cap_ids if not isinstance(c, str)]
    if non_strings:
        raise ValueError(f"{source.name}: capability_ids must be a list of strings")
    malformed = [c for c in cap_ids if not CAPABILITY_ID_RE.fullmatch(c)]
    if malformed:
        raise ValueError(
            f"{source.name}: capability_ids entries must be kebab-case, "
            f"optionally dot-namespaced (e.g. 'workrooms.create'); got {malformed}"
        )


def resolve_build_identity(build: str | None = None) -> str:
    """Resolve the build identity for the evidence record, or refuse.

    Precedence: explicit ``build`` argument (the scenario drivers wire the
    ``--build`` pytest option through here), then the ``KAMIWAZA_BUILD``
    env var. Evidence that does not name the build it ran against cannot
    support a staleness query or a validation stamp (design gap G1), so
    the harness refuses to run rather than emit anonymous evidence.
    """
    resolved = (build or os.environ.get("KAMIWAZA_BUILD", "")).strip()
    if not resolved:
        raise ValueError(
            "scenario evidence requires a build identity: pass "
            "build=... to run_scenario (the drivers wire the --build pytest "
            "option), or set KAMIWAZA_BUILD. Refusing to run — a record "
            "without build identity cannot support staleness queries (G1)."
        )
    return resolved


def _resolve_provenance(evidence_provenance: str | None) -> str:
    """Resolve evidence provenance: argument, then env, then the default."""
    resolved = (
        evidence_provenance
        or os.environ.get("KAMIWAZA_EVIDENCE_PROVENANCE")
        or "cycle-authored"
    )
    if resolved not in EVIDENCE_PROVENANCES:
        raise ValueError(
            f"evidence_provenance must be one of {sorted(EVIDENCE_PROVENANCES)}; "
            f"got {resolved!r}"
        )
    return resolved


def derive_status(steps: list[StepResult]) -> str:
    """Derive the three-valued scenario status from per-step statuses.

    * any step ``failed`` → ``"failed"`` (``not_reached`` steps only occur
      after a failure, so they are covered by this branch);
    * all steps ``passed`` → ``"passed"``;
    * otherwise (green but with ``skipped`` / ``pending`` / ``not_reached``
      steps — caveats a human should review) → ``"passed_with_notes"``.

    An empty step list is ``"failed"`` defensively: a run that executed
    nothing is not evidence of anything.
    """
    if not steps:
        return "failed"
    if any(s.status == "failed" for s in steps):
        return "failed"
    if all(s.status == "passed" for s in steps):
        return "passed"
    return "passed_with_notes"


def run_scenario(
    runbook: dict,
    handlers: dict[str, Callable[[], str | None]],
    *,
    ci_job_url: str | None = None,
    build: str | None = None,
    evidence_provenance: str | None = None,
) -> ScenarioResult:
    """Execute a runbook by dispatching each step to its registered handler.

    A handler returns a ``detail`` string on success, or raises on failure.
    Raising ``pytest.skip.Exception`` (e.g. via ``pytest.skip("reason")``)
    marks the step as ``skipped`` and execution continues.

    Steps with no registered handler are recorded as ``pending``. Steps
    after a hard failure are recorded as ``not_reached`` so the artifact
    distinguishes "no handler" from "earlier step blocked us."

    Emits a ``scenario-evidence.v2`` result: the build identity is resolved
    *before any step runs* (see :func:`resolve_build_identity` — no build,
    no run), ``method`` is always ``"automated"`` for harness executions,
    and ``capability_ids`` is copied from the optional runbook field.
    """
    resolved_build = resolve_build_identity(build)
    provenance = _resolve_provenance(evidence_provenance)
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    results = _execute_steps(runbook["steps"], handlers)
    finished = datetime.now(timezone.utc)
    return ScenarioResult(
        scenario_id=runbook["id"],
        scenario_name=runbook["name"],
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_s=time.monotonic() - t0,
        sign_off_actor=runbook["sign_off_actor"],
        ci_job_url=ci_job_url or os.environ.get("CI_JOB_URL"),
        build=resolved_build,
        method="automated",
        capability_ids=list(runbook.get("capability_ids") or []),
        evidence_provenance=provenance,
        status=derive_status(results),
        steps=results,
    )


def _execute_steps(
    steps: list[dict],
    handlers: dict[str, Callable[[], str | None]],
) -> list[StepResult]:
    """Dispatch each step in order; halt on hard failure.

    All async handlers in a scenario share a single event loop. Creating
    a fresh loop per step (the old ``asyncio.run`` per call) breaks any
    cross-step async resource — e.g. an ``httpx.AsyncClient`` or
    ``AsyncOpenAI`` opened in step 1 and reused in step 2 — because the
    client is bound to a now-closed loop. The loop is created lazily on
    the first coroutine and torn down in the finally block.
    """
    results: list[StepResult] = []
    loop_ref: list[asyncio.AbstractEventLoop | None] = [None]
    try:
        for i, step in enumerate(steps):
            result = _dispatch_step(step["name"], handlers.get(step["name"]), loop_ref)
            results.append(result)
            if result.status == "failed":
                results.extend(_not_reached(steps[i + 1 :]))
                break
    finally:
        if loop_ref[0] is not None:
            loop_ref[0].close()
            asyncio.set_event_loop(None)
    return results


def _dispatch_step(
    name: str,
    handler: Callable[[], str | None] | None,
    loop_ref: list[asyncio.AbstractEventLoop | None],
) -> StepResult:
    """Run one step's handler and record its outcome."""
    if handler is None:
        return StepResult(
            name=name,
            status="pending",
            duration_s=0.0,
            detail="no handler registered (driver not yet implemented)",
        )
    s0 = time.monotonic()
    try:
        detail = handler()
        # Async handlers return a coroutine; await it on the scenario-level
        # loop so the body actually runs and any captured async resources
        # stay alive across steps.
        if inspect.iscoroutine(detail):
            detail = _await_on_scenario_loop(detail, loop_ref)
    except pytest.skip.Exception as exc:
        return StepResult(
            name=name,
            status="skipped",
            duration_s=time.monotonic() - s0,
            detail=f"skipped: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 — record every failure
        # Catches everything except BaseException-derived control-flow
        # exceptions (KeyboardInterrupt, SystemExit, pytest.skip — the
        # last is handled explicitly above). Ctrl-C must propagate so
        # a long staging step can be aborted cleanly.
        return StepResult(
            name=name,
            status="failed",
            duration_s=time.monotonic() - s0,
            detail=f"{exc.__class__.__name__}: {exc}",
        )
    return StepResult(
        name=name,
        status="passed",
        duration_s=time.monotonic() - s0,
        detail=str(detail or ""),
    )


def _await_on_scenario_loop(
    coro,
    loop_ref: list[asyncio.AbstractEventLoop | None],
):
    """Await a handler coroutine on the shared scenario-level event loop."""
    loop = loop_ref[0]
    if loop is None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_ref[0] = loop
    return loop.run_until_complete(coro)


def _not_reached(steps: list[dict]) -> list[StepResult]:
    """Mark steps after a hard failure as ``not_reached``."""
    return [
        StepResult(
            name=step["name"],
            status="not_reached",
            duration_s=0.0,
            detail="earlier step failed; this step did not execute",
        )
        for step in steps
    ]


def record_run(result: ScenarioResult) -> Path:
    """Persist a scenario result as JSON under ``runs/`` and return the path.

    The record is validated against the ``scenario-evidence.v2`` contract
    *before* writing — an invalid record (empty ``build``, unknown
    ``status``, malformed ``capability_ids``, ...) raises ``ValueError``
    and nothing is persisted.

    Filenames include a UTC timestamp down to microseconds
    (``YYYYMMDDTHHMMSSffffff``) so a same-day or even same-second re-run
    never clobbers an earlier run's evidence. If two runs land on identical
    microseconds (rare; only if ``finished_at`` was hand-set), a numeric
    suffix disambiguates.
    """
    record = asdict(result)
    validate_evidence_record(record)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _timestamp_suffix(result.finished_at)
    out = RUNS_DIR / f"{result.scenario_id.lower()}-{stamp}.json"
    counter = 1
    while out.exists():
        out = RUNS_DIR / f"{result.scenario_id.lower()}-{stamp}-{counter}.json"
        counter += 1
    out.write_text(json.dumps(record, indent=2) + "\n")
    return out


# ---------------------------------------------------------------------------
# scenario-evidence.v2 structural validation
#
# ``jsonschema`` is not a dependency of this project's test extras, and per
# core-principles we do not add one for this. The checks below mirror
# schemas/scenario-evidence.v2.schema.json field for field; a sync test in
# test_harness.py pins the two against each other so they cannot drift.
# ---------------------------------------------------------------------------


def _is_non_empty_str(v: object) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _is_non_negative_num(v: object) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return v >= 0


def _is_iso8601_datetime(v: object) -> bool:
    """True iff ``v`` is an RFC 3339 date-time with a UTC/offset timezone.

    Normalizes a trailing ``Z`` to ``+00:00`` before parsing: ``fromisoformat``
    only accepts a bare ``Z`` suffix on Python >=3.11, but this project's
    minimum is 3.10 (``pyproject.toml``). Requires ``tzinfo`` to be present so
    date-only (``2026-08-06``) and timezone-less (``2026-08-06T12:00:00``)
    strings — both accepted by ``fromisoformat`` but neither a valid
    ``date-time`` per the schema's RFC 3339 + UTC contract — are rejected.
    """
    if not isinstance(v, str) or v.strip() == "":
        return False
    normalized = v[:-1] + "+00:00" if v.endswith("Z") else v
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None


# (field, predicate, requirement description) — mirrors the schema's
# scenario-level ``required`` + ``properties`` constraints.
_SCALAR_FIELD_CHECKS: tuple[tuple[str, Callable[[object], bool], str], ...] = (
    ("schema", lambda v: v == EVIDENCE_SCHEMA_ID, f"must be {EVIDENCE_SCHEMA_ID!r}"),
    ("scenario_id", _is_non_empty_str, "must be a non-empty string"),
    ("scenario_name", _is_non_empty_str, "must be a non-empty string"),
    ("started_at", _is_iso8601_datetime, "must be a non-empty ISO-8601 string"),
    ("finished_at", _is_iso8601_datetime, "must be a non-empty ISO-8601 string"),
    ("duration_s", _is_non_negative_num, "must be a non-negative number"),
    ("sign_off_actor", _is_non_empty_str, "must be a non-empty string"),
    ("ci_job_url", lambda v: v is None or isinstance(v, str), "must be str or null"),
    ("build", _is_non_empty_str, "must be a non-empty string (G1: build identity)"),
    (
        "method",
        lambda v: v in EVIDENCE_METHODS,
        f"must be one of {sorted(EVIDENCE_METHODS)}",
    ),
    (
        "evidence_provenance",
        lambda v: v in EVIDENCE_PROVENANCES,
        f"must be one of {sorted(EVIDENCE_PROVENANCES)}",
    ),
    (
        "status",
        lambda v: v in SCENARIO_STATUSES,
        f"must be one of {sorted(SCENARIO_STATUSES)}",
    ),
)


def validate_evidence_record(record: dict) -> None:
    """Structurally validate a ``scenario-evidence.v2`` record (a dict).

    Raises ``ValueError`` describing every problem found. Extra fields are
    allowed (the schema sets ``additionalProperties: true`` for forward
    compatibility); v1 records — which lack the ``schema`` field — are
    intentionally rejected: they are historical artifacts, not v2 records.
    """
    problems = _check_scalar_fields(record)
    problems += _check_capability_ids(record)
    problems += _check_steps(record)
    if problems:
        raise ValueError(f"{EVIDENCE_SCHEMA_ID} record invalid: " + "; ".join(problems))


def _check_scalar_fields(record: dict) -> list[str]:
    problems = []
    for field_name, predicate, requirement in _SCALAR_FIELD_CHECKS:
        if field_name not in record:
            problems.append(f"missing required field {field_name!r}")
        elif not predicate(record[field_name]):
            problems.append(f"{field_name} {requirement} (got {record[field_name]!r})")
    return problems


def _check_capability_ids(record: dict) -> list[str]:
    cap_ids = record.get("capability_ids")
    if not isinstance(cap_ids, list):
        return ["capability_ids must be a list (may be empty)"]
    problems = []
    for c in cap_ids:
        if not isinstance(c, str) or not CAPABILITY_ID_RE.fullmatch(c):
            problems.append(
                f"capability_ids entry {c!r} must be kebab-case, optionally "
                "dot-namespaced (e.g. 'workrooms.create')"
            )
    return problems


def _check_steps(record: dict) -> list[str]:
    steps = record.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["steps must be a non-empty list"]
    problems = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            problems.append(f"steps[{i}] must be an object")
            continue
        problems += _check_one_step(step, i)
    return problems


def _check_one_step(step: dict, i: int) -> list[str]:
    problems = []
    if not _is_non_empty_str(step.get("name")):
        problems.append(f"steps[{i}].name must be a non-empty string")
    if step.get("status") not in STEP_STATUSES:
        problems.append(f"steps[{i}].status must be one of {sorted(STEP_STATUSES)}")
    if not _is_non_negative_num(step.get("duration_s")):
        problems.append(f"steps[{i}].duration_s must be a non-negative number")
    if "detail" in step and not isinstance(step["detail"], str):
        problems.append(f"steps[{i}].detail must be a string when present")
    return problems


def _timestamp_suffix(iso: str) -> str:
    """Convert an ISO-8601 timestamp to a filesystem-safe, sortable stamp.

    Uses ``datetime.fromisoformat`` rather than string mangling, so timezone
    offsets containing ``:`` no longer corrupt the suffix.
    """
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%Y%m%dT%H%M%S%f")


# Sentinel substring that marks an unedited sign-off stub. Lives in the
# ``Sign-off`` table cells of TEMPLATE.md; once the named actor fills the
# row in, the substring is gone and ``render_sign_off`` treats the file
# as human-authored.
_UNEDITED_STUB_MARKER = "_(fill in)_"


def render_sign_off(result: ScenarioResult) -> Path:
    """Render the sign-off markdown artifact from the template; return path.

    Refresh semantics on a same-day re-run:
      * file does not exist → render fresh stub.
      * file exists and still contains an unedited fill-in marker
        (``_(fill in)_``) or unrendered ``{{...}}`` placeholders → refresh
        with the latest run's step results. The first run can leave a stub
        that later runs override.
      * file exists and the fill-in markers have been replaced (i.e. the
        named ``sign_off_actor`` has signed it) → preserve. A same-day
        re-run never erases a human-authored sign-off.
    """
    if not SIGN_OFF_TEMPLATE.exists():
        raise FileNotFoundError(f"sign-off template missing: {SIGN_OFF_TEMPLATE}")
    SIGN_OFF_DIR.mkdir(parents=True, exist_ok=True)
    date = result.finished_at.split("T", 1)[0]
    out = SIGN_OFF_DIR / f"{result.scenario_id.lower()}-{date}.md"
    if out.exists() and not _is_unedited_stub(out.read_text()):
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
                f"| `{s.name}` | {s.status} | {s.duration_s:.2f}s | "
                f"{_md_cell(s.detail) or '—'} |"
                for s in result.steps
            ),
        )
    )
    out.write_text(rendered)
    return out


def _md_cell(text: str) -> str:
    """Escape a value for safe inclusion in a Markdown table cell.

    A raw ``|`` or newline in ``StepResult.detail`` (realistic for
    multi-line exception messages or captured command transcripts) would
    split the cell or break out of the table row entirely, producing a
    malformed UAT sign-off artifact.
    """
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _is_unedited_stub(content: str) -> bool:
    """Heuristic: the file is still an auto-rendered (or literal-template)
    stub if any unreplaced template placeholder OR the canonical fill-in
    marker is present. Once the actor signs off, both are gone."""
    return _UNEDITED_STUB_MARKER in content or "{{" in content


def all_runbook_paths() -> Iterable[Path]:
    return sorted(RUNBOOKS_DIR.glob("s*.yaml"))
