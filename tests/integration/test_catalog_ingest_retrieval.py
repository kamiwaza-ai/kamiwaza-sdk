from __future__ import annotations

import json
from typing import Dict

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


def test_s3_ingest_and_retrieve(
    live_kamiwaza_client,
    ingestion_environment: Dict[str, str],
) -> None:
    client = live_kamiwaza_client

    bucket = ingestion_environment["bucket"]
    prefix = ingestion_environment["prefix"]
    endpoint = ingestion_environment["endpoint"]
    dataset_urn: str | None = None

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

    try:
        # Ensure the dataset metadata is reachable
        dataset = client.get("/catalog/datasets/by-urn", params={"urn": dataset_urn})
        assert dataset["urn"] == dataset_urn
        assert dataset["properties"]["path"].startswith("s3://")

        # Provide endpoint/region hints for retrieval
        updated_properties = dict(dataset["properties"])
        updated_properties.update({"endpoint": endpoint, "region": "us-east-1"})
        client.patch(
            "/catalog/datasets/by-urn",
            params={"urn": dataset_urn},
            json={"properties": updated_properties},
        )

        retrieval_payload = {
            "dataset_urn": dataset_urn,
            "transport": "inline",
            "format_hint": "parquet",
            "credential_override": json.dumps(
                {
                    "aws_access_key_id": "minioadmin",
                    "aws_secret_access_key": "minioadmin",
                    "endpoint_override": endpoint,
                    "endpoint_url": endpoint,
                    "region": "us-east-1",
                }
            ),
        }

        try:
            retrieval_job = client.post("/retrieval/retrieval/jobs", json=retrieval_payload)
        except APIError as exc:  # pragma: no cover - retrieval pipeline not always available
            pytest.xfail(f"retrieval job creation failed: {exc}")
            return

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
