"""Tests for the opt-in scenario-evidence.v2 emitter (ENG-10026, T3.7).

Pins the plugin contract: no behavior without ``--emit-evidence``; refusal
without a build identity; explicit-map-only matching (unmapped tests emit
nothing, unrun entries emit nothing); one conforming, schema-validated
``scenario-evidence.v2`` record per map entry whose tests ran, with
``evidence_provenance: "pre-existing"`` and harness ``derive_status``
composition (all passed → passed, skips → passed_with_notes, any failure
→ failed).

End-to-end cases run pytest in-process via ``pytester`` with a fixture
capability map and simulated test results; loader and outcome-folding
semantics are pinned by direct unit tests.
"""

from __future__ import annotations

import json
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


def _run_emitting(pytester, evidence_out, *extra_args, map_yaml=MAP_ONE_ENTRY):
    """Run the inner suite with evidence emission wired up."""
    pytester.makeconftest(INNER_CONFTEST)
    map_path = pytester.path / "capability_map.yaml"
    map_path.write_text(map_yaml)
    return pytester.runpytest_inprocess(
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


def test_loader_rejects_non_list(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text("pattern: oops\n")
    with pytest.raises(ValueError, match="must be a list"):
        emitter.load_capability_map(path)


def test_loader_rejects_unknown_or_missing_keys(tmp_path):
    path = tmp_path / "map.yaml"
    path.write_text(
        "- pattern: 'x::*'\n  capability_ids: [workrooms.create]\n"
        "  scenario_name: 'A'\n  extra: nope\n"
    )
    with pytest.raises(ValueError, match="exactly the keys"):
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


def _report(*, failed=False, skipped=False, duration=0.1):
    return SimpleNamespace(failed=failed, skipped=skipped, duration=duration)


def test_outcome_fold_failure_wins_over_later_phases():
    outcome = emitter._TestOutcome()
    outcome.fold(_report())  # setup passed
    outcome.fold(_report(failed=True))  # call failed
    outcome.fold(_report())  # teardown passed
    assert outcome.status == "failed"
    assert outcome.duration_s == pytest.approx(0.3)


def test_outcome_fold_skip_does_not_mask_failure():
    outcome = emitter._TestOutcome()
    outcome.fold(_report(failed=True))
    outcome.fold(_report(skipped=True))
    assert outcome.status == "failed"


def test_outcome_fold_setup_skip_marks_skipped():
    outcome = emitter._TestOutcome()
    outcome.fold(_report(skipped=True))
    outcome.fold(_report())
    assert outcome.status == "skipped"
