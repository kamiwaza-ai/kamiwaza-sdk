"""Live 1.2.1 producer for on-demand ingest and registered-job status."""

import json
import os
from contextlib import ExitStack
from uuid import uuid4

import boto3
import pytest
from pydantic import SecretStr

from kamiwaza_sdk.exceptions import NotFoundError
from kamiwaza_sdk.schemas.catalog import SecretCreate
from kamiwaza_sdk.schemas.ingestion import IngestJobCreate

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _fixture_config() -> dict[str, str]:
    keys = (
        "KAMIWAZA_INGEST_S3_LOCAL_ENDPOINT",
        "KAMIWAZA_INGEST_S3_CLUSTER_ENDPOINT",
        "KAMIWAZA_INGEST_S3_BUCKET",
        "KAMIWAZA_INGEST_S3_ACCESS_KEY",
        "KAMIWAZA_INGEST_S3_SECRET_KEY",
    )
    config = {key: os.getenv(key, "") for key in keys}
    if not all(config.values()):
        pytest.skip("1.2.1 S3 ingest fixture is not configured")
    return config


def _s3_client(config: dict[str, str]):
    return boto3.client(
        "s3",
        endpoint_url=config["KAMIWAZA_INGEST_S3_LOCAL_ENDPOINT"],
        aws_access_key_id=config["KAMIWAZA_INGEST_S3_ACCESS_KEY"],
        aws_secret_access_key=config["KAMIWAZA_INGEST_S3_SECRET_KEY"],
        region_name="us-east-1",
    )


def _catalog_secret(client, config: dict[str, str]) -> str:
    profile = client.get("/auth/users/me")
    owner = profile.get("urn") or f"urn:li:corpuser:{profile['username']}"
    endpoint = config["KAMIWAZA_INGEST_S3_CLUSTER_ENDPOINT"]
    value = json.dumps(
        {
            "aws_access_key_id": config["KAMIWAZA_INGEST_S3_ACCESS_KEY"],
            "aws_secret_access_key": config["KAMIWAZA_INGEST_S3_SECRET_KEY"],
            "endpoint_override": endpoint,
            "endpoint_url": endpoint,
            "region": "us-east-1",
        }
    )
    return client.catalog.secrets.create(
        SecretCreate(
            name=f"sdk-ingest-{uuid4().hex[:10]}",
            value=SecretStr(value),
            owner=owner,
            description="Temporary 1.2.1 ingestion capability probe",
        )
    )


def test_ingest_object_and_register_pollable_job(live_kamiwaza_client) -> None:
    """A small object becomes a catalog dataset; cron registration is pollable."""
    config = _fixture_config()
    client = live_kamiwaza_client
    bucket = config["KAMIWAZA_INGEST_S3_BUCKET"]
    key = f"sdk-ingest-capability/{uuid4().hex}.json"
    s3 = _s3_client(config)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=b'{"id": 1}',
        ContentType="application/json",
    )
    with ExitStack() as cleanup:
        cleanup.callback(s3.delete_object, Bucket=bucket, Key=key)
        secret_urn = _catalog_secret(client, config)
        cleanup.callback(client.catalog.secrets.delete, secret_urn)
        args = {
            "bucket": bucket,
            "prefix": key,
            "endpoint_url": config["KAMIWAZA_INGEST_S3_CLUSTER_ENDPOINT"],
            "region": "us-east-1",
            "secret_name": secret_urn,
        }
        response = client.ingestion.run_active("s3", **args)
        dataset_urns = response.urns
        for urn in dataset_urns:
            cleanup.callback(client.catalog.datasets.delete, urn)
        assert dataset_urns, "On-demand ingest produced no catalog dataset"
        assert client.catalog.get_dataset(dataset_urns[0]).urn == dataset_urns[0]

        job_id = f"sdk-ingest-{uuid4().hex}"
        registered = client.ingestion.schedule_job(
            IngestJobCreate(
                job_id=job_id,
                schedule="*/5 * * * *",
                source_type="s3",
                conn_args=args,
            )
        )
        assert registered.status == "scheduled"
        observations = []
        for _ in range(6):
            try:
                status = client.ingestion.get_job_status(job_id)
            except NotFoundError:
                observations.append("404")
            else:
                assert status.job_id == job_id
                assert not status.created_urns
                observations.append(status.status)
        assert observations == ["pending"] * 6, observations
