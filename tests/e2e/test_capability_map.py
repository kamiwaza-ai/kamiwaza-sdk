"""Guards on the *shipped* capability map (ENG-10026, T3.7).

Separate from ``test_evidence_emitter.py`` on purpose: those tests pin the
plugin's runtime behavior against synthetic maps and simulated results, while
these validate the real ``capability_map.yaml`` against the repo's actual test
collection. Different subject, different inputs, no shared state.

What these pin is the map's load-bearing property: an entry emits evidence
composed from whichever of its matched tests ran, so a stale ``pattern``
silently stops an entry from ever emitting, and a stale ``exclude`` silently
re-admits a test the carve-out existed to keep out. Either can be voided by an
ordinary rename with every other test in the suite still green.
"""

from __future__ import annotations

import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from tests.e2e import _evidence_emitter as emitter

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


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
        return _unmatched_pattern_problems(entry, nodeids)
    return [
        f"{entry.scenario_name!r}: exclude {glob!r} matches nothing"
        for glob in entry.exclude
        if not any(fnmatchcase(n, glob) for n in matched)
    ]


def _unmatched_pattern_problems(
    entry: emitter.MapEntry, nodeids: list[str]
) -> list[str]:
    """Diagnose an entry whose pattern matched no collected nodeid."""
    file_part = entry.pattern.split("::", 1)[0]
    if any(n.startswith(f"{file_part}::") for n in nodeids):
        # The file collected tests, so the pattern itself has gone stale.
        return [f"{entry.scenario_name!r}: pattern {entry.pattern!r} matches nothing"]
    if entry.exclude:
        # Nothing collected, so this entry's carve-outs cannot be checked here.
        # That is only safe while it has none: an unverifiable `exclude` could
        # rot in the false-evidence direction, unseen by this guard.
        return [
            f"{entry.scenario_name!r}: file {file_part!r} collected no tests, "
            f"so its {len(entry.exclude)} exclude glob(s) cannot be verified "
            "here — run on a host where this file collects, or drop them"
        ]
    return []


def test_shipped_map_globs_still_match_real_nodeids():
    """Every pattern and carve-out in the shipped map must still bite.

    One known gap remains, in the safe direction (missing evidence, never
    false evidence): ``marker:`` patterns are not checked at all, since a
    marker match cannot be resolved from nodeids alone. A wholly deselected
    file is handled explicitly — its pattern cannot be checked, so the entry
    is required to carry no ``exclude`` that would go unverified.
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


def test_engine_matrix_chat_smoke_does_not_claim_full_inference():
    """ENG-12269: one chat path cannot prove all declared inference operations."""
    entries = emitter.load_capability_map(emitter.DEFAULT_MAP_PATH)
    matrix_entries = [
        entry for entry in entries if "test_inference_matrix_live.py" in entry.pattern
    ]
    assert len(matrix_entries) == 2
    local = next(
        entry
        for entry in matrix_entries
        if entry.capability_ids == ("models.local-deployment",)
    )
    inference = next(
        entry
        for entry in matrix_entries
        if entry.capability_ids == ("models.openai-compatible-inference",)
    )
    assert local.pattern.endswith("::test_deploy_and_infer_llamacpp_gguf")
    assert inference.unverified_operations == (
        "embeddings",
        "transcription",
        "image_generation",
    )


def test_warm_model_and_unbound_workroom_tests_do_not_claim_full_capabilities():
    """ENG-12269: these tests omit acquisition and session binding, respectively."""
    entries = emitter.load_capability_map(emitter.DEFAULT_MAP_PATH)
    forbidden = {
        "models.discover-and-download": (
            "test_models_live.py",
            "test_model_files_live.py",
            "test_models_extended_live.py",
        ),
        "workrooms.session-scoping": ("test_workroom_isolation_live.py",),
    }
    for capability_id, file_names in forbidden.items():
        assert not any(
            capability_id in entry.capability_ids
            for entry in entries
            if any(file_name in entry.pattern for file_name in file_names)
        )


def test_partial_sdk_scenarios_cannot_pass_full_openai_inference():
    """ENG-12269: the one inference mapping carries all unverified operations."""
    entries = emitter.load_capability_map(emitter.DEFAULT_MAP_PATH)
    inference = [
        entry
        for entry in entries
        if "models.openai-compatible-inference" in entry.capability_ids
    ]
    assert len(inference) == 1
    assert inference[0].unverified_operations
