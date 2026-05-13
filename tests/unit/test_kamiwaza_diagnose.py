"""T5.7 / T5.8 — ENG-4692 / ENG-4693 — kz.cluster.diagnose() SDK tests.

Per design `system-design.md` §4.2.10 / §4.2.11: customer-facing diagnose
surface returns typed ClusterDiagnostics. Demo bullet (1) for WS-M2:
``kz.cluster.diagnose()`` returns clean status on a healthy cluster.

Walking-skeleton scope (matches the server skeleton): ``diagnose()`` only.
``fix()`` orchestration is deferred until at least one auto-fixable probe
exists on the server — adding it now would be a no-op customer surface.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_diagnose_returns_typed_cluster_diagnostics(httpx_mock: Any) -> None:
    """``kz.cluster.diagnose()`` GETs ``/api/cluster/diagnose`` and parses
    the structured response into a ClusterDiagnostics model."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import ClusterDiagnostics

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/diagnose",
        status_code=200,
        json={
            "cluster_id": "11111111-2222-3333-4444-555555555555",
            "timestamp": "2026-05-10T22:00:00+00:00",
            "issues": [],
            "has_issues": False,
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.diagnose()

    assert isinstance(result, ClusterDiagnostics)
    assert result.has_issues is False
    assert result.issues == []


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_diagnose_parses_structured_issues(httpx_mock: Any) -> None:
    """Issues come back as typed DiagnoseIssue models so customer code
    can match on stable issue.id strings."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import DiagnoseIssue

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
                    "summary": "No user holds owner on this cluster",
                    "detail": {"cluster_id": "11111111-2222-3333-4444-555555555555"},
                    "fix_endpoint": None,
                    "fix_payload": None,
                    "auto_fixable": False,
                }
            ],
            "has_issues": True,
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.diagnose()

    assert result.has_issues is True
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert isinstance(issue, DiagnoseIssue)
    assert issue.id == "admin_missing_baseline_rebac"
    assert issue.severity == "error"
    assert issue.auto_fixable is False


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_diagnose_allows_unknown_issue_fields_forward_compat(httpx_mock: Any) -> None:
    """Server-side schema evolution must not break a pinned SDK wheel."""
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
                    "summary": "kamiwaza-platform-svc lacks token-exchange",
                    "detail": {"client_id": "kamiwaza-platform-svc"},
                    "fix_endpoint": "/cluster/diagnose/fix/missing_token_exchange_permission",
                    "fix_payload": {},
                    "auto_fixable": True,
                    "remediation_url": "https://docs/...",  # Future field
                }
            ],
            "has_issues": True,
            "next_run_recommended_at": "2026-05-10T22:05:00+00:00",  # Future field
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.diagnose()

    assert result.issues[0].id == "missing_token_exchange_permission"
    # Future fields preserved via extra="allow"
    raw_issue = result.issues[0].model_dump()
    assert raw_issue.get("remediation_url") == "https://docs/..."
    raw_result = result.model_dump()
    assert "next_run_recommended_at" in raw_result
