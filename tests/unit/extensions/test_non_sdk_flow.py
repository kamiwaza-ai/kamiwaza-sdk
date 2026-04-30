"""Contract tests for the non-SDK extension flow doc and canonical test vectors.

Issue: ENG-3891 / D210 M2 / Tasks T2.7 + T2.8.
Scenarios: TS-M2-16, TS-M2-17.

These tests pin the structure of the public contract that non-Python/TS extension
authors consume. The flow doc and test-vectors.json are loaded by the Python
runtime lib (ENG-3892), the TypeScript runtime lib (ENG-3893), and the Go
reference (ENG-3894); divergence here cascades.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Locate paths relative to repo root regardless of where pytest is invoked.
REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = REPO_ROOT / "docs" / "extensions" / "non-sdk-flow.md"
VECTORS_PATH = REPO_ROOT / "docs" / "extensions" / "non-sdk-flow" / "test-vectors.json"


# Per design §4.2.11: 8 mandated sections in the canonical contract doc.
EXPECTED_SECTION_HEADINGS = (
    "Runtime contract",
    "Envelope headers",
    "Identity parsing",
    "Model-access pattern",
    "Failure semantics",
    "Packaging",
    "Publishing",
    "Trust boundary and known gaps",
)


# Per design §4.2.11 revised 2026-04-23: extensions don't verify HMAC.
#
# The doc may *mention* HMAC in order to explain the decision (§8 "Trust
# boundary and known gaps") and to tell readers to ignore the signature pair.
# What it must not do is teach the verification *algorithm* — that would be
# a regression to pre-2026-04-23 design. Forbid imperative phrasing.
HMAC_FORBIDDEN_INSTRUCTIONS = (
    "verify the signature",
    "verify the HMAC",
    "verify HMAC",
    "compute HMAC",
    "compute the signature",
    "KAMIWAZA_ENVELOPE_HMAC_KEY",
)


class TestNonSDKFlowDoc:
    """TS-M2-16: non-sdk-flow.md exists with 8 mandated sections."""

    def test_doc_exists(self):
        assert DOC_PATH.exists(), (
            f"Expected canonical non-SDK flow doc at {DOC_PATH.relative_to(REPO_ROOT)}"
        )

    def test_doc_has_all_eight_sections(self):
        text = DOC_PATH.read_text()
        missing = [h for h in EXPECTED_SECTION_HEADINGS if h not in text]
        assert not missing, (
            f"non-sdk-flow.md missing expected section headings: {missing}. "
            f"Per §4.2.11 the doc must cover all 8 of {list(EXPECTED_SECTION_HEADINGS)}."
        )

    def test_doc_does_not_teach_hmac_verification(self):
        # The §4.4.2 revision (2026-04-23) explicitly removed verify-in-extension.
        # The doc may *explain* that extensions don't verify HMAC (the §8 known-gaps
        # section depends on saying so). What it must not do is teach the
        # verification algorithm — case-insensitive match on imperative phrasing.
        text = DOC_PATH.read_text().lower()
        leaked = [t for t in HMAC_FORBIDDEN_INSTRUCTIONS if t.lower() in text]
        assert not leaked, (
            f"non-sdk-flow.md teaches HMAC verification — §4.4.2 (revised 2026-04-23) "
            f"removed crypto from extension scope. Imperative phrases found: {leaked}."
        )

    def test_doc_references_misbound_auth_failure_class(self):
        # AC4: missing X-User-Id / X-Workroom-Id raises misbound_auth.
        # The doc must teach this.
        text = DOC_PATH.read_text()
        assert "misbound_auth" in text, (
            "Doc must reference the misbound_auth failure class — that's the "
            "outcome non-SDK authors need to surface on missing envelope."
        )


class TestCanonicalTestVectors:
    """TS-M2-17: test-vectors.json has 4 cases with required fields."""

    REQUIRED_CASES = ("happy-path", "missing-user-id", "missing-workroom", "global-workroom-sentinel")

    @pytest.fixture(scope="class")
    def vectors(self):
        assert VECTORS_PATH.exists(), (
            f"Expected canonical test vectors at {VECTORS_PATH.relative_to(REPO_ROOT)}"
        )
        return json.loads(VECTORS_PATH.read_text())

    def test_vectors_is_a_list(self, vectors):
        assert isinstance(vectors, list), (
            "test-vectors.json must be a top-level JSON array (consumed by Python, "
            "TS, and Go test suites)."
        )

    def test_vectors_have_all_four_required_cases(self, vectors):
        names = {v["case"] for v in vectors}
        missing = [c for c in self.REQUIRED_CASES if c not in names]
        assert not missing, (
            f"test-vectors.json missing required cases: {missing}. "
            f"Required: {list(self.REQUIRED_CASES)}, found: {sorted(names)}."
        )

    def test_each_vector_has_case_and_headers(self, vectors):
        for v in vectors:
            assert "case" in v and isinstance(v["case"], str), v
            assert "headers" in v and isinstance(v["headers"], dict), v

    def test_each_vector_has_either_expected_or_should_fail(self, vectors):
        for v in vectors:
            has_expected = "expected_identity" in v
            has_failure = "should_fail_class" in v
            assert has_expected ^ has_failure, (
                f"Vector {v.get('case')!r} must have exactly one of "
                f"'expected_identity' or 'should_fail_class'."
            )

    def test_happy_path_vector_has_required_identity_fields(self, vectors):
        happy = next(v for v in vectors if v["case"] == "happy-path")
        identity = happy["expected_identity"]
        for field in ("user_id", "email", "workroom_id", "workroom_role", "roles"):
            assert field in identity, (
                f"happy-path vector missing identity field {field!r}; "
                f"present: {sorted(identity.keys())}"
            )

    def test_missing_envelope_vectors_fail_with_misbound_auth(self, vectors):
        for case in ("missing-user-id", "missing-workroom"):
            v = next(x for x in vectors if x["case"] == case)
            assert v.get("should_fail_class") == "misbound_auth", (
                f"Vector {case!r} must fail with class 'misbound_auth' "
                f"(per §4.4.2 revised: missing required envelope → misbound_auth)."
            )

    def test_global_workroom_sentinel_is_all_f_uuid(self, vectors):
        v = next(x for x in vectors if x["case"] == "global-workroom-sentinel")
        wr = v["expected_identity"]["workroom_id"]
        assert wr == "ffffffff-ffff-ffff-ffff-ffffffffffff", (
            f"Global workroom sentinel must be all-f UUID; got {wr!r}."
        )
