"""ENG-10282: federated jobs must traverse the mesh, not run locally."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kamiwaza_sdk.services.jobs_federation import JobsAPI


def test_sync_run_routes_to_encoded_mesh_selector_without_body_hint(
    mock_client,
) -> None:
    path = "/mesh/ORION%20edge/api/cluster/jobs/run"
    mock_client.expect(
        "POST",
        path,
        {"job_id": "remote-sync", "status": "SUCCEEDED", "result": {"ok": True}},
    )

    result = JobsAPI(client=mock_client).run(
        target_cluster="ORION edge",
        entrypoint="python remote.py",
    )

    assert result.job_id == "remote-sync"
    assert mock_client.calls == [
        ("POST", path, {"json": {"entrypoint": "python remote.py"}})
    ]


def test_recoverable_run_keeps_submit_status_and_result_on_remote_mesh(
    mock_client,
) -> None:
    prefix = "/mesh/ORION/api/cluster/jobs"
    job_id = "remote-recoverable"
    mock_client.expect("POST", f"{prefix}/submit", {"job_id": job_id})
    mock_client.expect("GET", f"{prefix}/{job_id}/status", {"status": "SUCCEEDED"})
    mock_client.expect(
        "GET",
        f"{prefix}/{job_id}/result",
        {"probe": "receiver-marker"},
    )

    with patch("time.sleep"):
        result = JobsAPI(client=mock_client).run(
            target_cluster="ORION",
            entrypoint="python remote.py",
            recoverable=True,
            timeout_seconds=30,
        )

    assert result.job_id == job_id
    assert result.result == {"probe": "receiver-marker"}
    assert [path for _, path, _ in mock_client.calls] == [
        f"{prefix}/submit",
        f"{prefix}/{job_id}/status",
        f"{prefix}/{job_id}/result",
    ]


def test_remote_submit_can_be_resumed_and_canceled_on_same_target(mock_client) -> None:
    prefix = "/mesh/ORION/api/cluster/jobs"
    job_id = "remote-resume"
    mock_client.expect("POST", f"{prefix}/submit", {"job_id": job_id})
    mock_client.expect("GET", f"{prefix}/{job_id}/status", {"status": "SUCCEEDED"})
    mock_client.expect("GET", f"{prefix}/{job_id}/result", {"answer": 42})
    mock_client.expect("POST", f"{prefix}/{job_id}/cancel", {"status": "STOPPED"})
    jobs = JobsAPI(client=mock_client)

    assert (
        jobs.submit_async(
            target_cluster="ORION",
            entrypoint="python remote.py",
        )
        == job_id
    )
    result = jobs.wait(job_id, timeout=30)
    canceled = jobs.cancel(job_id)

    assert result.result == {"answer": 42}
    assert canceled == {"status": "STOPPED"}


def test_remote_job_requests_attach_target_credential(mock_client, monkeypatch) -> None:
    prefix = "/mesh/ORION/api/cluster/jobs"
    monkeypatch.setenv("KAMIWAZA_FEDERATION_CREDENTIAL_ORION", "receiver-offline-token")
    mock_client.expect("POST", f"{prefix}/submit", {"job_id": "remote-auth"})
    mock_client.expect("GET", f"{prefix}/remote-auth/status", {"status": "SUCCEEDED"})
    mock_client.expect("GET", f"{prefix}/remote-auth/result", {"answer": 42})
    mock_client.expect("POST", f"{prefix}/remote-auth/cancel", {"status": "STOPPED"})
    jobs = JobsAPI(client=mock_client)

    job_id = jobs.submit_async(target_cluster="ORION", entrypoint="python remote.py")
    jobs.wait(job_id, timeout=30)
    jobs.cancel(job_id)

    assert all(
        kwargs["headers"]["X-KZ-Federation-Credential"] == "receiver-offline-token"
        for _, _, kwargs in mock_client.calls
    )


@pytest.mark.parametrize(
    "target_cluster", ["", "  ", " ORION ", "ORION/edge", ".", ".."]
)
def test_invalid_target_cluster_is_rejected_before_request(
    mock_client, target_cluster: str
) -> None:
    with pytest.raises(ValueError, match="target_cluster"):
        JobsAPI(client=mock_client).run(
            target_cluster=target_cluster,
            entrypoint="python remote.py",
        )

    assert mock_client.calls == []
