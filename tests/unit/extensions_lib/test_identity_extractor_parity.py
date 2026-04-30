"""Parity test: Python ``extract_identity`` consumes the canonical test vectors.

Issue: ENG-3892 / D210 M2 / Task T2.9.
Scenarios: TS-M2-18..21.

The same JSON file (``docs/extensions/non-sdk-flow/test-vectors.json``) is
consumed bit-identically by:

* this Python parity test
* the TypeScript runtime-lib parity test (ENG-3893)
* the Go reference's extractor test — *planned*, not yet shipped (ENG-3894)

If a behavior diverges between Py and TS, both this test and the TS sibling
fail at the same case. Go consumer parity is enforced once ENG-3894 ships
the reference implementation; until then, the vectors are the bit-identical
contract Py and TS already follow.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kamiwaza_extensions_lib.errors import MisboundAuthError
from kamiwaza_extensions_lib.identity import extract_identity

REPO_ROOT = Path(__file__).resolve().parents[3]
VECTORS_PATH = REPO_ROOT / "docs" / "extensions" / "non-sdk-flow" / "test-vectors.json"

# Load once at collection time so the parametrize ids reflect the case names.
_VECTORS = json.loads(VECTORS_PATH.read_text())
_HAPPY_VECTORS = [v for v in _VECTORS if "expected_identity" in v]
_FAILURE_VECTORS = [v for v in _VECTORS if "should_fail_class" in v]


def _projected(identity, fields: list[str]) -> dict:
    """Return ``identity.model_dump()`` projected onto the keys the vector pins.

    The vector's ``expected_identity`` is the authoritative subset — extra
    fields on ``Identity`` (``is_authenticated``, etc.) are not constrained
    by the parity contract because they're language-specific niceties.
    """
    dumped = identity.model_dump()
    return {k: dumped.get(k) for k in fields}


@pytest.mark.parametrize(
    "vector",
    _HAPPY_VECTORS,
    ids=[v["case"] for v in _HAPPY_VECTORS],
)
def test_extract_identity_matches_expected(vector):
    """TS-M2-18, TS-M2-21: happy-path + global-sentinel produce expected Identity."""
    identity = extract_identity(vector["headers"])
    expected = vector["expected_identity"]
    assert _projected(identity, list(expected.keys())) == expected, (
        f"vector {vector['case']!r}: extract_identity output diverges from "
        f"the canonical expected_identity. Verify the vector and the "
        f"implementation agree on field projection."
    )


@pytest.mark.parametrize(
    "vector",
    _FAILURE_VECTORS,
    ids=[v["case"] for v in _FAILURE_VECTORS],
)
def test_extract_identity_raises_expected_failure_class(vector):
    """TS-M2-19, TS-M2-20: missing X-User-Id / X-Workroom-Id raises misbound_auth."""
    expected_class = vector["should_fail_class"]
    if expected_class == "misbound_auth":
        with pytest.raises(MisboundAuthError):
            extract_identity(vector["headers"])
    else:
        pytest.fail(
            f"vector {vector['case']!r}: unrecognized failure class "
            f"{expected_class!r}; this test only models misbound_auth today."
        )
