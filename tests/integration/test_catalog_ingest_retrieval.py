from __future__ import annotations

import json
from typing import Dict

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


def _ingest_sample_dataset(client, ingestion_environment: Dict[str, str]) -> str:
    bucket = ingestion_environment["bucket"]
    prefix = ingestion_environment["prefix"]
    endpoint = ingestion_environment["endpoint"]

    ingest_response = client.ingestion.run_active(
        "s3",
        bucket=bucket,
        prefix=prefix,
        recursive=True,
        endpoint_url=endpoint,
        region="us-east-1",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    )
    urns = ingest_response.urns
    assert urns, "ingestion did not return dataset URNs"
    dataset_urn = urns[0]

    dataset = client.get("/catalog/datasets/by-urn", params={"urn": dataset_urn})
    assert dataset["urn"] == dataset_urn
    assert dataset["properties"]["path"].startswith("s3://")

    return dataset_urn


def _inline_payload(dataset_urn: str, endpoint: str) -> Dict[str, str]:
    return {
        "dataset_urn": dataset_urn,
        "transport": "inline",
        "format_hint": "parquet",
        "credential_override": json.dumps(
            {
                "aws_access_key_id": "minioadmin",
                "aws_secret_access_key": "minioadmin",
                "endpoint": endpoint,
                "endpoint_override": endpoint,
                "endpoint_url": endpoint,
                "region": "us-east-1",
            }
        ),
    }


def _grpc_payload(dataset_urn: str, endpoint: str) -> Dict[str, str]:
    payload = _inline_payload(dataset_urn, endpoint)
    payload["transport"] = "grpc"
    return payload


def test_s3_ingest_and_retrieve_inline(
    live_kamiwaza_client,
    ingestion_environment: Dict[str, str],
) -> None:
    client = live_kamiwaza_client
    endpoint = ingestion_environment["endpoint"]
    dataset_urn: str | None = None

    try:
        dataset_urn = _ingest_sample_dataset(client, ingestion_environment)
        retrieval_payload = _inline_payload(dataset_urn, endpoint)

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
) -> None:
    client = live_kamiwaza_client
    endpoint = ingestion_environment["endpoint"]
    dataset_urn: str | None = None

    try:
        dataset_urn = _ingest_sample_dataset(client, ingestion_environment)
        retrieval_payload = _grpc_payload(dataset_urn, endpoint)

        retrieval_job = client.post("/retrieval/jobs", json=retrieval_payload)
        assert retrieval_job["transport"] == "grpc"
        handshake = retrieval_job.get("grpc")
        assert handshake is not None
        assert handshake["protocol"].startswith("kamiwaza.retrieval")
    finally:
        if dataset_urn:
            client.delete("/catalog/datasets/by-urn", params={"urn": dataset_urn})
