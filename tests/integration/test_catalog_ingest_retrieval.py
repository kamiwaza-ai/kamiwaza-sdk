from __future__ import annotations

from typing import Dict

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _ingest_sample_dataset(
    client,
    ingestion_environment: Dict[str, str],
    secret_urn: str,
) -> str:
    bucket = ingestion_environment["bucket"]
    prefix = ingestion_environment["prefix"]
    endpoint = ingestion_environment["endpoint"]

    try:
        ingest_response = client.ingestion.run_active(
            "s3",
            bucket=bucket,
            prefix=prefix,
            recursive=True,
            endpoint_url=endpoint,
            region="us-east-1",
            secret_name=secret_urn,
        )
    except APIError as exc:
        if exc.status_code == 500 and "Could not connect to the endpoint URL" in str(exc):
            pytest.skip(
                "Live ingestion object-store endpoint is unreachable from the platform "
                "(see docs-local/00-server-defects.md)"
            )
        raise
    urns = ingest_response.urns
    assert urns, "ingestion did not return dataset URNs"
    dataset_urn = urns[0]

    dataset = client.get("/catalog/datasets/by-urn", params={"urn": dataset_urn})
    assert dataset["urn"] == dataset_urn
    assert dataset["properties"]["path"].startswith("s3://")

    return dataset_urn


def _inline_payload(dataset_urn: str) -> Dict[str, str]:
    return {
        "dataset_urn": dataset_urn,
        "transport": "inline",
        "format_hint": "parquet",
    }


def _grpc_payload(dataset_urn: str) -> Dict[str, str]:
    payload = _inline_payload(dataset_urn)
    payload["transport"] = "grpc"
    return payload


def test_s3_ingest_and_retrieve_inline(
    live_kamiwaza_client,
    ingestion_environment: Dict[str, str],
    ingestion_s3_secret_urn: str,
) -> None:
    client = live_kamiwaza_client
    dataset_urn: str | None = None

    try:
        dataset_urn = _ingest_sample_dataset(
            client,
            ingestion_environment,
            ingestion_s3_secret_urn,
        )
        retrieval_payload = _inline_payload(dataset_urn)

        retrieval_job = client.post("/retrieval/jobs", json=retrieval_payload)

        assert retrieval_job["transport"] == "inline"
        inline = retrieval_job.get("inline")
        assert inline is not None
        assert inline["row_count"] > 0
        assert inline["media_type"] == "application/json"

        rows = inline["data"]
        assert isinstance(rows, list)
        assert {row["store"] for row in rows} == {"downtown", "uptown"}
    finally:
        if dataset_urn:
            client.delete("/catalog/datasets/by-urn", params={"urn": dataset_urn})


@pytest.mark.skip(reason="Retrieval gRPC transport currently fails (docs-local/00-server-defects.md)")
def test_s3_ingest_and_retrieve_grpc(
    live_kamiwaza_client,
    ingestion_environment: Dict[str, str],
    ingestion_s3_secret_urn: str,
) -> None:
    client = live_kamiwaza_client
    dataset_urn: str | None = None

    try:
        dataset_urn = _ingest_sample_dataset(
            client,
            ingestion_environment,
            ingestion_s3_secret_urn,
        )
        retrieval_payload = _grpc_payload(dataset_urn)

        retrieval_job = client.post("/retrieval/jobs", json=retrieval_payload)
        assert retrieval_job["transport"] == "grpc"
        handshake = retrieval_job.get("grpc")
        assert handshake is not None
        assert handshake["protocol"].startswith("kamiwaza.retrieval")
    finally:
        if dataset_urn:
            client.delete("/catalog/datasets/by-urn", params={"urn": dataset_urn})
