"""Exact target/scenario/case coverage evaluation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from pydantic import BaseModel

from kamiwaza_sdk.validation.models import (
    CaseResult,
    CoverageIssue,
    CoverageIssueCode,
    CoverageSummary,
    ScenarioEvidence,
    ScenarioPlan,
)

CellKey = tuple[str, str, str]


def model_digest(model: BaseModel) -> str:
    """Return a canonical SHA-256 digest for a protocol model."""

    payload = model.model_dump(mode="json", by_alias=True, exclude_none=False)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def evaluate_coverage(
    plan: ScenarioPlan, evidence: ScenarioEvidence
) -> CoverageSummary:
    """Compare evidence with the plan's exact required cell inventory."""

    issues = _metadata_issues(plan, evidence)
    expected = _expected_cells(plan)
    actual = [_result_key(result) for result in evidence.results]
    counts = Counter(actual)
    actual_set = set(actual)

    issues.extend(_cell_issues("duplicate_case", counts, threshold=2))
    issues.extend(_missing_issues(expected, actual_set))
    issues.extend(_unexpected_issues(expected, actual_set))
    issues.extend(_result_issues(expected, evidence.results))

    return CoverageSummary(
        status="failed" if issues else "passed",
        plan_digest=model_digest(plan),
        issues=tuple(issues),
    )


def _metadata_issues(
    plan: ScenarioPlan, evidence: ScenarioEvidence
) -> list[CoverageIssue]:
    expected = (
        plan.provider_revision,
        plan.profile_digest,
        model_digest(plan),
    )
    actual = (
        evidence.provider_revision,
        evidence.profile_digest,
        evidence.plan_digest,
    )
    if actual == expected:
        return []
    return [
        CoverageIssue(
            code="metadata_mismatch",
            detail="evidence provider revision or input digest does not match plan",
        )
    ]


def _expected_cells(plan: ScenarioPlan) -> dict[CellKey, bool]:
    return {
        (scenario.target_id, scenario.scenario_id, case_id): scenario.required
        for scenario in plan.selected
        for case_id in scenario.case_ids
    }


def _result_key(result: CaseResult) -> CellKey:
    return (result.target_id, result.scenario_id, result.case_id)


def _cell_issues(
    code: CoverageIssueCode, counts: Counter[CellKey], *, threshold: int
) -> list[CoverageIssue]:
    return [
        _cell_issue(code, cell, f"case evidence occurred {count} times")
        for cell, count in sorted(counts.items())
        if count >= threshold
    ]


def _missing_issues(
    expected: dict[CellKey, bool], actual: set[CellKey]
) -> list[CoverageIssue]:
    return [
        _cell_issue("missing_case", cell, "planned case has no evidence")
        for cell in sorted(set(expected) - actual)
    ]


def _unexpected_issues(
    expected: dict[CellKey, bool], actual: set[CellKey]
) -> list[CoverageIssue]:
    return [
        _cell_issue("unexpected_case", cell, "evidence cell was not in the plan")
        for cell in sorted(actual - set(expected))
    ]


def _result_issues(
    expected: dict[CellKey, bool], results: tuple[CaseResult, ...]
) -> list[CoverageIssue]:
    issues: list[CoverageIssue] = []
    for result in results:
        cell = _result_key(result)
        if not expected.get(cell, False):
            continue
        if result.status == "failed":
            issues.append(_cell_issue("required_failure", cell, "required case failed"))
        if result.status == "skipped":
            issues.append(_cell_issue("required_skip", cell, "required case skipped"))
    return issues


def _cell_issue(code: CoverageIssueCode, cell: CellKey, detail: str) -> CoverageIssue:
    target_id, scenario_id, case_id = cell
    return CoverageIssue(
        code=code,
        target_id=target_id,
        scenario_id=scenario_id,
        case_id=case_id,
        detail=detail,
    )
