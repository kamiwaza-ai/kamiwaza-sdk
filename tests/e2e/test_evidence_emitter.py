"""Tests for the opt-in scenario-evidence.v2 emitter (ENG-10026, T3.7).

Pins the plugin contract: no behavior without ``--emit-evidence``; refusal
without a build identity (and without a parseable map); explicit-map-only
matching with ``exclude`` carve-outs (unmapped, excluded, and unrun tests
emit nothing); one conforming, schema-validated ``scenario-evidence.v2``
record per map entry whose tests ran, with ``evidence_provenance:
"pre-existing"`` and harness ``derive_status`` composition (all passed →
passed, skips → passed_with_notes, any failure → failed).

Also pins the three rules that stop a partial run from claiming evidence:
an incomplete test contributes no step, an aborted session writes nothing,
and an all-skipped entry emits nothing.

End-to-end cases run pytest in-process via ``pytester`` with a fixture
capability map and simulated test results; loader and outcome-folding
semantics are pinned by direct unit tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e import _evidence_emitter as emitter
from tests.e2e.scenarios import harness

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_BUILD = "kamiwaza-0.99.0+test.abc1234"

# Mirrors the real wiring in the repo-root conftest.py: register the
# options, then conditionally register the plugin. The sys.path insert
# keeps the conftest importable under pytester's subprocess mode too.
INNER_CONFTEST = f"""
import sys

sys.path.insert(0, {str(REPO_ROOT)!r})

from tests.e2e import _evidence_emitter as em


def pytest_addoption(parser):
    parser.addoption("--build", action="store", default="")
    em.add_evidence_options(parser)


def pytest_configure(config):
    em.maybe_register(config)
"""

MAP_ONE_ENTRY = """
- pattern: "test_mapped.py::*"
  capability_ids: [workrooms.create]
  scenario_name: "Mapped scenario"
"""


@pytest.fixture(autouse=True)
def _build_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic build identity; refusal tests delete it explicitly."""
    monkeypatch.setenv("KAMIWAZA_BUILD", TEST_BUILD)


@pytest.fixture()
def evidence_out(pytester: pytest.Pytester) -> Path:
    return pytester.path / "evidence-out"


def _run_emitting(
    pytester, evidence_out, *extra_args, map_yaml=MAP_ONE_ENTRY, subprocess=False
):
    """Run the inner suite with evidence emission wired up.

    ``subprocess=True`` is needed for the interrupted-run case: an inner
    ``KeyboardInterrupt`` propagates straight out of ``runpytest_inprocess``
    and would tear down the outer session too.
    """
    pytester.makeconftest(INNER_CONFTEST)
    map_path = pytester.path / "capability_map.yaml"
    map_path.write_text(map_yaml)
    runner = (
        pytester.runpytest_subprocess if subprocess else pytester.runpytest_inprocess
    )
    return runner(
        "--evidence-map",
        str(map_path),
        "--evidence-out",
        str(evidence_out),
        *extra_args,
    )


def _records(evidence_out: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(evidence_out.glob("*.json"))]


# ---------------------------------------------------------------------------
# End-to-end plugin behavior (pytester)
# ---------------------------------------------------------------------------


def test_flag_off_emits_nothing(pytester, evidence_out):
    pytester.makepyfile(test_mapped="def test_ok():\n    assert True\n")
    result = _run_emitting(pytester, evidence_out)
    result.assert_outcomes(passed=1)
    assert not evidence_out.exists()


def test_unmapped_tests_emit_nothing(pytester, evidence_out):
    """A run where no mapped test executed writes zero records."""
    pytester.makepyfile(test_other="def test_ok():\n    assert True\n")
    result = _run_emitting(
        pytester, evidence_out, "--emit-evidence", "--build", TEST_BUILD
    )
    result.assert_outcomes(passed=1)
    assert not evidence_out.exists()


