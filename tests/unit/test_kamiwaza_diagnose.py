"""T5.7 / ENG-4692 — ClusterAPI.diagnose() on canonical kamiwaza_sdk.

WS-M3.2 test migration (T7.15 / ENG-5049). Per design §4.2.10 / §4.2.11:
customer-facing diagnose surface returns typed ClusterDiagnostics.
Walking-skeleton scope — fix() orchestration is in test_kamiwaza_diagnose_fix.
"""

from __future__ import annotations


def test_diagnose_returns_typed_cluster_diagnostics(mock_client) -> None:
    """``kz.cluster.diagnose()`` GETs ``/cluster/diagnose`` and parses the
    structured response into a ClusterDiagnostics model."""
    from kamiwaza_sdk.schemas.federation import ClusterDiagnostics
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "GET",
        "/cluster/diagnose",
        {
            "cluster_id": "11111111-2222-3333-4444-555555555555",
            "timestamp": "2026-05-10T22:00:00+00:00",
            "issues": [],
            "has_issues": False,
        },
    )

    result = ClusterAPI(client=mock_client).diagnose()

    assert isinstance(result, ClusterDiagnostics)
    assert result.has_issues is False
    assert result.issues == []


def test_diagnose_parses_structured_issues(mock_client) -> None:
    """Issues come back as typed DiagnoseIssue models so customer code
    can match on stable issue.id strings."""
    from kamiwaza_sdk.schemas.federation import DiagnoseIssue
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "GET",
        "/cluster/diagnose",
        {
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

    result = ClusterAPI(client=mock_client).diagnose()

    assert result.has_issues is True
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert isinstance(issue, DiagnoseIssue)
    assert issue.id == "admin_missing_baseline_rebac"
    assert issue.severity == "error"
    assert issue.auto_fixable is False


def test_diagnose_allows_unknown_issue_fields_forward_compat(mock_client) -> None:
    """Server-side schema evolution must not break a pinned SDK wheel."""
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "GET",
        "/cluster/diagnose",
        {
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

    result = ClusterAPI(client=mock_client).diagnose()

    assert result.issues[0].id == "missing_token_exchange_permission"
    raw_issue = result.issues[0].model_dump()
    assert raw_issue.get("remediation_url") == "https://docs/..."
    raw_result = result.model_dump()
    assert "next_run_recommended_at" in raw_result
