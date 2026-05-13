"""ENG-4686 / T5.18-skeleton — SDK federation walkthrough integration test.

Exercises the full WS-M1 demo flow through the customer-facing SDK
surface, end-to-end, with the shared ``mock_client`` fixture standing
in for a paired LYRA + ORION cluster. Mirrors the manual smoke at
``~/kamiwaza-smoke.py federation-brokering`` — same call shape, same
assertions on the audit-actor round-trip — but runs in the standard
pytest collection so regressions are caught on every PR.

A live-cluster variant of this same flow is what the smoke script
exercises in real conditions; this fakes-driven test is the CI
counterpart and intentionally does not require a running cluster.

WS-M3.2 test migration (T7.15 / ENG-5049): rewritten off ``httpx_mock``
onto the shared ``MockClient`` fixture in ``tests/conftest.py``. The
canonical SDK uses ``requests`` (not httpx), so the httpx-mock framework
never intercepted post-migration. Per peer integration test
``test_jobs_recoverable_drop_recovery.py``: despite being under
``tests/integration/``, this is a unit-style test that mocks at the
``_request`` boundary. Transport-layer URL construction is regression-
guarded separately by ``tests/unit/test_kamiwaza_sdk_url_construction.py``.

Walkthrough mirrors the README "Federation walkthrough" section:

  1. Pair LYRA → ORION (initiator)
  2. Allowlist a brokered user on the receiver
  3. Submit a federated job
  4. Assert ``audit_actor`` round-trip — the demo gate's signal that
     the job ran as the originating user, not as a system principal.
"""

from __future__ import annotations

from unittest.mock import patch

_ORION_FEDERATION_ID = "fed-orion-uuid"
_ORION_NAME = "ORION"
_LYRA_CLUSTER_UUID = "lyra-cluster-uuid"
_BROKERED_EXTERNAL_ID = f"cdr-baker@{_LYRA_CLUSTER_UUID}"


