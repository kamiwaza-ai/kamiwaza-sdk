"""The documentation gate for published operations.

`ruff`'s pydocstyle rules enforce that a docstring exists and is shaped
correctly, but they cannot express "the summary line is too thin to choose
between operations on". That half of FR-006i lives here.

Both numbers are ceilings, and they only ever come down. Raising one to land a
change is the one edit this file forbids: see AGENTS.md, "Agent tools contract".
Lowering one as T116 lands is the expected edit, and a run that beats a ceiling
fails loudly so the ceiling is tightened rather than quietly enjoyed.
"""

from __future__ import annotations

import warnings

import pytest

from kamiwaza_sdk.agent_tools.descriptors import description_coverage
from kamiwaza_sdk.agent_tools.spec_index import build_index

pytestmark = pytest.mark.unit

#: Published operations with no description from any source. Target: 0.
#:
#: **Zero, and it stays zero.** Every one of the 332 published operations now
#: resolves a description from some source. A ceiling of 0 means the next
#: operation added without one fails this test rather than shipping nameless.
#:
#: History, because a ceiling that moved needs one: 41 at the first
#: measurement; 69 when the index began walking nested platform sub-clients and
#: made 38 previously unreachable operations callable — the one legitimate
#: reason to raise a ceiling, being new territory rather than a regression; 38
#: after the nested families and the catalogue facade; 0 after `context`,
#: `auth`, `retrieval` and `serving`.
MAX_ABSENT = 0

#: Published operations whose description is under six words — present, but too
#: short to distinguish one operation from another in a ranked list. Target: 0.
#: 85 at the first measurement, 88 after the nested families arrived, 83 after
#: the first documentation slice, now 65. The remainder are one-line docstrings
#: and OpenAPI title-case summaries across `cluster` (11), `prompts` (8),
#: `ingestion`, `models` and `serving` (5 each), and a long tail below that.
MAX_THIN = 65


@pytest.fixture(scope="module")
def coverage():
    from kamiwaza_sdk.client import KamiwazaClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = KamiwazaClient(base_url="http://localhost:7777/api")
    return description_coverage(build_index(client), client)


def test_no_new_operation_ships_without_a_description(coverage) -> None:
    absent = coverage["absent"]
    assert absent <= MAX_ABSENT, (
        f"{absent} published operations have no description, above the ceiling "
        f"of {MAX_ABSENT}. Write the docstring; do not raise the ceiling."
    )


def test_no_new_operation_ships_with_a_thin_description(coverage) -> None:
    thin = coverage["thin"]
    assert thin <= MAX_THIN, (
        f"{thin} published operations have a description under six words, above "
        f"the ceiling of {MAX_THIN}. Write a real summary line; do not raise "
        f"the ceiling."
    )


def test_ceilings_are_tightened_when_they_are_beaten(coverage) -> None:
    """A ceiling left slack after the work lands stops being a gate.

    This fails on *improvement*, which is deliberate: the fix is a one-line
    edit to the constant in the same change that earned it.
    """
    assert coverage["absent"] == MAX_ABSENT, (
        f"absent is {coverage['absent']}, ceiling is {MAX_ABSENT}. Lower "
        f"MAX_ABSENT to {coverage['absent']}."
    )
    assert coverage["thin"] == MAX_THIN, (
        f"thin is {coverage['thin']}, ceiling is {MAX_THIN}. Lower MAX_THIN to "
        f"{coverage['thin']}."
    )
