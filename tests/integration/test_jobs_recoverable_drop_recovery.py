"""T5.24 / ENG-4701 — connection-drop recovery integration test.

WS-M3.2 test migration (T7.15 / ENG-5049). Verifies WS-M2 demo bullet
(8): a federated job submitted via ``kz.jobs.run(..., recoverable=True)``
survives an induced mid-call SDK process kill — the SDK reconnects via
``job_id`` and retrieves the result.

Test shape: simulate two SDK instances on the same cluster. The first
instance submits the job (captures the job_id), is "killed," and a fresh
second instance resumes via ``wait(job_id)``.

Per design `system-design.md` §4.2.14 + Sequence 7.

Note: despite being under tests/integration/, this is a unit-style test
that mocks at the ``_request`` boundary — the "two SDK instances" shape
is preserved by constructing two ``JobsAPI(client=mock_client)`` calls
across the simulated kill.
"""

from __future__ import annotations

from unittest.mock import patch


def test_recoverable_run_survives_mid_call_sdk_kill(mock_client) -> None:
    """Demo bullet (8) — submit → kill SDK → resume from saved job_id."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "job-survives-restart-001"

    # Phase 1: submit_async returns job_id.
    mock_client.expect("POST", "/cluster/jobs/submit", {"job_id": job_id})
    # Phase 2: status polls RUNNING then SUCCEEDED, then result fetch.
    mock_client.expect_sequence(
        "GET",
        f"/cluster/jobs/{job_id}/status",
        [{"status": "RUNNING"}, {"status": "SUCCEEDED"}],
    )
    mock_client.expect(
        "GET",
        f"/cluster/jobs/{job_id}/result",
        {"job_id": job_id, "status": "SUCCEEDED", "result": {"conjunction_pairs": 42}},
    )

    # Phase 1 — submit on the "first SDK process."
    first_process = JobsAPI(client=mock_client)
    saved_job_id = first_process.submit_async(
        entrypoint="python conjunction_query.py",
        target_cluster="ORION",
        timeout_seconds=300,
    )
    assert saved_job_id == job_id

    # Simulate process death: drop the first JobsAPI handle.
    del first_process

    # Phase 2 — fresh "SDK process" resumes via wait(job_id). The MockClient
    # is the shared transport (in reality both processes would hit the same
    # remote cluster); only the SDK side is reconstructed.
    with patch("time.sleep"):
        second_process = JobsAPI(client=mock_client)
        result = second_process.wait(saved_job_id, timeout=120)

    assert result.job_id == saved_job_id
    assert result.status == "SUCCEEDED"
    assert result.result == {"conjunction_pairs": 42}


def test_recoverable_run_returns_job_id_before_completion(mock_client) -> None:
    """The load-bearing property: submit_async must return the job_id
    BEFORE the long-running poll loop completes. Without this, the SDK
    has no recovery handle."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "job-immediate-id-002"
    mock_client.expect("POST", "/cluster/jobs/submit", {"job_id": job_id})

    returned_id = JobsAPI(client=mock_client).submit_async(
        entrypoint="python query.py",
        target_cluster="ORION",
        timeout_seconds=300,
    )

    assert returned_id == job_id
    # Confirm only the submit was issued — no polling round-trips.
    assert len(mock_client.calls) == 1
    assert mock_client.calls[0][0] == "POST"
