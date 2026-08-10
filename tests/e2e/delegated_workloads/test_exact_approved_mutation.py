"""Neutral guarded mutation, governance, ambiguity, and audit journey."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from .exact_mutation_support import run_exact_approved_mutation_journey


pytestmark = pytest.mark.e2e


def test_exact_approved_mutation_is_governed_and_never_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = run_exact_approved_mutation_journey(tmp_path, monkeypatch)

    assert outcome.success_status == 200
    assert outcome.replay_status == 403
    assert outcome.cancelled_status == 403
    assert outcome.revoked_status == 403
    assert outcome.successful_mutations == 1
    assert outcome.cancelled_mutations == 0
    assert outcome.revoked_mutations == 0
    assert outcome.ambiguous_mutations == 1
    assert outcome.ambiguous_run_status == "ambiguous"
    assert outcome.ambiguous_effect_outcome == "ambiguous"

    actions = tuple(event.action for event in outcome.audit_events)
    assert actions == (
        "approval.approved",
        "effect.consumed",
        "effect.succeeded",
        "effect.replay_denied",
        "approval.approved",
        "run.cancellation_requested",
        "effect.cancelled_authority_denied",
        "approval.approved",
        "grant.revoked",
        "effect.revoked_authority_denied",
        "approval.approved",
        "effect.consumed",
        "effect.ambiguous",
        "run.ambiguous",
    )
    encoded = str(tuple(asdict(event) for event in outcome.audit_events))
    assert "Quarterly plan" not in encoded
    assert "DPoP" not in encoded
    assert all(event.subject_id != event.workload_instance_id for event in outcome.audit_events)
