"""ENG-4686 / T5.18-skeleton — SDK federation walkthrough integration test.

Exercises the full WS-M1 demo flow through the customer-facing SDK
surface, end-to-end, via httpx_mock fakes that stand in for a paired
LYRA + ORION cluster. Mirrors the manual smoke at
``~/kamiwaza-smoke.py federation-brokering`` — same call shape, same
assertions on the audit-actor round-trip — but runs in the standard
pytest collection so regressions are caught on every PR.

A live-cluster variant of this same flow is what the smoke script
exercises in real conditions; this fakes-driven test is the CI
counterpart and intentionally does not require a running cluster.

Walkthrough mirrors the README "Federation walkthrough" section:

  1. Pair LYRA → ORION (initiator)
  2. Allowlist a brokered user on the receiver
  3. Submit a federated job
  4. Assert ``audit_actor`` round-trip — the demo gate's signal that
     the job ran as the originating user, not as a system principal.

The fake server sequences the responses to match what a real paired
flow returns. Customer code under test is the SDK's public API only;
internal helpers are not patched.
"""

from __future__ import annotations

from typing import Any

import pytest


_LYRA_BASE_URL = "https://lyra.kamiwaza.test"
_ORION_FEDERATION_ID = "fed-orion-uuid"
_ORION_NAME = "ORION"
_LYRA_CLUSTER_UUID = "lyra-cluster-uuid"
_BROKERED_EXTERNAL_ID = f"cdr-baker@{_LYRA_CLUSTER_UUID}"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_ws_m1_skeleton_walkthrough_pair_allowlist_run_audit(
    httpx_mock: Any,
) -> None:
    """End-to-end SDK walkthrough — the WS-M1 demo gate.

    Sequences the customer-facing SDK calls in the order a customer
    runs them, with httpx_mock standing in for the paired cluster pair.
    Asserts on the audit-actor round-trip because that's the
    load-bearing demo-gate signal: the job ran as the originating user
    (``cdr-baker@LYRA``), not as a system principal.
    """
    from kamiwaza_sdk import KamiwazaClient

    # Step 1 — Pair: two-step flow on the initiator side.
    httpx_mock.add_response(
        method="POST",
        url=f"{_LYRA_BASE_URL}/api/cluster/federations",
        status_code=201,
        json={
            "id": _ORION_FEDERATION_ID,
            "status": "PAIRING",
            "remote_cluster_name": _ORION_NAME,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=(f"{_LYRA_BASE_URL}/api/cluster/federations/{_ORION_FEDERATION_ID}/pair"),
        status_code=200,
        json={
            "id": _ORION_FEDERATION_ID,
            "status": "PAIRED",
            "remote_cluster_name": _ORION_NAME,
            "remote_cluster_id": "orion-cluster-uuid",
            "callback_hostname": "edge.lyra.example.com",
        },
    )

    # Step 2a — Federation lookup-by-name (used to resolve the id for
    # the indexed-access proxy when the customer says ``kz.federations
    # ["ORION"]``). This is the federations.list response the proxy
    # internally hits before posting to /users.
    httpx_mock.add_response(
        method="GET",
        url=f"{_LYRA_BASE_URL}/api/cluster/federations",
        status_code=200,
        json={
            "items": [
                {
                    "id": _ORION_FEDERATION_ID,
                    "status": "PAIRED",
                    "remote_cluster_name": _ORION_NAME,
                }
            ]
        },
    )

    # Step 2b — Allowlist the brokered user.
    httpx_mock.add_response(
        method="POST",
        url=(f"{_LYRA_BASE_URL}/api/cluster/federations/{_ORION_FEDERATION_ID}/users"),
        status_code=201,
        json={
            "federation_id": _ORION_FEDERATION_ID,
            "external_id": _BROKERED_EXTERNAL_ID,
            "auto_provisioned": False,
            "initial_tuples": [
                {
                    "subject": f"user:{_BROKERED_EXTERNAL_ID}",
                    "relation": "viewer",
                    "object": f"cluster:{_ORION_NAME}",
                }
            ],
        },
    )

    # Step 3 — Run a federated job synchronously and assert audit
    # attribution comes back to the SDK caller.
    httpx_mock.add_response(
        method="POST",
        url=f"{_LYRA_BASE_URL}/api/cluster/jobs/run",
        status_code=200,
        json={
            "job_id": "job-fed-1",
            "status": "SUCCEEDED",
            "result": {"row_count": 42},
            "audit_actor": _BROKERED_EXTERNAL_ID,
        },
    )

    with KamiwazaClient(base_url=f"{_LYRA_BASE_URL}/api", api_key="pat-lyra-admin") as kz:
        # Step 1
        fed = kz.federations.pair(
            name=_ORION_NAME,
            role="initiator",
            remote_url="https://orion.kamiwaza.test",
            remote_admin_token="orion-admin-pat",
        )
        assert fed.id == _ORION_FEDERATION_ID
        assert fed.status == "PAIRED"

        # Step 2
        user = kz.federations[_ORION_NAME].users.add(
            external_id=_BROKERED_EXTERNAL_ID,
            initial_tuples=[
                {
                    "subject": f"user:{_BROKERED_EXTERNAL_ID}",
                    "relation": "viewer",
                    "object": f"cluster:{_ORION_NAME}",
                }
            ],
        )
        assert user.external_id == _BROKERED_EXTERNAL_ID
        assert user.auto_provisioned is False  # set true on first ingress

        # Step 3
        result = kz.jobs.run(
            target_cluster=_ORION_NAME,
            entrypoint="python /workdir/query.py --rows 42",
        )
        assert result.status == "SUCCEEDED"
        # Step 4 — the demo gate's load-bearing assertion.
        assert result.audit_actor == _BROKERED_EXTERNAL_ID

    # All four expected POSTs/GETs landed in the right sequence; httpx_mock
    # would have recorded the order. Verify the call-site count to catch
    # silent regressions where one of the calls was elided or duplicated.
    requests = httpx_mock.get_requests()
    assert len(requests) == 5, [(r.method, r.url.path) for r in requests]
    methods_paths = [(r.method, r.url.path) for r in requests]
    assert methods_paths[0] == ("POST", "/api/cluster/federations")
    assert methods_paths[1] == (
        "POST",
        f"/api/cluster/federations/{_ORION_FEDERATION_ID}/pair",
    )
    # 2a list-by-name lookup, 2b POST users
    assert methods_paths[2] == ("GET", "/api/cluster/federations")
    assert methods_paths[3] == (
        "POST",
        f"/api/cluster/federations/{_ORION_FEDERATION_ID}/users",
    )
    assert methods_paths[4] == ("POST", "/api/cluster/jobs/run")


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_ws_m1_skeleton_walkthrough_async_submit_path(httpx_mock: Any) -> None:
    """Customers running long jobs prefer the async ``submit_async +
    wait`` shape. This variant of the walkthrough exercises that path
    end-to-end: pair → allowlist → submit_async → wait → audit. The
    SDK polls until the job's terminal state and returns the result.
    """
    from unittest.mock import patch

    from kamiwaza_sdk import KamiwazaClient

    # Pair (same as the synchronous test).
    httpx_mock.add_response(
        method="POST",
        url=f"{_LYRA_BASE_URL}/api/cluster/federations",
        status_code=201,
        json={
            "id": _ORION_FEDERATION_ID,
            "status": "PAIRING",
            "remote_cluster_name": _ORION_NAME,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=(f"{_LYRA_BASE_URL}/api/cluster/federations/{_ORION_FEDERATION_ID}/pair"),
        status_code=200,
        json={
            "id": _ORION_FEDERATION_ID,
            "status": "PAIRED",
            "remote_cluster_name": _ORION_NAME,
        },
    )

    # Allowlist (no initial_tuples on this path — exercised by the
    # synchronous test, no need to duplicate).
    httpx_mock.add_response(
        method="GET",
        url=f"{_LYRA_BASE_URL}/api/cluster/federations",
        status_code=200,
        json={
            "items": [
                {
                    "id": _ORION_FEDERATION_ID,
                    "status": "PAIRED",
                    "remote_cluster_name": _ORION_NAME,
                }
            ]
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=(f"{_LYRA_BASE_URL}/api/cluster/federations/{_ORION_FEDERATION_ID}/users"),
        status_code=201,
        json={
            "federation_id": _ORION_FEDERATION_ID,
            "external_id": _BROKERED_EXTERNAL_ID,
            "auto_provisioned": False,
        },
    )

    # Async submit returns a job_id; SDK then polls /status and /result.
    httpx_mock.add_response(
        method="POST",
        url=f"{_LYRA_BASE_URL}/api/cluster/jobs/submit",
        status_code=202,
        json={"job_id": "job-async-1"},
    )
    # First /status poll — RUNNING (not terminal); SDK keeps polling.
    httpx_mock.add_response(
        method="GET",
        url=f"{_LYRA_BASE_URL}/api/cluster/jobs/job-async-1/status",
        status_code=200,
        json={"status": "RUNNING"},
    )
    # Second /status poll — SUCCEEDED (terminal).
    httpx_mock.add_response(
        method="GET",
        url=f"{_LYRA_BASE_URL}/api/cluster/jobs/job-async-1/status",
        status_code=200,
        json={"status": "SUCCEEDED"},
    )
    # /result fetch on terminal.
    httpx_mock.add_response(
        method="GET",
        url=f"{_LYRA_BASE_URL}/api/cluster/jobs/job-async-1/result",
        status_code=200,
        json={
            "job_id": "job-async-1",
            "status": "SUCCEEDED",
            "result": {"row_count": 99},
            "audit_actor": _BROKERED_EXTERNAL_ID,
        },
    )

    with KamiwazaClient(base_url=f"{_LYRA_BASE_URL}/api", api_key="pat-lyra-admin") as kz:
        kz.federations.pair(
            name=_ORION_NAME,
            role="initiator",
            remote_url="https://orion.kamiwaza.test",
            remote_admin_token="orion-admin-pat",
        )
        kz.federations[_ORION_NAME].users.add(external_id=_BROKERED_EXTERNAL_ID)

        job_id = kz.jobs.submit_async(
            target_cluster=_ORION_NAME,
            entrypoint="python /workdir/long_query.py",
        )
        assert job_id == "job-async-1"

        # Patch time.sleep so the polling backoff doesn't actually
        # block test runtime — we still exercise the polling loop, just
        # without the wall-clock cost.
        with patch("time.sleep"):
            result = kz.jobs.wait(job_id, timeout=60)

        assert result.status == "SUCCEEDED"
        assert result.audit_actor == _BROKERED_EXTERNAL_ID
