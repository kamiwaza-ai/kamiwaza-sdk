"""SDK contracts for per-user federation onboarding (ENG-9807)."""

from __future__ import annotations

import pytest

from tests.unit.test_kamiwaza_sdk_services_federations import _MockClient

pytestmark = pytest.mark.unit


def _proxy(client: _MockClient):
    from kamiwaza_sdk.services.federations import FederationsAPI

    client.expect(
        "GET",
        "/cluster/federations",
        [{"id": "fed-1", "status": "PAIRED", "remote_cluster_name": "ORION"}],
    )
    return FederationsAPI(client)["ORION"]


def test_onboarding_property_and_request() -> None:
    client = _MockClient()
    proxy = _proxy(client)
    client.expect(
        "POST",
        "/cluster/federations/fed-1/onboarding/request",
        {"id": "req-1", "external_id": "alice", "status": "REQUESTED"},
    )

    result = proxy.onboarding.request(
        justification="Conjunction review",
        email="alice@example.com",
    )

    assert result["id"] == "req-1"
    post = [kwargs for method, path, kwargs in client.calls if method == "POST"][0]
    assert post["json"] == {
        "justification": "Conjunction review",
        "email": "alice@example.com",
    }


def test_onboarding_approve_and_deny_forward_payloads() -> None:
    client = _MockClient()
    proxy = _proxy(client)
    client.expect(
        "POST",
        "/cluster/federations/fed-1/onboarding/req-1/approve",
        {"status": "APPROVED"},
    )
    client.expect(
        "POST", "/cluster/federations/fed-1/onboarding/req-2/deny", {"status": "DENIED"}
    )

    proxy.onboarding.approve(
        "req-1",
        attributes={"clearance": "S"},
        relations=[{"relation": "viewer", "object": "dataset:x"}],
        admitted_without_access=True,
    )
    proxy.onboarding.deny("req-2", reason="Not eligible")

    approve = client.calls[1][2]
    assert approve["json"] == {
        "attributes": {"clearance": "S"},
        "relations": [{"relation": "viewer", "object": "dataset:x"}],
        "admitted_without_access": True,
    }
    assert client.calls[2][2]["json"] == {"reason": "Not eligible"}


def test_onboarding_status_and_claim_paths() -> None:
    client = _MockClient()
    proxy = _proxy(client)
    client.expect(
        "GET", "/cluster/federations/fed-1/onboarding", [{"status": "REQUESTED"}]
    )
    client.expect(
        "GET", "/cluster/federations/fed-1/onboarding/me", {"status": "APPROVED"}
    )
    client.expect(
        "POST",
        "/cluster/federations/fed-1/onboarding/claim",
        {"status": "CLAIMED", "credential": "once"},
    )
    client.expect(
        "GET",
        "/cluster/federations/fed-1/onboarding/claims/attempt-1",
        {"status": "CLAIMED"},
    )
    client.expect(
        "POST",
        "/cluster/federations/fed-1/onboarding/claims/attempt-1/recover",
        {"status": "RECOVERED"},
    )

    assert proxy.onboarding.list()[0]["status"] == "REQUESTED"
    assert proxy.onboarding.me()["status"] == "APPROVED"
    assert proxy.onboarding.claim("claim-token")["credential"] == "once"
    assert proxy.onboarding.claim_status("attempt-1")["status"] == "CLAIMED"
    assert proxy.onboarding.recover_claim("attempt-1")["status"] == "RECOVERED"