def test_pass_plus_skip_emits_passed_with_notes(pytester, evidence_out):
    pytester.makepyfile(
        test_mapped=(
            "import pytest\n"
            "def test_ok():\n    assert True\n"
            "def test_skipped():\n    pytest.skip('not applicable')\n"
        ),
        test_unmapped="def test_also_ok():\n    assert True\n",
    )
    result = _run_emitting(
        pytester, evidence_out, "--emit-evidence", "--build", TEST_BUILD
    )
    result.assert_outcomes(passed=2, skipped=1)

    records = _records(evidence_out)
    assert len(records) == 1  # the unmapped test contributed nothing
    record = records[0]
    harness.validate_evidence_record(record)
    assert record["schema"] == harness.EVIDENCE_SCHEMA_ID
    assert record["scenario_id"] == "mapped-scenario"
    assert record["build"] == TEST_BUILD
    assert record["method"] == "automated"
    assert record["evidence_provenance"] == "pre-existing"
    assert record["capability_ids"] == ["workrooms.create"]
    assert record["status"] == "passed_with_notes"
    assert [(s["name"], s["status"]) for s in record["steps"]] == [
        ("test_mapped.py::test_ok", "passed"),
        ("test_mapped.py::test_skipped", "skipped"),
    ]


def test_all_passed_emits_passed(pytester, evidence_out):
    pytester.makepyfile(
        test_mapped="def test_a():\n    pass\ndef test_b():\n    pass\n"
    )
    _run_emitting(pytester, evidence_out, "--emit-evidence", "--build", TEST_BUILD)
    (record,) = _records(evidence_out)
    harness.validate_evidence_record(record)
    assert record["status"] == "passed"
    assert all(s["status"] == "passed" for s in record["steps"])


def test_any_failure_emits_failed(pytester, evidence_out):
    pytester.makepyfile(
        test_mapped=(
            "def test_ok():\n    assert True\n" "def test_broken():\n    assert False\n"
        )
    )
    result = _run_emitting(
        pytester, evidence_out, "--emit-evidence", "--build", TEST_BUILD
    )
    result.assert_outcomes(passed=1, failed=1)
    (record,) = _records(evidence_out)
    harness.validate_evidence_record(record)
    assert record["status"] == "failed"
    statuses = {s["name"]: s["status"] for s in record["steps"]}
    assert statuses["test_mapped.py::test_broken"] == "failed"
    assert statuses["test_mapped.py::test_ok"] == "passed"


def test_marker_pattern_matches(pytester, evidence_out):
    map_yaml = """
- pattern: "marker:mapped_cap"
  capability_ids: [models.local-deployment]
  scenario_name: "Marker mapped scenario"
"""
    pytester.makepyfile(
        test_marked=(
            "import pytest\n"
            "@pytest.mark.mapped_cap\n"
            "def test_ok():\n    assert True\n"
            "def test_unmarked():\n    assert True\n"
        )
    )
    _run_emitting(
        pytester,
        evidence_out,
        "--emit-evidence",
        "--build",
        TEST_BUILD,
        map_yaml=map_yaml,
    )
    (record,) = _records(evidence_out)
    assert [s["name"] for s in record["steps"]] == ["test_marked.py::test_ok"]


def test_record_conforms_to_vendored_schema(pytester, evidence_out):
    """The emitted record satisfies the vendored v2 schema's constraints."""
    pytester.makepyfile(test_mapped="def test_ok():\n    assert True\n")
    _run_emitting(pytester, evidence_out, "--emit-evidence", "--build", TEST_BUILD)
    (record,) = _records(evidence_out)

    schema = json.loads(harness.EVIDENCE_SCHEMA_PATH.read_text())
    assert set(schema["required"]) <= set(record)
    assert record["schema"] == schema["properties"]["schema"]["const"]
    assert record["status"] in schema["properties"]["status"]["enum"]
    assert record["method"] in schema["properties"]["method"]["enum"]
    assert (
        record["evidence_provenance"]
        in schema["properties"]["evidence_provenance"]["enum"]
    )
    step_required = set(schema["$defs"]["step"]["required"])
    assert all(step_required <= set(s) for s in record["steps"])
    # And the harness-side mirror of that schema agrees.
    harness.validate_evidence_record(record)


