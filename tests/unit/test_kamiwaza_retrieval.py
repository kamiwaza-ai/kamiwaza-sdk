"""T5.36 / ENG-4713 — kamiwaza.retrieval module tests.

Customer-facing surface per design §4.2.11:

    kz.retrieval.list(...)         -> list of retrieval job records
    kz.retrieval.cancel(query_id)  -> updated job status

Plus operations() now populates the retrievals slice from this module
(previously empty until cycle 5 retrieval work landed).
"""

from __future__ import annotations

from typing import Any

import pytest


def test_kamiwaza_exposes_retrieval_attribute() -> None:
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    assert client.retrieval is not None


def test_retrieval_is_lazy_loaded() -> None:
    from kamiwaza.client import Kamiwaza

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    a = client.retrieval
    b = client.retrieval
    assert a is b


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_retrieval_list_hits_get_endpoint(httpx_mock: Any) -> None:
    """kz.retrieval.list() GETs /api/retrieval/jobs."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/retrieval/jobs?limit=100&offset=0",
        status_code=200,
        json=[
            {
                "job_id": "00000000-0000-0000-0000-000000000001",
                "status": "RUNNING",
                "transport": "sse",
                "dataset": {
                    "urn": "urn:li:dataset:test",
                    "platform": "foo",
                    "format": "csv",
                    "estimated_bytes": 1024,
                },
            }
        ],
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    items = client.retrieval.list()

    assert len(items) == 1
    assert items[0]["job_id"] == "00000000-0000-0000-0000-000000000001"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_retrieval_cancel_posts_to_cancel_endpoint(httpx_mock: Any) -> None:
    """kz.retrieval.cancel(query_id) POSTs to .../jobs/{id}/cancel."""
    from kamiwaza.client import Kamiwaza

    job_id = "00000000-0000-0000-0000-000000000002"

    httpx_mock.add_response(
        method="POST",
        url=f"https://kamiwaza.test/api/retrieval/jobs/{job_id}/cancel",
        status_code=200,
        json={
            "job_id": job_id,
            "status": "CANCELED",
            "transport": "sse",
        },
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.retrieval.cancel(job_id)

    assert result["status"] == "CANCELED"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
def test_operations_populates_retrievals_slice(httpx_mock: Any) -> None:
    """cluster.operations() now surfaces in-flight retrievals (T5.37 thickening)."""
    from kamiwaza.client import Kamiwaza

    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/cluster/jobs/",
        status_code=200,
        json=[],
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kamiwaza.test/api/retrieval/jobs",
        status_code=200,
        json=[
            {
                "job_id": "00000000-0000-0000-0000-000000000003",
                "status": "RUNNING",
                "transport": "sse",
                "dataset": {
                    "urn": "urn:li:dataset:test",
                    "platform": "foo",
                    "format": "csv",
                    "estimated_bytes": 1024,
                },
            }
        ],
    )

    client = Kamiwaza(base_url="https://kamiwaza.test", token="pat-abc")
    result = client.cluster.operations()

    assert result.jobs == []
    assert len(result.retrievals) == 1
    assert result.retrievals[0]["status"] == "RUNNING"
