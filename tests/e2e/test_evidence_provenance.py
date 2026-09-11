"""Where a mapped entry's evidence came from (ENG-11524).

``scenario-evidence.v2`` distinguishes evidence that pre-dates the kit cycle
from evidence authored within it, and the kit consumes that distinction --
``scripts/sdk_reference.py`` surfaces it per method and
``scripts/render_sign_off.py`` reads it.

The capability map was built to harvest coverage that already existed, so
``pre-existing`` stays the default. But an entry may name a test written
inside the cycle *to* evidence a capability, and stamping that
``pre-existing`` misreports its origin. These tests pin all three halves of
that: the declaration is honoured, the default is unchanged, and a value
outside the schema's set is refused rather than passed through.

Kept separate from ``test_evidence_emitter.py`` deliberately -- that module
pins the emitter's own contract and is already large; provenance is its own
concern, and CodeScene reads the mixture as low cohesion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.scenarios import harness
from tests.e2e.test_evidence_emitter import (
    MAP_ONE_ENTRY,
    TEST_BUILD,
    _records,
    _run_emitting,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _build_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic build identity, and no ambient KAMIWAZA_RELEASE.

    Mirrors the sibling module's fixture rather than importing it: an
    autouse fixture that arrives by import is invisible at the point it
    takes effect.
    """
    monkeypatch.delenv("KAMIWAZA_RELEASE", raising=False)
    monkeypatch.setenv("KAMIWAZA_BUILD", TEST_BUILD)


@pytest.fixture()
def evidence_out(pytester: pytest.Pytester) -> Path:
    return pytester.path / "evidence-out"


def test_entry_can_declare_cycle_authored_provenance(pytester, evidence_out):
    """A test written to evidence a capability is not "pre-existing".

    The map harvests existing coverage by default, but an entry naming a test
    authored within the cycle must be able to say so: the kit consumes this
    field, so a wrong value misreports the corpus rather than merely reading
    oddly.
    """
    pytester.makepyfile(test_mapped="def test_a():\n    pass\n")
    result = _run_emitting(
        pytester,
        evidence_out,
        "--emit-evidence",
        "--build",
        TEST_BUILD,
        map_yaml=MAP_ONE_ENTRY + "  evidence_provenance: cycle-authored\n",
    )
    result.assert_outcomes(passed=1)

    record = _records(evidence_out)[0]
    harness.validate_evidence_record(record)
    assert record["evidence_provenance"] == "cycle-authored"


def test_entry_without_a_declaration_stays_pre_existing(pytester, evidence_out):
    """The default is unchanged, so every existing entry keeps its meaning."""
    pytester.makepyfile(test_mapped="def test_a():\n    pass\n")
    result = _run_emitting(
        pytester, evidence_out, "--emit-evidence", "--build", TEST_BUILD
    )
    result.assert_outcomes(passed=1)

    record = _records(evidence_out)[0]
    assert record["evidence_provenance"] == "pre-existing"


def test_unknown_provenance_is_refused(pytester, evidence_out):
    """A value outside the schema's set is a refusal, not a silent passthrough.

    The accepted set is read from ``harness.EVIDENCE_PROVENANCES``, so this
    cannot drift from what ``validate_evidence_record`` will accept.
    """
    pytester.makepyfile(test_mapped="def test_a():\n    pass\n")
    result = _run_emitting(
        pytester,
        evidence_out,
        "--emit-evidence",
        "--build",
        TEST_BUILD,
        map_yaml=MAP_ONE_ENTRY + "  evidence_provenance: invented-yesterday\n",
    )
    result.stderr.fnmatch_lines(["*evidence_provenance must be one of*"])
    assert not evidence_out.exists()
