"""Fail-closed coverage tests for target/scenario/case evidence."""

from __future__ import annotations

import pytest

from kamiwaza_sdk.validation import (
    CaseResult,
    ResolvedScenario,
    ScenarioEvidence,
    ScenarioPlan,
    evaluate_coverage,
    model_digest,
)

pytestmark = pytest.mark.contract


def _plan() -> ScenarioPlan:
    return ScenarioPlan(
        schema="kamiwaza.scenario-plan/v1",
        profile_digest="sha256:" + "1" * 64,
        provider_revision="sdk-validation@abc123",
        selected=(
            ResolvedScenario(
                target_id="evo-x2-2-llamacpp-chat",
                scenario_id="sdk.inference.lifecycle/v1",
                required=True,
                case_ids=("catalog-discovery", "openai-multi-turn-chat"),
                redacted_parameters={"engine": "llamacpp"},
            ),
        ),
        install_requirements={},
        runtime_requirements=("cluster-api",),
    )


def _evidence(plan: ScenarioPlan, results: tuple[CaseResult, ...]) -> ScenarioEvidence:
    return ScenarioEvidence(
        schema="kamiwaza.scenario-evidence/v1",
        provider_revision=plan.provider_revision,
        profile_digest=plan.profile_digest,
        plan_digest=model_digest(plan),
        results=results,
        resolved_runtime={},
    )


def _result(case_id: str, *, target_id: str = "evo-x2-2-llamacpp-chat") -> CaseResult:
    return CaseResult(
        target_id=target_id,
        scenario_id="sdk.inference.lifecycle/v1",
        case_id=case_id,
        status="passed",
        duration_ms=1,
        detail=None,
    )


def test_exact_case_evidence_passes_only_when_every_planned_cell_passes() -> None:
    plan = _plan()
    evidence = _evidence(
        plan,
        (_result("catalog-discovery"), _result("openai-multi-turn-chat")),
    )

    coverage = evaluate_coverage(plan, evidence)

    assert coverage.status == "passed"
    assert coverage.issues == ()


@pytest.mark.parametrize(
    ("results", "issue_codes"),
    [
        ((_result("catalog-discovery"),), {"missing_case"}),
        (
            (
                _result("catalog-discovery"),
                _result("catalog-discovery"),
                _result("openai-multi-turn-chat"),
            ),
            {"duplicate_case"},
        ),
        (
            (
                _result("catalog-discovery"),
                _result("openai-multi-turn-chat"),
                _result("unplanned-case"),
            ),
            {"unexpected_case"},
        ),
        (
            (
                _result("catalog-discovery", target_id="wrong-target"),
                _result("openai-multi-turn-chat"),
            ),
            {"missing_case", "unexpected_case"},
        ),
    ],
)
def test_missing_duplicate_unexpected_and_wrong_target_evidence_fail_closed(
    results: tuple[CaseResult, ...], issue_codes: set[str]
) -> None:
    plan = _plan()

    coverage = evaluate_coverage(plan, _evidence(plan, results))

    assert coverage.status == "failed"
    assert {issue.code for issue in coverage.issues} == issue_codes


@pytest.mark.parametrize(
    ("status", "detail", "expected_issue"),
    [
        ("skipped", "deployment unavailable", "required_skip"),
        ("failed", "empty assistant response", "required_failure"),
    ],
)
def test_nonpassing_required_case_is_a_failed_coverage_cell(
    status: str, detail: str, expected_issue: str
) -> None:
    plan = _plan()
    nonpassing = _result("openai-multi-turn-chat").model_copy(
        update={"status": status, "detail": detail}
    )

    coverage = evaluate_coverage(
        plan,
        _evidence(plan, (_result("catalog-discovery"), nonpassing)),
    )

    assert coverage.status == "failed"
    assert [issue.code for issue in coverage.issues] == [expected_issue]


def test_evidence_from_a_different_plan_revision_fails_closed() -> None:
    plan = _plan()
    evidence = _evidence(
        plan,
        (_result("catalog-discovery"), _result("openai-multi-turn-chat")),
    ).model_copy(update={"provider_revision": "sdk-validation@different"})

    coverage = evaluate_coverage(plan, evidence)

    assert coverage.status == "failed"
    assert [issue.code for issue in coverage.issues] == ["metadata_mismatch"]