def test_all_skipped_entry_emits_nothing(pytester, evidence_out):
    """The under-provisioned-host shape: every mapped test skips at runtime.

    ``derive_status`` would score this ``passed_with_notes``; emitting that
    would claim non-failing evidence for a capability nothing exercised.
    """
    pytester.makepyfile(
        test_mapped=(
            "import pytest\n"
            "def test_needs_gpu():\n    pytest.skip('no gpu')\n"
            "def test_needs_peer():\n    pytest.skip('single cluster')\n"
        )
    )
    result = _run_emitting(
        pytester, evidence_out, "--emit-evidence", "--build", TEST_BUILD
    )
    result.assert_outcomes(skipped=2)
    assert not evidence_out.exists()


def test_skip_at_collection_gate_emits_nothing(pytester, evidence_out):
    """Module-level skip (the marker/fixture gate shape) is also not evidence."""
    pytester.makepyfile(
        test_mapped=(
            "import pytest\n"
            "pytestmark = pytest.mark.skip('requires two clusters')\n"
            "def test_a():\n    assert True\n"
        )
    )
    result = _run_emitting(
        pytester, evidence_out, "--emit-evidence", "--build", TEST_BUILD
    )
    result.assert_outcomes(skipped=1)
    assert not evidence_out.exists()


def test_failure_among_skips_still_emits_failed(pytester, evidence_out):
    """The all-skipped gate must not swallow genuine failure evidence."""
    pytester.makepyfile(
        test_mapped=(
            "import pytest\n"
            "def test_skipped():\n    pytest.skip('n/a')\n"
            "def test_broken():\n    assert False\n"
        )
    )
    _run_emitting(pytester, evidence_out, "--emit-evidence", "--build", TEST_BUILD)
    (record,) = _records(evidence_out)
    assert record["status"] == "failed"


def test_interrupted_session_emits_nothing(pytester, evidence_out):
    """Ctrl-C mid-suite: coverage is unknowable, so no record is honest."""
    pytester.makepyfile(
        test_mapped=(
            "def test_a():\n    assert True\n"
            "def test_b_interrupts():\n    raise KeyboardInterrupt\n"
            "def test_c():\n    assert True\n"
        )
    )
    result = _run_emitting(
        pytester,
        evidence_out,
        "--emit-evidence",
        "--build",
        TEST_BUILD,
        subprocess=True,
    )
    assert result.ret == pytest.ExitCode.INTERRUPTED
    assert not evidence_out.exists()
    result.stdout.fnmatch_lines(["*no scenario-evidence records written*"])


def test_stop_early_on_maxfail_emits_nothing(pytester, evidence_out):
    """``-x`` leaves later mapped tests unrun — the same partial-coverage risk."""
    pytester.makepyfile(
        test_mapped=(
            "def test_a_broken():\n    assert False\n"
            "def test_b():\n    assert True\n"
        )
    )
    _run_emitting(
        pytester, evidence_out, "--emit-evidence", "--build", TEST_BUILD, "-x"
    )
    assert not evidence_out.exists()


def test_exclude_narrows_a_broad_pattern(pytester, evidence_out):
    """A negative-path test carved out of a module glob emits no evidence."""
    map_yaml = """
- pattern: "test_mapped.py::*"
  capability_ids: [workrooms.create]
  scenario_name: "Mapped scenario"
  exclude:
    - "*::test_get_nonexistent*"
"""
    pytester.makepyfile(
        test_mapped=(
            "def test_real_work():\n    assert True\n"
            "def test_get_nonexistent_thing():\n    assert True\n"
        )
    )
    _run_emitting(
        pytester,
        evidence_out,
        "--emit-evidence",
        "--build",
        TEST_BUILD,
        map_yaml=map_yaml,
    )
    (record,) = _records(evidence_out)
    assert [s["name"] for s in record["steps"]] == ["test_mapped.py::test_real_work"]


