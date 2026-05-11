"""T5.35 + T5.37 — kz.jobs.cancel() + kz.cluster.operations() tests.

WS-M2 demo bullets (2) and (3):
- (2) kz.cluster.operations() lists the running federated job + active
  retrieval (retrieval slice is empty in the walking skeleton until
  T5.30/T5.36 land).
- (3) kz.jobs.cancel(job_id) stops a stuck job within seconds.

Server-side correlates:
- POST /api/cluster/jobs/{id}/cancel  (already shipped; M1)
- GET /api/cluster/jobs               (ENG-4706 / T5.29)
"""

from __future__ import annotations

from typing import Any

import pytest


# -- kz.jobs.cancel ----------------------------------------------------------


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_cancel_posts_to_server_cancel_endpoint(httpx_mock: Any) -> None:
    """``kz.jobs.cancel(job_id)`` POSTs to
    ``/api/cluster/jobs/{id}/cancel`` and returns the JobRecord."""
    from kamiwaza.client import Kamiwaza

    job_id = "00000000-0000-0000-0000-000000000001"
    httpx_mock.add_response(
        method="POST",
        url=f"https://kamiwaza.test/api/cluster/jobs/{job_id}/cancel",
        status_code=200,
        json={
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

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.jobs.cancel(job_id)

    assert result["id"] == job_id
    assert result["status"] == "STOPPED"


# -- kz.cluster.operations --------------------------------------------------


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_operations_lists_running_jobs(httpx_mock: Any) -> None:
    """``kz.cluster.operations()`` GETs /api/cluster/jobs and surfaces
    them as a structured ClusterOperations result."""
    from kamiwaza.client import Kamiwaza
    from kamiwaza.models import ClusterOperations

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs/",
        status_code=200,
        json=[
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

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.operations()

    assert isinstance(result, ClusterOperations)
    assert len(result.jobs) == 2
    assert result.jobs[0]["status"] == "RUNNING"
    # Retrieval slice is empty until T5.30/T5.36 land; the contract is
    # already that operations() returns the unified shape.
    assert result.retrievals == []


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_operations_empty_when_no_jobs(httpx_mock: Any) -> None:
    """Empty cluster — no jobs, no retrievals — returns empty lists."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs/",
        status_code=200,
        json=[],
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.operations()

    assert result.jobs == []
    assert result.retrievals == []
