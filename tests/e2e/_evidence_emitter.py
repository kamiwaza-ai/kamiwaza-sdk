"""Opt-in scenario-evidence.v2 emitter for the existing e2e/live suite.

ENG-10026 (T3.7): with ``--emit-evidence`` AND a build identity
(``--build`` / ``KAMIWAZA_BUILD``), this pytest plugin groups the run's
test outcomes by the reviewed entries in ``tests/e2e/capability_map.yaml``
and, after the session, writes one ``scenario-evidence.v2`` record per
entry that saw at least one matched test actually run, into
``tests/e2e/evidence-out/`` (gitignored). Records carry
``evidence_provenance: "pre-existing"`` — they harvest what the existing
suite already demonstrates; they do not author new coverage.

Guarantees:

* Without ``--emit-evidence`` the plugin is never registered — zero
  behavior change for a normal run.
* ``--emit-evidence`` without a build identity is a refusal
  (``pytest.UsageError``), mirroring the scenario harness's G1 stance:
  evidence that does not name its build is not evidence.
* Mapping is explicit, never inferred: a test matched by no map entry
  emits nothing, and a map entry none of whose tests ran emits nothing.
* Status derivation reuses :func:`harness.derive_status` — every matched
  test becomes one step; all passed → ``passed``, any failure →
  ``failed``, green-with-skips → ``passed_with_notes``.
* Every record is validated with :func:`harness.validate_evidence_record`
  (the vendored-schema mirror) before it is written.
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

_ENTRY_KEYS = frozenset({"pattern", "capability_ids", "scenario_name"})
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Stable kebab-case slug of a scenario name (record ``scenario_id``)."""
    return _SLUG_RE.sub("-", name.lower()).strip("-")


@dataclass(frozen=True)
class MapEntry:
    """One reviewed mapping: a nodeid glob / marker → capability ids."""

    pattern: str
    scenario_name: str
    capability_ids: tuple[str, ...]

    @property
    def scenario_id(self) -> str:
        return slugify(self.scenario_name)

    def matches(self, item: pytest.Item) -> bool:
        if self.pattern.startswith(MARKER_PREFIX):
            marker = self.pattern[len(MARKER_PREFIX) :]
            return item.get_closest_marker(marker) is not None
        return fnmatchcase(item.nodeid, self.pattern)


def load_capability_map(path: Path) -> list[MapEntry]:
    """Parse and validate the explicit capability map. Raises ``ValueError``."""
    if not path.is_file():
        raise ValueError(f"capability map not found: {path}")
    raw = yaml.safe_load(path.read_text()) or []
    if not isinstance(raw, list):
        raise ValueError(f"{path.name}: top level must be a list of entries")
    entries = [_parse_entry(item, i, source=path.name) for i, item in enumerate(raw)]
    _reject_duplicate_scenario_ids(entries, source=path.name)
    return entries


def _parse_entry(item: object, i: int, *, source: str) -> MapEntry:
    if not isinstance(item, dict):
        raise ValueError(f"{source}: entry[{i}] must be a mapping")
    if set(item) != _ENTRY_KEYS:
        raise ValueError(
            f"{source}: entry[{i}] must have exactly the keys "
            f"{sorted(_ENTRY_KEYS)}; got {sorted(item)}"
        )
    _require_non_empty_str(item["pattern"], f"entry[{i}].pattern", source=source)
    _require_non_empty_str(
        item["scenario_name"], f"entry[{i}].scenario_name", source=source
    )
    cap_ids = _parse_capability_ids(item["capability_ids"], i, source=source)
    return MapEntry(
        pattern=item["pattern"],
        scenario_name=item["scenario_name"],
        capability_ids=cap_ids,
    )


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

    def fold(self, report: pytest.TestReport) -> None:
        self.duration_s += report.duration
        if report.failed:
            self.status = "failed"
        elif report.skipped and self.status != "failed":
            self.status = "skipped"


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

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        finished_at = _utc_now_iso()
        for entry in self._entries:
            steps = self._steps_for(entry)
            if steps:
                self._write_record(entry, steps, finished_at)

    def _steps_for(self, entry: MapEntry) -> list[harness.StepResult]:
        """One step per matched test that ran, in stable nodeid order."""
        steps = []
        for nodeid in sorted(self._outcomes):
            if entry not in self._matched[nodeid]:
                continue
            outcome = self._outcomes[nodeid]
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
