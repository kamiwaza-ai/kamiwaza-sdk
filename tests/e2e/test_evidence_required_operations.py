"""Focused regression for partial OpenAI-compatible inference evidence."""

from pathlib import Path

import pytest

from tests.e2e.test_evidence_emitter import (
    TEST_BUILD,
    _records,
    _run_emitting,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def build_identity_env(monkeypatch):
    monkeypatch.delenv("KAMIWAZA_RELEASE", raising=False)
    monkeypatch.setenv("KAMIWAZA_BUILD", TEST_BUILD)


@pytest.fixture()
def evidence_out(pytester) -> Path:
    return pytester.path / "evidence-out"


def test_unverified_required_operations_emit_failed_with_reasons(
    pytester, evidence_out
):
    """ENG-12269: a chat pass cannot establish the full inference contract."""
    map_yaml = """
- pattern: "test_mapped.py::*"
  capability_ids: [models.openai-compatible-inference]
  scenario_name: "Inference operation set"
  unverified_operations: [embeddings, transcription, image_generation]
"""
    pytester.makepyfile(test_mapped="def test_chat():\n    assert True\n")
    _run_emitting(
        pytester,
        evidence_out,
        "--emit-evidence",
        "--build",
        TEST_BUILD,
        map_yaml=map_yaml,
    )
    (record,) = _records(evidence_out)
    assert record["status"] == "failed"
    assert record["capability_ids"] == ["models.openai-compatible-inference"]
    assert [step["status"] for step in record["steps"]] == [
        "passed",
        "skipped",
        "skipped",
        "skipped",
    ]
    assert all(
        "required operation not exercised" in step["detail"]
        for step in record["steps"][1:]
    )