def test_excluded_test_alone_emits_nothing(pytester, evidence_out):
    """Codex's targeted-run case: running only the excluded test claims nothing."""
    map_yaml = """
- pattern: "test_mapped.py::*"
  capability_ids: [workrooms.create]
  scenario_name: "Mapped scenario"
  exclude:
    - "*::test_get_nonexistent*"
"""
    pytester.makepyfile(
        test_mapped=(
            "def test_real_work():\n    assert True\n"
            "def test_get_nonexistent_thing():\n    assert True\n"
        )
    )
    _run_emitting(
        pytester,
        evidence_out,
        "--emit-evidence",
        "--build",
        TEST_BUILD,
        "-k",
        "nonexistent",
        map_yaml=map_yaml,
    )
    assert not evidence_out.exists()


def test_refusal_without_build_identity(pytester, evidence_out, monkeypatch):
    monkeypatch.delenv("KAMIWAZA_BUILD", raising=False)
    pytester.makepyfile(test_mapped="def test_ok():\n    assert True\n")
    result = _run_emitting(pytester, evidence_out, "--emit-evidence")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*build identity*"])
    assert not evidence_out.exists()


# ---------------------------------------------------------------------------
# Capability-map loader
# ---------------------------------------------------------------------------


def test_loader_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        emitter.load_capability_map(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    "body", ["pattern: oops\n", "{}\n", "0\n", "false\n"], ids=lambda b: b.strip()
)
def test_loader_rejects_non_list(tmp_path, body):
    """Falsey scalars/mappings are malformed maps, not 'no entries'."""
    path = tmp_path / "map.yaml"
    path.write_text(body)
    with pytest.raises(ValueError, match="must be a list"):
        emitter.load_capability_map(path)


@pytest.mark.parametrize("body", ["", "[]\n"], ids=["empty-file", "empty-list"])
def test_loader_rejects_empty_map(tmp_path, body):
    """An opted-in run against an empty map can never produce evidence."""
    path = tmp_path / "map.yaml"
    path.write_text(body)
    with pytest.raises(ValueError, match="contains no entries"):
        emitter.load_capability_map(path)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "- pattern: 'x::*'\n  capability_ids: [workrooms.create]\n"
            "  scenario_name: 'A'\n  extra: nope\n",
            id="unknown-key",
        ),
        pytest.param(
            "- pattern: 'x::*'\n  capability_ids: [workrooms.create]\n",
            id="missing-required-key",
        ),
    ],
)
def test_loader_rejects_unknown_or_missing_keys(tmp_path, body):
    path = tmp_path / "map.yaml"
    path.write_text(body)
    with pytest.raises(ValueError, match="exactly the keys"):
        emitter.load_capability_map(path)


def test_loader_rejects_scenario_name_slugging_to_empty(tmp_path):
    """A non-empty name of only non-ASCII characters yields no scenario_id."""
    path = tmp_path / "map.yaml"
    path.write_text(
        "- pattern: 'x::*'\n  capability_ids: [workrooms.create]\n"
        "  scenario_name: '日本語'\n"
    )
    with pytest.raises(ValueError, match="empty scenario_id"):
        emitter.load_capability_map(path)


def test_loader_rejects_malformed_yaml_as_value_error(tmp_path):
    """A YAML syntax error must reach callers as the documented ValueError."""
    path = tmp_path / "map.yaml"
    path.write_text("- pattern: 'x::*'\n   bad: [unclosed\n")
    with pytest.raises(ValueError, match="invalid YAML"):
        emitter.load_capability_map(path)