def test_ws_m1_skeleton_walkthrough_pair_allowlist_run_audit(mock_client) -> None:
    """End-to-end SDK walkthrough — the WS-M1 demo gate.

    Sequences the customer-facing SDK calls in the order a customer
    runs them, with ``mock_client`` standing in for the paired cluster
    pair. Asserts on the audit-actor round-trip because that's the
    load-bearing demo-gate signal: the job ran as the originating user
    (``cdr-baker@LYRA``), not as a system principal.
    """
    from kamiwaza_sdk.services.federations import FederationsAPI
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    # Step 1 — Pair: two-step flow on the initiator side.
    mock_client.expect(
        "POST",
        "/cluster/federations",
        {
            "id": _ORION_FEDERATION_ID,
            "status": "PAIRING",
            "remote_cluster_name": _ORION_NAME,
        },
    )
    mock_client.expect(
        "POST",
        f"/cluster/federations/{_ORION_FEDERATION_ID}/pair",
        {
            "id": _ORION_FEDERATION_ID,
            "status": "PAIRED",
            "remote_cluster_name": _ORION_NAME,
            "remote_cluster_id": "orion-cluster-uuid",
            "callback_hostname": "edge.lyra.example.com",
        },
    )

    # Step 2a — Federation lookup-by-name (used to resolve the id for
    # the indexed-access proxy when the customer says
    # ``kz.federations["ORION"]``). The proxy hits the list endpoint
    # internally before posting to /users.
    mock_client.expect(
        "GET",
        "/cluster/federations",
        {
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
    mock_client.expect(
        "POST",
        f"/cluster/federations/{_ORION_FEDERATION_ID}/users",
        {
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
    mock_client.expect(
        "POST",
        "/cluster/jobs/run",
        {
            "job_id": "job-fed-1",
            "status": "SUCCEEDED",
            "result": {"row_count": 42},
            "audit_actor": _BROKERED_EXTERNAL_ID,
        },
    )

    federations = FederationsAPI(client=mock_client)
    jobs = JobsAPI(client=mock_client)

    # Step 1
    fed = federations.pair(
        name=_ORION_NAME,
        role="initiator",
        remote_url="https://orion.kamiwaza.test",
        remote_admin_token="orion-admin-pat",
    )
    assert fed.id == _ORION_FEDERATION_ID
    assert fed.status == "PAIRED"

    # Step 2
    user = federations[_ORION_NAME].users.add(
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
    result = jobs.run(
        target_cluster=_ORION_NAME,
        entrypoint="python /workdir/query.py --rows 42",
    )
    assert result.status == "SUCCEEDED"
    # Step 4 — the demo gate's load-bearing assertion.
    assert result.audit_actor == _BROKERED_EXTERNAL_ID

    # All five expected calls landed in the right sequence. The recorded
    # ``mock_client.calls`` is the canonical introspection point — verify
    # method + path order so silent regressions where one call is elided
    # or duplicated surface immediately.
    methods_paths = [(method, path) for method, path, _kwargs in mock_client.calls]
    assert len(methods_paths) == 5, methods_paths
    assert methods_paths[0] == ("POST", "/cluster/federations")
    assert methods_paths[1] == (
        "POST",
        f"/cluster/federations/{_ORION_FEDERATION_ID}/pair",
    )
    # 2a list-by-name lookup, 2b POST users
    assert methods_paths[2] == ("GET", "/cluster/federations")
    assert methods_paths[3] == (
        "POST",
        f"/cluster/federations/{_ORION_FEDERATION_ID}/users",
    )
    assert methods_paths[4] == ("POST", "/cluster/jobs/run")


def test_ws_m1_skeleton_walkthrough_async_submit_path(mock_client) -> None:
    """Customers running long jobs prefer the async ``submit_async +
    wait`` shape. This variant of the walkthrough exercises that path
    end-to-end: pair → allowlist → submit_async → wait → audit. The
    SDK polls until the job's terminal state and returns the result.
    """
    from kamiwaza_sdk.services.federations import FederationsAPI
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    # Pair (same as the synchronous test).
    mock_client.expect(
        "POST",
        "/cluster/federations",
        {
            "id": _ORION_FEDERATION_ID,
            "status": "PAIRING",
            "remote_cluster_name": _ORION_NAME,
        },
    )
    mock_client.expect(
        "POST",
        f"/cluster/federations/{_ORION_FEDERATION_ID}/pair",
        {
            "id": _ORION_FEDERATION_ID,
            "status": "PAIRED",
            "remote_cluster_name": _ORION_NAME,
        },
    )

    # Allowlist (no initial_tuples on this path — covered by the
    # synchronous test, no need to duplicate).
    mock_client.expect(
        "GET",
        "/cluster/federations",
        {
            "items": [
                {
                    "id": _ORION_FEDERATION_ID,
                    "status": "PAIRED",
                    "remote_cluster_name": _ORION_NAME,
                }
            ]
        },
    )
    mock_client.expect(
        "POST",
        f"/cluster/federations/{_ORION_FEDERATION_ID}/users",
        {
            "federation_id": _ORION_FEDERATION_ID,
            "external_id": _BROKERED_EXTERNAL_ID,
            "auto_provisioned": False,
        },
    )

    # Async submit returns a job_id; SDK then polls /status (RUNNING → SUCCEEDED)
    # and finally fetches /result on terminal.
    mock_client.expect("POST", "/cluster/jobs/submit", {"job_id": "job-async-1"})
    mock_client.expect_sequence(
        "GET",
        "/cluster/jobs/job-async-1/status",
        [{"status": "RUNNING"}, {"status": "SUCCEEDED"}],
    )
    mock_client.expect(
        "GET",
        "/cluster/jobs/job-async-1/result",
        {
            "job_id": "job-async-1",
            "status": "SUCCEEDED",
            "result": {"row_count": 99},
            "audit_actor": _BROKERED_EXTERNAL_ID,
        },
    )

    federations = FederationsAPI(client=mock_client)
    jobs = JobsAPI(client=mock_client)

    federations.pair(
        name=_ORION_NAME,
        role="initiator",
        remote_url="https://orion.kamiwaza.test",
        remote_admin_token="orion-admin-pat",
    )
    federations[_ORION_NAME].users.add(external_id=_BROKERED_EXTERNAL_ID)

    job_id = jobs.submit_async(
        target_cluster=_ORION_NAME,
        entrypoint="python /workdir/long_query.py",
    )
    assert job_id == "job-async-1"

    # Patch time.sleep so polling backoff doesn't actually block test
    # runtime — we still exercise the polling loop, just without wall-
    # clock cost.
    with patch("time.sleep"):
        result = jobs.wait(job_id, timeout=60)

    assert result.status == "SUCCEEDED"
    assert result.audit_actor == _BROKERED_EXTERNAL_ID
