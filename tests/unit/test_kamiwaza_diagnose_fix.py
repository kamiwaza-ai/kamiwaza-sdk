"""T5.8 / ENG-4693 — kz.cluster.fix() orchestration tests.

Per design `system-design.md` §4.2.10: ``fix()`` iterates issues in
severity order; for each ``auto_fixable=True`` issue, invokes its
``fix_endpoint`` with ``fix_payload``. Issues with ``auto_fixable=False``
are surfaced but skipped with ``status="manual_required"``.

Server-side fix endpoints don't exist yet (no auto-fixable probe is
implemented in this cycle's walking skeleton). Tests verify the SDK's
generic dispatch shape — when the token-exchange / claims-scope probes
land with fix endpoints in subsequent commits, this SDK orchestration
works without changes.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_fix_reports_manual_required_for_non_auto_fixable_issues(
    httpx_mock: Any,
) -> None:
    """The skeleton-cluster case: admin-baseline issue is auto_fixable=False
    so fix() reports manual_required without hitting any endpoint."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import FixResult

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/diagnose",
        status_code=200,
        json={
            "cluster_id": "11111111-2222-3333-4444-555555555555",
            "timestamp": "2026-05-10T22:00:00+00:00",
            "issues": [
                {
                    "id": "admin_missing_baseline_rebac",
                    "severity": "error",
                    "summary": "No user holds owner",
                    "detail": {},
                    "fix_endpoint": None,
                    "fix_payload": None,
                    "auto_fixable": False,
                }
            ],
            "has_issues": True,
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.fix()

    assert isinstance(result, FixResult)
    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.issue_id == "admin_missing_baseline_rebac"
    assert outcome.status == "manual_required"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_fix_invokes_fix_endpoint_for_auto_fixable_issue(httpx_mock: Any) -> None:
    """When a probe ships an auto-fixable issue (e.g. token-exchange in a
    later commit), fix() POSTs to issue.fix_endpoint with issue.fix_payload
    and records the result. SDK is probe-type-agnostic — the dispatch is
    generic on the issue shape."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/diagnose",
        status_code=200,
        json={
            "cluster_id": "11111111-2222-3333-4444-555555555555",
            "timestamp": "2026-05-10T22:00:00+00:00",
            "issues": [
                {
                    "id": "missing_token_exchange_permission",
                    "severity": "error",
                    "summary": "OBO actor lacks token-exchange",
                    "detail": {"client_id": "kamiwaza-platform-svc"},
                    "fix_endpoint": "/cluster/diagnose/fix/missing_token_exchange_permission",
                    "fix_payload": {"client_id": "kamiwaza-platform-svc"},
                    "auto_fixable": True,
                }
            ],
            "has_issues": True,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://kamiwaza.test/api/cluster/diagnose/fix/"
            "missing_token_exchange_permission"
        ),
        status_code=200,
        json={"fixed": True},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.fix()

    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.issue_id == "missing_token_exchange_permission"
    assert outcome.status == "fixed"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_fix_records_failure_when_fix_endpoint_5xxs(httpx_mock: Any) -> None:
    """If the fix endpoint returns an error, the outcome records
    status=failed without raising — so the operator sees per-issue
    success/failure for the batch."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/diagnose",
        status_code=200,
        json={
            "cluster_id": "11111111-2222-3333-4444-555555555555",
            "timestamp": "2026-05-10T22:00:00+00:00",
            "issues": [
                {
                    "id": "missing_kamiwaza_claims_scope_attachment",
                    "severity": "error",
                    "summary": "kamiwaza-claims scope missing on platform clients",
                    "detail": {"missing_on": ["kamiwaza-platform"]},
                    "fix_endpoint": (
                        "/cluster/diagnose/fix/"
                        "missing_kamiwaza_claims_scope_attachment"
                    ),
                    "fix_payload": {},
                    "auto_fixable": True,
                }
            ],
            "has_issues": True,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://kamiwaza.test/api/cluster/diagnose/fix/"
            "missing_kamiwaza_claims_scope_attachment"
        ),
        status_code=503,
        json={"detail": "Keycloak unreachable"},
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.fix()

    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.status == "failed"
    assert outcome.error is not None
