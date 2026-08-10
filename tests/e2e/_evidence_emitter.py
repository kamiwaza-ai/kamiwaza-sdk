"""Opt-in scenario-evidence.v2 emitter for the existing e2e/live suite.

ENG-10026 (T3.7): with ``--emit-evidence`` AND a build identity
(``--build`` / ``KAMIWAZA_BUILD``), this pytest plugin groups the run's
test outcomes by the reviewed entries in ``tests/e2e/capability_map.yaml``
and, after the session, writes one ``scenario-evidence.v2`` record per
entry that saw at least one matched test run to a non-skipped outcome,
into ``tests/e2e/evidence-out/`` (gitignored). Records carry
``evidence_provenance: "pre-existing"`` — they harvest what the existing
suite already demonstrates; they do not author new coverage.

Guarantees:

* Without ``--emit-evidence`` the plugin is never registered — zero
  behavior change for a normal run.
* ``--emit-evidence`` without a build identity is a refusal
  (``pytest.UsageError``), mirroring the scenario harness's G1 stance:
  evidence that does not name its build is not evidence.
* Mapping is explicit, never inferred: a test matched by no map entry
  emits nothing, and a map entry none of whose tests ran — or whose
  tests all skipped — emits nothing.
* Status derivation reuses :func:`harness.derive_status` — every matched
  test becomes one step; all passed → ``passed``, any failure →
  ``failed``, green-with-skips → ``passed_with_notes``.
* Every record is validated with :func:`harness.validate_evidence_record`
  (the vendored-schema mirror) before it is written.

Because a partial run emits from whatever subset of an entry's tests
actually ran, three rules keep "nothing failed" from masquerading as
"the capability is evidenced":

* Only tests that reached a *terminal* report contribute a step. A test
  whose setup passed but whose call never reported (the session died
  mid-test) is dropped rather than counted as ``passed``.
* An aborted session — Ctrl-C, ``-x``, internal error — writes **no**
  records at all: its coverage is unknowable, so no claim is honest.
* An entry whose matched tests all *skipped* emits nothing. Runtime skips
  are the normal outcome on an under-provisioned host, and
  ``passed_with_notes`` over an all-skipped set would claim evidence the
  run never produced.

Records are written one-per-file with a UTC timestamp in the name and
*accumulate* across runs; the output directory is a spool, not a snapshot.
Each record names its own ``build``, so a collector reading the directory
should group by ``build`` rather than assume every file came from one run.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

from tests.e2e.scenarios import harness

E2E_DIR = Path(__file__).resolve().parent
DEFAULT_MAP_PATH = E2E_DIR / "capability_map.yaml"
DEFAULT_OUT_DIR = E2E_DIR / "evidence-out"

PLUGIN_NAME = "kamiwaza-evidence-emitter"
MARKER_PREFIX = "marker:"
# The accountable actor for pre-existing automated evidence is the suite
# itself; human sign-off happens downstream on the kit side.
SIGN_OFF_ACTOR = "automated e2e suite (pre-existing evidence)"

_REQUIRED_ENTRY_KEYS = frozenset({"pattern", "capability_ids", "scenario_name"})
_OPTIONAL_ENTRY_KEYS = frozenset({"exclude"})
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Exit codes that mean the run did not finish the work it was asked to do.
_ABORTED_EXIT_CODES = frozenset(
    {
        int(pytest.ExitCode.INTERRUPTED),
        int(pytest.ExitCode.INTERNAL_ERROR),
        int(pytest.ExitCode.USAGE_ERROR),
    }
)
ABORTED_RUN_MESSAGE = (
    "evidence emitter: run was interrupted or stopped early — no "
    "scenario-evidence records written (an aborted run's coverage is unknown)."
)


def slugify(name: str) -> str:
    """Stable kebab-case slug of a scenario name (record ``scenario_id``)."""
    return _SLUG_RE.sub("-", name.lower()).strip("-")


@dataclass(frozen=True)
class MapEntry:
    """One reviewed mapping: a nodeid glob / marker → capability ids.

    ``exclude`` carves nodeid globs back out of a broad ``pattern`` so that
    every test the entry still matches is one that, on its own, exercises
    the capability — negative-path and availability-only tests must not be
    able to stand in as evidence for it.
    """

    pattern: str
    scenario_name: str
    capability_ids: tuple[str, ...]
    exclude: tuple[str, ...] = ()

    @property
    def scenario_id(self) -> str:
        return slugify(self.scenario_name)

    def matches(self, item: pytest.Item) -> bool:
        if not self._matches_pattern(item):
            return False
        return not any(fnmatchcase(item.nodeid, glob) for glob in self.exclude)

    def _matches_pattern(self, item: pytest.Item) -> bool:
        if self.pattern.startswith(MARKER_PREFIX):
            marker = self.pattern[len(MARKER_PREFIX) :]
            return item.get_closest_marker(marker) is not None
        return fnmatchcase(item.nodeid, self.pattern)


def load_capability_map(path: Path) -> list[MapEntry]:
    """Parse and validate the explicit capability map. Raises ``ValueError``."""
    if not path.is_file():
        raise ValueError(f"capability map not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text()) or []
    except OSError as exc:
        raise ValueError(f"{path.name}: cannot read capability map: {exc}") from None
    except yaml.YAMLError as exc:
        # Surfaces as the same UsageError refusal as any other bad map,
        # instead of a raw pytest_configure traceback.
        raise ValueError(f"{path.name}: invalid YAML: {exc}") from None
    if not isinstance(raw, list):
        raise ValueError(f"{path.name}: top level must be a list of entries")
    entries = [_parse_entry(item, i, source=path.name) for i, item in enumerate(raw)]
    _reject_duplicate_scenario_ids(entries, source=path.name)
    return entries


def _parse_entry(item: object, i: int, *, source: str) -> MapEntry:
    if not isinstance(item, dict):
        raise ValueError(f"{source}: entry[{i}] must be a mapping")
    _require_entry_keys(item, i, source=source)
    _require_non_empty_str(item["pattern"], f"entry[{i}].pattern", source=source)
    _require_non_empty_str(
        item["scenario_name"], f"entry[{i}].scenario_name", source=source
    )
    cap_ids = _parse_capability_ids(item["capability_ids"], i, source=source)
    entry = MapEntry(
        pattern=item["pattern"],
        scenario_name=item["scenario_name"],
        capability_ids=cap_ids,
        exclude=_parse_exclude(item.get("exclude", []), i, source=source),
    )
    if not entry.scenario_id:
        # Caught here rather than at session finish, where the whole run would
        # already have executed before schema validation rejected the record.
        raise ValueError(
            f"{source}: entry[{i}].scenario_name {entry.scenario_name!r} slugs "
            "to an empty scenario_id; use a name containing ASCII letters or digits"
        )
    return entry


def _require_entry_keys(item: dict, i: int, *, source: str) -> None:
    keys = set(item)
    unexpected = sorted(
        (keys - _REQUIRED_ENTRY_KEYS - _OPTIONAL_ENTRY_KEYS)
        | (_REQUIRED_ENTRY_KEYS - keys)
    )
    if unexpected:
        raise ValueError(
            f"{source}: entry[{i}] must have exactly the keys "
            f"{sorted(_REQUIRED_ENTRY_KEYS)} (optionally "
            f"{sorted(_OPTIONAL_ENTRY_KEYS)}); got {sorted(keys)}"
        )


def _parse_exclude(value: object, i: int, *, source: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{source}: entry[{i}].exclude must be a list of nodeid globs")
    for j, glob in enumerate(value):
        _require_non_empty_str(glob, f"entry[{i}].exclude[{j}]", source=source)
    return tuple(value)


def _require_non_empty_str(value: object, label: str, *, source: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: {label} must be a non-empty string")


def _parse_capability_ids(value: object, i: int, *, source: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(
            f"{source}: entry[{i}].capability_ids must be a non-empty list"
        )
    for cap in value:
        if not isinstance(cap, str) or not harness.CAPABILITY_ID_RE.fullmatch(cap):
            raise ValueError(
                f"{source}: entry[{i}] capability id {cap!r} must be kebab-case, "
                "optionally dot-namespaced (e.g. 'workrooms.create')"
            )
    return tuple(value)


def _reject_duplicate_scenario_ids(entries: list[MapEntry], *, source: str) -> None:
    seen: dict[str, str] = {}
    for entry in entries:
        clash = seen.get(entry.scenario_id)
        if clash is not None:
            raise ValueError(
                f"{source}: scenario_name {entry.scenario_name!r} slugs to "
                f"{entry.scenario_id!r}, already used by {clash!r}"
            )
        seen[entry.scenario_id] = entry.scenario_name


# ---------------------------------------------------------------------------
# pytest wiring (called from the root conftest.py)
# ---------------------------------------------------------------------------


def add_evidence_options(parser: pytest.Parser) -> None:
    """Register the opt-in evidence flags (ENG-10026, T3.7)."""
    group = parser.getgroup("kamiwaza")
    group.addoption(
        "--emit-evidence",
        action="store_true",
        default=False,
        help=(
            "After the run, emit one scenario-evidence.v2 record per "
            "capability_map.yaml entry whose tests ran, as pre-existing "
            "evidence. Requires a build identity (--build / KAMIWAZA_BUILD)."
        ),
    )
    group.addoption(
        "--evidence-map",
        action="store",
        default=str(DEFAULT_MAP_PATH),
        help="Path to the capability map YAML (default: tests/e2e/capability_map.yaml).",
    )
    group.addoption(
        "--evidence-out",
        action="store",
        default=str(DEFAULT_OUT_DIR),
        help="Directory evidence records are written to (default: tests/e2e/evidence-out/).",
    )


def maybe_register(config: pytest.Config) -> None:
    """Register the emitter iff ``--emit-evidence`` is on.

    Refuses (``pytest.UsageError``) when no build identity is resolvable or
    the capability map is malformed — an opted-in run must never silently
    produce anonymous or partial evidence.
    """
    if not config.getoption("emit_evidence"):
        return
    try:
        build = harness.resolve_build_identity(
            str(config.getoption("build", default="")) or None
        )
        entries = load_capability_map(Path(config.getoption("evidence_map")))
    except ValueError as exc:
        raise pytest.UsageError(f"--emit-evidence: {exc}") from None
    plugin = EvidenceEmitterPlugin(
        entries=entries,
        build=build,
        out_dir=Path(config.getoption("evidence_out")),
    )
    config.pluginmanager.register(plugin, PLUGIN_NAME)


@dataclass
class _TestOutcome:
    """Folded per-test outcome across setup/call/teardown reports."""

    status: str = "passed"
    duration_s: float = 0.0
    complete: bool = False

    def fold(self, report: pytest.TestReport) -> None:
        self.duration_s += report.duration
        if report.failed:
            self.status = "failed"
        elif report.skipped and self.status != "failed":
            self.status = "skipped"
        if _is_terminal(report):
            self.complete = True


def _is_terminal(report: pytest.TestReport) -> bool:
    """True once this report settles the test's outcome.

    A ``call`` report always settles it. A ``setup`` report settles it only
    when it failed or skipped, because the call phase never runs in that
    case. A *passing* setup with no call report means the session died
    mid-test — ``status`` still reads ``"passed"`` there, so such a test must
    not be counted as evidence.
    """
    when = getattr(report, "when", "call")
    if when == "call":
        return True
    return when == "setup" and not report.passed


class EvidenceEmitterPlugin:
    """Collects mapped test outcomes and writes v2 records at session end."""

    def __init__(self, *, entries: list[MapEntry], build: str, out_dir: Path) -> None:
        self._entries = entries
        self._build = build
        self._out_dir = out_dir
        self._started_at = _utc_now_iso()
        # nodeid → entries it matched (collection time; explicit map only).
        self._matched: dict[str, list[MapEntry]] = {}
        # nodeid → folded outcome; only tests that actually ran appear here.
        self._outcomes: dict[str, _TestOutcome] = {}

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            matches = [e for e in self._entries if e.matches(item)]
            if matches:
                self._matched[item.nodeid] = matches

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.nodeid not in self._matched:
            return
        self._outcomes.setdefault(report.nodeid, _TestOutcome()).fold(report)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if _run_was_aborted(session, exitstatus):
            _warn_aborted(session)
            return
        finished_at = _utc_now_iso()
        for entry in self._entries:
            steps = self._steps_for(entry)
            if _is_evidence(steps):
                self._write_record(entry, steps, finished_at)

    def _steps_for(self, entry: MapEntry) -> list[harness.StepResult]:
        """One step per matched test that ran to a terminal report."""
        steps = []
        for nodeid in sorted(self._outcomes):
            if entry not in self._matched[nodeid]:
                continue
            outcome = self._outcomes[nodeid]
            if not outcome.complete:
                continue
            steps.append(
                harness.StepResult(
                    name=nodeid,
                    status=outcome.status,
                    duration_s=outcome.duration_s,
                    detail=f"pytest outcome: {outcome.status}",
                )
            )
        return steps

    def _write_record(
        self, entry: MapEntry, steps: list[harness.StepResult], finished_at: str
    ) -> Path:
        record = self._build_record(entry, steps, finished_at)
        harness.validate_evidence_record(record)
        self._out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        out = self._out_dir / f"{entry.scenario_id}-{stamp}.json"
        out.write_text(json.dumps(record, indent=2) + "\n")
        return out

    def _build_record(
        self, entry: MapEntry, steps: list[harness.StepResult], finished_at: str
    ) -> dict:
        return {
            "schema": harness.EVIDENCE_SCHEMA_ID,
            "scenario_id": entry.scenario_id,
            "scenario_name": entry.scenario_name,
            # started_at/finished_at bracket the whole pytest session, while
            # duration_s is the summed cost of *this entry's* steps — they are
            # deliberately different quantities and will not agree.
            "started_at": self._started_at,
            "finished_at": finished_at,
            "duration_s": round(sum(s.duration_s for s in steps), 6),
            "sign_off_actor": SIGN_OFF_ACTOR,
            "ci_job_url": os.environ.get("CI_JOB_URL"),
            "build": self._build,
            "method": "automated",
            "capability_ids": list(entry.capability_ids),
            "evidence_provenance": "pre-existing",
            "status": harness.derive_status(steps),
            "steps": [asdict(s) for s in steps],
            # Traceability extra (schema allows additionalProperties).
            "map_pattern": entry.pattern,
        }


def _is_evidence(steps: list[harness.StepResult]) -> bool:
    """True when these steps actually say something about the capability.

    An empty or all-skipped step list emits nothing: runtime skips
    (``requires_two_clusters``, ``requires_deployable_model``, GPU-count
    gates) are the normal outcome on an under-provisioned host, and
    ``derive_status`` would score that green-with-notes — an affirmative
    claim over a capability the run never exercised.
    """
    return any(s.status != "skipped" for s in steps)


def _run_was_aborted(session: pytest.Session, exitstatus: int) -> bool:
    """True when the session stopped short of the work it was asked to do."""
    if getattr(session, "shouldstop", False):
        return True
    if getattr(session, "shouldfail", False):
        return True
    return int(exitstatus) in _ABORTED_EXIT_CODES


def _warn_aborted(session: pytest.Session) -> None:
    """Make the suppression visible rather than silent."""
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(ABORTED_RUN_MESSAGE, yellow=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
