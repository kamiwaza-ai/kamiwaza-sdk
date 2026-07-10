"""T5.8 / ENG-4693 — ClusterAPI.fix() orchestration on canonical surface.

WS-M3.2 test migration (T7.15 / ENG-5049). Per design §4.2.10: ``fix()``
iterates issues in severity order; for each ``auto_fixable=True`` issue,
invokes its ``fix_endpoint`` with ``fix_payload``. Issues with
``auto_fixable=False`` surface with ``status="manual_required"``.

PR-feedback M4: H2 defensive ``/api/``-strip in ``_attempt_fix`` is
covered by ``test_fix_strips_legacy_api_prefix_from_fix_endpoint`` below.
Without the strip, the request would resolve to ``/api/api/...`` against
the ``KamiwazaClient(base_url=".../api")`` convention and 404 every time.
"""

from __future__ import annotations

from kamiwaza_sdk.exceptions import KamiwazaError


def test_fix_reports_manual_required_for_non_auto_fixable_issues(mock_client) -> None:
    """admin-baseline issue is auto_fixable=False → fix() reports
    manual_required without hitting any endpoint."""
    from kamiwaza_sdk.schemas.federation import FixResult
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

    result = ClusterAPI(client=mock_client).fix()

    assert isinstance(result, FixResult)
    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.issue_id == "admin_missing_baseline_rebac"
    assert outcome.status == "manual_required"


def test_fix_invokes_fix_endpoint_for_auto_fixable_issue(mock_client) -> None:
    """fix() POSTs to issue.fix_endpoint with issue.fix_payload and records
    the result. SDK dispatch is generic on the issue shape."""
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
    mock_client.expect(
        "POST",
        "/cluster/diagnose/fix/missing_token_exchange_permission",
        {"fixed": True},
    )

    result = ClusterAPI(client=mock_client).fix()

    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.issue_id == "missing_token_exchange_permission"
    assert outcome.status == "fixed"

    # H2 (PR feedback): payload forwarded verbatim
    fix_call = next(c for c in mock_client.calls if c[0] == "POST")
    assert fix_call[2].get("json") == {"client_id": "kamiwaza-platform-svc"}


def test_fix_records_failure_when_fix_endpoint_5xxs(mock_client) -> None:
    """If the fix endpoint raises, the outcome records status=failed
    without re-raising — so the operator sees per-issue success/failure."""
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "GET",
        "/cluster/diagnose",
        {
            "cluster_id": "11111111-2222-3333-4444-555555555555",
            "timestamp": "2026-05-10T22:00:00+00:00",
            "issues": [
                {
                    "id": "missing_kamiwaza_claims_scope_attachment",
                    "severity": "error",
                    "summary": "kamiwaza-claims scope missing on platform clients",
                    "detail": {"missing_on": ["kamiwaza-platform"]},
                    "fix_endpoint": (
                        "/cluster/diagnose/fix/missing_kamiwaza_claims_scope_attachment"
                    ),
                    "fix_payload": {},
                    "auto_fixable": True,
                }
            ],
            "has_issues": True,
        },
    )
    mock_client.raise_on(
        "POST",
        "/cluster/diagnose/fix/missing_kamiwaza_claims_scope_attachment",
        KamiwazaError("Keycloak unreachable", status_code=503),
    )

    result = ClusterAPI(client=mock_client).fix()

    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.status == "failed"
    assert outcome.error is not None


def test_fix_strips_legacy_api_prefix_from_fix_endpoint(mock_client) -> None:
    """PR-feedback M4 / H2 regression guard: when the server emits a legacy
    fix_endpoint with a leading ``/api/`` prefix, the SDK strips it before
    routing against ``KamiwazaClient(base_url=".../api")``. Without the
    strip, the request would resolve to ``/api/api/...`` and 404 every
    time. The mock expects the **stripped** path; if the strip regresses,
    no expectation will match and the test fails with a clear error."""
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    legacy_fix_endpoint = "/api/cluster/diagnose/fix/legacy_probe"
    canonical_path = "/cluster/diagnose/fix/legacy_probe"

    mock_client.expect(
        "GET",
        "/cluster/diagnose",
        {
            "cluster_id": "11111111-2222-3333-4444-555555555555",
            "timestamp": "2026-05-10T22:00:00+00:00",
            "issues": [
                {
                    "id": "legacy_probe",
                    "severity": "error",
                    "summary": "Server returns legacy /api/-prefixed fix_endpoint",
                    "detail": {},
                    "fix_endpoint": legacy_fix_endpoint,
                    "fix_payload": {},
                    "auto_fixable": True,
                }
            ],
            "has_issues": True,
        },
    )
    # If the strip regresses, the SDK will POST to ``/api/cluster/...``
    # which has no expectation set — the mock raises AssertionError and
    # the test fails. The canonical path is the one we expect to see.
    mock_client.expect("POST", canonical_path, {"fixed": True})

    result = ClusterAPI(client=mock_client).fix()

    assert len(result.outcomes) == 1
    assert result.outcomes[0].status == "fixed"
    # Belt-and-suspenders: explicit assertion on the recorded request path.
    fix_call = next(c for c in mock_client.calls if c[0] == "POST")
    assert fix_call[1] == canonical_path
