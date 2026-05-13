"""T5.35 + T5.37 — JobsAPI.cancel + ClusterAPI.operations on canonical surface.

WS-M3.2 test migration (T7.15 / ENG-5049). WS-M2 demo bullets:
- (2) ``kz.cluster.operations()`` lists running jobs + retrievals.
- (3) ``kz.jobs.cancel(job_id)`` stops a stuck job.
"""

from __future__ import annotations


def test_cancel_posts_to_server_cancel_endpoint(mock_client) -> None:
    """``kz.jobs.cancel(job_id)`` POSTs to ``/cluster/jobs/{id}/cancel``."""
    from kamiwaza_sdk.services.jobs_federation import JobsAPI

    job_id = "00000000-0000-0000-0000-000000000001"
    mock_client.expect(
        "POST",
        f"/cluster/jobs/{job_id}/cancel",
        {
            "id": job_id,
            "status": "STOPPED",
            "source": "local",
            "user_id": "u",
            "entrypoint": "python q.py",
            "submitted_at": "2026-05-10T22:00:00+00:00",
            "created_at": "2026-05-10T22:00:00+00:00",
            "updated_at": "2026-05-10T22:00:01+00:00",
        },
    )

    result = JobsAPI(client=mock_client).cancel(job_id)
    assert result["id"] == job_id
    assert result["status"] == "STOPPED"


def test_operations_lists_running_jobs(mock_client) -> None:
    """``kz.cluster.operations()`` GETs /cluster/jobs and surfaces them
    as a structured ClusterOperations result."""
    from kamiwaza_sdk.schemas.federation import ClusterOperations
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect(
        "GET",
        "/cluster/jobs/",
        [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "status": "RUNNING",
                "source": "local",
                "user_id": "u",
                "entrypoint": "python q.py",
                "submitted_at": "2026-05-10T22:00:00+00:00",
                "created_at": "2026-05-10T22:00:00+00:00",
                "updated_at": "2026-05-10T22:00:00+00:00",
            },
            {
                "id": "00000000-0000-0000-0000-000000000002",
                "status": "SUCCEEDED",
                "source": "mesh",
                "user_id": "u",
                "entrypoint": "python q2.py",
                "submitted_at": "2026-05-10T21:00:00+00:00",
                "created_at": "2026-05-10T21:00:00+00:00",
                "updated_at": "2026-05-10T21:00:05+00:00",
            },
        ],
    )
    mock_client.expect("GET", "/retrieval/jobs", [])

    result = ClusterAPI(client=mock_client).operations()

    assert isinstance(result, ClusterOperations)
    assert len(result.jobs) == 2
    assert result.jobs[0]["status"] == "RUNNING"
    assert result.retrievals == []


def test_operations_empty_when_no_jobs(mock_client) -> None:
    """Empty cluster — no jobs, no retrievals — returns empty lists."""
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect("GET", "/cluster/jobs/", [])
    mock_client.expect("GET", "/retrieval/jobs", [])

    result = ClusterAPI(client=mock_client).operations()

    assert result.jobs == []
    assert result.retrievals == []
