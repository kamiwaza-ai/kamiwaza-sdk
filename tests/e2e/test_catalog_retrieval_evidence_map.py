"""Evidence-map guard for T01, the catalog ingest-to-retrieval tests.

An evidence record is composed from whichever mapped tests actually ran, so a
claim is honest only when each test earns it on its own (the PER-TEST RULE in
capability_map.yaml). The inline test proves catalog registration, readback
and deletion and a completed inline retrieval job, so it feeds both claims. The
gRPC test is the only one that exercises Arrow Flight and feeds the retrieval
claim through its own entry, so a Flight failure yields its own failed record
instead of disappearing into, or failing, the inline record.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.e2e import _evidence_emitter as emitter
from tests.integration import test_catalog_ingest_retrieval as t01_module

pytestmark = pytest.mark.unit

T01_MODULE = "tests/integration/test_catalog_ingest_retrieval.py"
EXPECTED_CLAIMS = {
    "test_s3_ingest_and_retrieve_inline": (
        "catalog.dataset-registry",
        "retrieval.async-retrieval-jobs",
    ),
    "test_s3_ingest_and_retrieve_grpc": ("retrieval.async-retrieval-jobs",),
}
# A skipped or xfailed step never evidences anything (the emitter records an
# xfail as skipped), so a known product failure must stay a plain failure.
# Only decorator markers are checked here; a runtime pytest.skip or
# pytest.xfail inside a test body is not detected.
OUTCOME_CHANGING_MARKERS = frozenset({"skip", "skipif", "xfail"})


def _marks(test_name: str) -> list[pytest.Mark]:
    """The test's real markers; resolving it by name fails loudly on a rename."""
    test_fn = getattr(t01_module, test_name)
    module_marks = [decorator.mark for decorator in t01_module.pytestmark]
    return [*module_marks, *getattr(test_fn, "pytestmark", [])]


def _matching_entries(test_name: str) -> list[emitter.MapEntry]:
    marks = _marks(test_name)

    def get_closest_marker(name: str) -> pytest.Mark | None:
        return next((mark for mark in marks if mark.name == name), None)

    item = SimpleNamespace(
        nodeid=f"{T01_MODULE}::{test_name}", get_closest_marker=get_closest_marker
    )
    entries = emitter.load_capability_map(emitter.DEFAULT_MAP_PATH)
    return [entry for entry in entries if entry.matches(item)]


@pytest.mark.parametrize("test_name", sorted(EXPECTED_CLAIMS))
def test_t01_test_feeds_exactly_its_own_claim(test_name: str) -> None:
    matching = _matching_entries(test_name)

    assert [entry.capability_ids for entry in matching] == [
        EXPECTED_CLAIMS[test_name]
    ], (
        f"{test_name} must feed exactly one entry claiming "
        f"{EXPECTED_CLAIMS[test_name]}; found "
        f"{[(entry.scenario_name, entry.capability_ids) for entry in matching]}"
    )
    (entry,) = matching
    assert entry.evidence_provenance == "cycle-authored", (
        f"{entry.scenario_name!r} is stamped {entry.evidence_provenance!r}; "
        "the T01 tests were written in-cycle to evidence the capability"
    )


@pytest.mark.parametrize("test_name", sorted(EXPECTED_CLAIMS))
def test_t01_test_carries_no_outcome_changing_marker(test_name: str) -> None:
    found = sorted(
        mark.name for mark in _marks(test_name) if mark.name in OUTCOME_CHANGING_MARKERS
    )
    assert not found, f"{test_name} carries {found}; report failures as failures"
