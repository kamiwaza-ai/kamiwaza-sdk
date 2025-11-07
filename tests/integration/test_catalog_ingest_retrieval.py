from __future__ import annotations

from pathlib import Path
import sys
import json
import os
from typing import Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
import responses

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import APIError


@pytest.mark.integration
@responses.activate
def test_s3_ingest_and_retrieve(ingestion_environment: Dict[str, str]) -> None:
    responses.add_passthru("https://localhost")
    responses.add_passthru("http://localhost")

    os.environ.setdefault("KAMIWAZA_VERIFY_SSL", "false")

    client = KamiwazaClient("https://localhost/api", api_key=os.environ.get("KAMIWAZA_API_KEY"))
    if client.authenticator is None:
        client.authenticator = UserPasswordAuthenticator("admin", "kamiwaza", client.auth)

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

    # cleanup
    client.delete("/catalog/datasets/by-urn", params={"urn": dataset_urn})
