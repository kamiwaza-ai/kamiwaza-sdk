"""T7.11 / ENG-5045 — RetrievalAPI on the canonical kamiwaza_sdk surface.

WS-M3.2 test migration (T7.15 / ENG-5049). Drops the legacy
``kamiwaza.client.Kamiwaza`` + ``httpx_mock`` machinery in favor of the
canonical ``kamiwaza_sdk.services.retrieval_federation.RetrievalAPI``
instantiated directly against the shared ``MockClient`` fixture.

Customer-facing surface per design §4.2.11:

    kz.retrieval.list(...)         -> list of retrieval job records
    kz.retrieval.cancel(query_id)  -> updated job status

Plus operations() now populates the retrievals slice from this module.
"""

from __future__ import annotations


def test_retrieval_list_hits_get_endpoint(mock_client) -> None:
    """kz.retrieval.list() GETs /retrieval/jobs with default pagination."""
    from kamiwaza_sdk.services.retrieval_federation import RetrievalAPI

    mock_client.expect(
        "GET",
        "/retrieval/jobs",
        [
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

    items = RetrievalAPI(client=mock_client).list()

    assert len(items) == 1
    assert items[0]["job_id"] == "00000000-0000-0000-0000-000000000001"


def test_retrieval_cancel_posts_to_cancel_endpoint(mock_client) -> None:
    """kz.retrieval.cancel(query_id) POSTs to .../jobs/{id}/cancel."""
    from kamiwaza_sdk.services.retrieval_federation import RetrievalAPI

    job_id = "00000000-0000-0000-0000-000000000002"
    mock_client.expect(
        "POST",
        f"/retrieval/jobs/{job_id}/cancel",
        {"job_id": job_id, "status": "CANCELED", "transport": "sse"},
    )

    result = RetrievalAPI(client=mock_client).cancel(job_id)

    assert result["status"] == "CANCELED"


def test_operations_populates_retrievals_slice(mock_client) -> None:
    """ClusterAPI.operations() surfaces in-flight retrievals (T5.37 thickening)."""
    from kamiwaza_sdk.services.cluster_federation import ClusterAPI

    mock_client.expect("GET", "/cluster/jobs/", [])
    mock_client.expect(
        "GET",
        "/retrieval/jobs",
        [
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

    result = ClusterAPI(client=mock_client).operations()

    assert result.jobs == []
    assert len(result.retrievals) == 1
    assert result.retrievals[0]["status"] == "RUNNING"