def test_malformed_yaml_map_refuses_with_usage_error(pytester, evidence_out):
    """End to end: a broken map is a clean refusal, not a configure traceback."""
    pytester.makepyfile(test_mapped="def test_ok():\n    assert True\n")
    result = _run_emitting(
        pytester,
        evidence_out,
        "--emit-evidence",
        "--build",
        TEST_BUILD,
        map_yaml="- pattern: 'x::*'\n   bad: [unclosed\n",
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*invalid YAML*"])
    assert not evidence_out.exists()


def test_loader_accepts_and_applies_exclude(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text(
        "- pattern: 'tests/x.py::*'\n  capability_ids: [workrooms.create]\n"
        "  scenario_name: 'A'\n  exclude: ['*::test_nope']\n"
    )
    (entry,) = emitter.load_capability_map(path)
    assert entry.exclude == ("*::test_nope",)
    assert entry.matches(SimpleNamespace(nodeid="tests/x.py::test_yes"))
    assert not entry.matches(SimpleNamespace(nodeid="tests/x.py::test_nope"))


def test_loader_rejects_non_list_exclude(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text(
        "- pattern: 'x::*'\n  capability_ids: [workrooms.create]\n"
        "  scenario_name: 'A'\n  exclude: 'oops'\n"
    )
    with pytest.raises(ValueError, match="exclude must be a list"):
        emitter.load_capability_map(path)


def test_loader_rejects_empty_exclude_glob(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text(
        "- pattern: 'x::*'\n  capability_ids: [workrooms.create]\n"
        "  scenario_name: 'A'\n  exclude: ['']\n"
    )
    with pytest.raises(ValueError, match=r"exclude\[0\]"):
        emitter.load_capability_map(path)


def test_loader_rejects_malformed_capability_id(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text(
        "- pattern: 'x::*'\n  capability_ids: ['Not Valid!']\n" "  scenario_name: 'A'\n"
    )
    with pytest.raises(ValueError, match="kebab-case"):
        emitter.load_capability_map(path)


def test_loader_rejects_empty_capability_ids(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text("- pattern: 'x::*'\n  capability_ids: []\n  scenario_name: 'A'\n")
    with pytest.raises(ValueError, match="non-empty list"):
        emitter.load_capability_map(path)


def test_loader_rejects_duplicate_scenario_slugs(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text(
        "- pattern: 'x::*'\n  capability_ids: [workrooms.create]\n"
        "  scenario_name: 'Same Name'\n"
        "- pattern: 'y::*'\n  capability_ids: [workrooms.create]\n"
        "  scenario_name: 'same name'\n"
    )
    with pytest.raises(ValueError, match="slugs to"):
        emitter.load_capability_map(path)


def test_slugify_is_stable_kebab_case():
    assert emitter.slugify("Model discovery, metadata, and hub download") == (
        "model-discovery-metadata-and-hub-download"
    )
    assert emitter.slugify("  Weird -- Name!  ") == "weird-name"


def _collect_repo_nodeids() -> list[str]:
    """Nodeids pytest actually collects for the files the shipped map names."""
    entries = emitter.load_capability_map(emitter.DEFAULT_MAP_PATH)
    files = sorted(
        {
            e.pattern.split("::", 1)[0]
            for e in entries
            if not e.pattern.startswith(emitter.MARKER_PREFIX)
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            *files,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # A collection error must fail loudly: silently losing a module's nodeids
    # would make every glob in its entries vacuously "unverifiable" below.
    # Marker deselection still exits 0, so this does not fire on a host that
    # merely lacks a second cluster.
    assert proc.returncode == 0, (
        f"pytest --collect-only failed (rc={proc.returncode}); cannot validate "
        f"the map.\nstdout tail:\n{proc.stdout[-2000:]}\n"
        f"stderr tail:\n{proc.stderr[-2000:]}"
    )
    return [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    ]


def _stale_globs(entry: emitter.MapEntry, nodeids: list[str]) -> list[str]:
    """Globs in one entry that no longer match anything they should."""
    matched = [n for n in nodeids if fnmatchcase(n, entry.pattern)]
    if not matched:
        # A pattern matching nothing is stale only if its file still collected
        # tests; a wholly marker-deselected file (the two-cluster suite on a
        # single-cluster host) legitimately contributes none.
        file_part = entry.pattern.split("::", 1)[0]
        if any(n.startswith(f"{file_part}::") for n in nodeids):
            return [
                f"{entry.scenario_name!r}: pattern {entry.pattern!r} matches nothing"
            ]
        return []
    return [
        f"{entry.scenario_name!r}: exclude {glob!r} matches nothing"
        for glob in entry.exclude
        if not any(fnmatchcase(n, glob) for n in matched)
    ]


def test_shipped_map_globs_still_match_real_nodeids():
    """Every pattern and carve-out in the shipped map must still bite.

    The ``exclude`` globs are the whole defense against a targeted run
    claiming a capability it never exercised, and a live ``pattern`` is what
    makes an entry produce evidence at all. Either can be voided by a rename
    with every other test still green, so pin both against a real collection.
    """
    nodeids = _collect_repo_nodeids()
    assert nodeids, "collection produced no nodeids; cannot validate the map"

    stale: list[str] = []
    for entry in emitter.load_capability_map(emitter.DEFAULT_MAP_PATH):
        stale.extend(_stale_globs(entry, nodeids))
    assert not stale, "stale globs in capability_map.yaml: " + "; ".join(stale)


def test_repo_capability_map_is_valid_and_references_real_files():
    """The shipped map loads, and every nodeid pattern names a real file."""
    entries = emitter.load_capability_map(emitter.DEFAULT_MAP_PATH)
    assert entries, "shipped capability map must not be empty"
    for entry in entries:
        if entry.pattern.startswith(emitter.MARKER_PREFIX):
            continue
        file_part = entry.pattern.split("::", 1)[0]
        assert "*" not in file_part, entry.pattern
        assert (
            REPO_ROOT / file_part
        ).is_file(), f"capability_map.yaml pattern references missing file: {file_part}"


# ---------------------------------------------------------------------------
# Outcome folding (setup/call/teardown composition)
# ---------------------------------------------------------------------------


def _report(*, failed=False, skipped=False, duration=0.1, when="call"):
    return SimpleNamespace(
        failed=failed,
        skipped=skipped,
        duration=duration,
        when=when,
        passed=not (failed or skipped),
    )


def test_outcome_fold_failure_wins_over_later_phases():
    outcome = emitter._TestOutcome()
    outcome.fold(_report(when="setup"))  # setup passed
    outcome.fold(_report(failed=True))  # call failed
    outcome.fold(_report(when="teardown"))  # teardown passed
    assert outcome.status == "failed"
    assert outcome.duration_s == pytest.approx(0.3)


def test_outcome_fold_skip_does_not_mask_failure():
    outcome = emitter._TestOutcome()
    outcome.fold(_report(failed=True))
    outcome.fold(_report(skipped=True))
    assert outcome.status == "failed"


def test_outcome_fold_setup_skip_marks_skipped():
    outcome = emitter._TestOutcome()
    outcome.fold(_report(skipped=True, when="setup"))
    outcome.fold(_report(when="teardown"))
    assert outcome.status == "skipped"


# ---------------------------------------------------------------------------
# Completion tracking: only terminal reports make a test count as evidence
# ---------------------------------------------------------------------------


def test_outcome_passing_setup_alone_is_incomplete():
    """The mid-test-death shape: setup passed, call never reported."""
    outcome = emitter._TestOutcome()
    outcome.fold(_report(when="setup"))
    assert outcome.status == "passed"  # default status is still affirmative...
    assert outcome.complete is False  # ...so completion is what gates emission


def test_outcome_call_report_completes():
    outcome = emitter._TestOutcome()
    outcome.fold(_report(when="setup"))
    outcome.fold(_report(when="call"))
    assert outcome.complete is True


@pytest.mark.parametrize("kwargs", [{"failed": True}, {"skipped": True}])
def test_outcome_terminal_setup_completes(kwargs):
    """A failed or skipped setup settles the test — no call phase follows."""
    outcome = emitter._TestOutcome()
    outcome.fold(_report(when="setup", **kwargs))
    assert outcome.complete is True


def test_incomplete_outcome_contributes_no_step():
    entry = emitter.MapEntry(
        pattern="test_x.py::*",
        scenario_name="X",
        capability_ids=("workrooms.create",),
    )
    plugin = emitter.EvidenceEmitterPlugin(
        entries=[entry], build=TEST_BUILD, out_dir=Path("unused")
    )
    plugin._matched = {"test_x.py::test_a": [entry], "test_x.py::test_b": [entry]}
    plugin._outcomes = {
        "test_x.py::test_a": emitter._TestOutcome(status="passed", complete=True),
        "test_x.py::test_b": emitter._TestOutcome(status="passed", complete=False),
    }
    assert [s.name for s in plugin._steps_for(entry)] == ["test_x.py::test_a"]
