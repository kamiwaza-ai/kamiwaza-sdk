"""Catalog ingest to retrieval round trip, inline and over Arrow Flight (gRPC).

Each test writes its own uniquely named parquet object, ingests exactly that
object, and retrieves it through the SDK's typed retrieval client. Cleanup is
part of the evidence: the dataset, the object, and the catalog secret are all
deleted, with errors propagating, then proven absent.

The two tests feed separate evidence records (tests/e2e/capability_map.yaml):
the inline test's record claims catalog registration and retrieval, and the
gRPC test, the only one that exercises Arrow Flight, has a retrieval record of
its own. On the Azure 1.2.1 evidence instance the gRPC leg fails with HTTP 503
(ENG-12300). It is reported as a failure on purpose: the retrieval capability
includes Arrow Flight, so an unavailable transport must not be converted into a
skip.

Arrow Flight verifies TLS. When the cluster certificate is not publicly
trusted, set REQUESTS_CA_BUNDLE to the cluster CA bundle.
"""

from __future__ import annotations

import io
import os
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any

import boto3
import pandas as pd
import pytest
from botocore.exceptions import ClientError

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.retrieval import RetrievalRequest, TransportType

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

# The MinIO that ingestion_environment starts or reuses on this host.
_LOCAL_MINIO_ENDPOINT = "http://localhost:19100"
_MINIO_ACCESS_KEY = "minioadmin"
_MINIO_SECRET_KEY = "minioadmin"  # nosec B105 - disposable local test fixture
_REGION = "us-east-1"
_ABSENCE_POLLS = 15
_ABSENCE_POLL_INTERVAL_S = 2.0
_COMPLETED = "COMPLETED"
# The stream carries three rows; the SDK default deadline is an hour.
_FLIGHT_TIMEOUT_S = 120.0
_S3_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
# The server detail of the 503 that ENG-12300 diagnoses; other upstream
# failures also answer 503 and must keep their own diagnostics.
_ENG_12300_503_DETAIL = "Arrow Flight retrieval is not configured"


@dataclass(frozen=True)
class _SeededDataset:
    urn: str
    bucket: str
    key: str
    rows: tuple[dict[str, Any], ...]


def _seed_rows(run_id: str) -> list[dict[str, Any]]:
    # Exactly representable floats keep the parquet -> JSON round trip exact.
    return [
        {"visitor_id": 1, "store": f"downtown-{run_id}", "spend": 23.5},
        {"visitor_id": 2, "store": f"uptown-{run_id}", "spend": 14.25},
        {"visitor_id": 3, "store": f"downtown-{run_id}", "spend": 9.75},
    ]


def _sorted_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=lambda row: row["visitor_id"])


def _local_minio_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=_LOCAL_MINIO_ENDPOINT,
        aws_access_key_id=_MINIO_ACCESS_KEY,
        aws_secret_access_key=_MINIO_SECRET_KEY,
        region_name=_REGION,
    )


def _expected_urn(bucket: str, key: str) -> str:
    # An exact-object scan (prefix == key) names the dataset by its full key.
    # A directory-prefix scan names it relative to the prefix, so a shared
    # filename under different prefixes would collide on one URN.
    return f"urn:li:dataset:(urn:li:dataPlatform:s3,{bucket}/{key},PROD)"


def _poll_until_not_found(fetch: Callable[[], object], what: str) -> None:
    """Prove a deletion; only NotFoundError ends the eventual-consistency poll."""
    for attempt in range(_ABSENCE_POLLS):
        try:
            fetch()
        except NotFoundError:
            return
        if attempt < _ABSENCE_POLLS - 1:
            time.sleep(_ABSENCE_POLL_INTERVAL_S)
    pytest.fail(f"{what} remained readable after deletion")


def _assert_object_absent(s3: Any, bucket: str, key: str) -> None:
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in _S3_NOT_FOUND_CODES:
            return
        raise
    pytest.fail(f"s3://{bucket}/{key} still exists after deletion")


def _ingest_exact_object(
    client: KamiwazaClient,
    ingestion_environment: dict[str, str],
    secret_urn: str,
    key: str,
) -> list[str]:
    try:
        response = client.ingestion.run_active(
            "s3",
            bucket=ingestion_environment["bucket"],
            prefix=key,
            recursive=True,
            endpoint_url=ingestion_environment["endpoint"],
            region=_REGION,
            secret_name=secret_urn,
        )
    except APIError as exc:
        if exc.status_code == 500 and "Could not connect to the endpoint URL" in str(
            exc
        ):
            pytest.skip(
                "Live ingestion object-store endpoint is unreachable from the platform "
                "(see docs-local/00-server-defects.md)"
            )
        raise
    return list(response.urns)


@contextmanager
def _seeded_dataset(
    client: KamiwazaClient,
    ingestion_environment: dict[str, str],
    secret_urn: str,
) -> Iterator[_SeededDataset]:
    """Write, ingest, and read back one run-unique object; delete it all after.

    Every deletion is attempted even when an earlier one raises. The error that
    propagates is the last deletion error, with a body failure kept as its
    context. Absence is proven only when the body and every deletion succeeded,
    so a cleanup check never masks the failure being reported.
    """
    bucket = ingestion_environment["bucket"]
    run_id = uuid.uuid4().hex[:12]
    # A bucket-root key: S3 ingestion creates a catalog folder container for
    # every slash-delimited path level, and deleting the dataset does not
    # remove it, so a per-run directory would leak one container per run.
    key = f"t01-{run_id}.parquet"
    rows = _seed_rows(run_id)
    s3 = _local_minio_client()

    dataset_urns: list[str] = []
    with ExitStack() as cleanup:
        # Callbacks run last-registered-first: datasets, then the object, then
        # the secret they were ingested with. ingestion_s3_secret_urn is
        # function-scoped, so no other test sees the deleted secret, and its
        # own teardown then meets only the expected 404.
        cleanup.callback(client.catalog.secrets.delete, secret_urn)
        buffer = io.BytesIO()
        pd.DataFrame(rows).to_parquet(buffer, index=False)
        s3.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue())
        cleanup.callback(s3.delete_object, Bucket=bucket, Key=key)

        dataset_urns = _ingest_exact_object(
            client, ingestion_environment, secret_urn, key
        )
        for urn in dataset_urns:
            cleanup.callback(client.catalog.datasets.delete, urn)
        expected_urn = _expected_urn(bucket, key)
        assert dataset_urns == [expected_urn]
        dataset = client.catalog.datasets.get(expected_urn)
        assert dataset.urn == expected_urn
        assert dataset.properties["path"] == f"s3://{bucket}/{key}"
        yield _SeededDataset(
            urn=expected_urn, bucket=bucket, key=key, rows=tuple(_sorted_rows(rows))
        )

    for urn in dataset_urns:
        _poll_until_not_found(
            partial(client.catalog.datasets.get, urn), f"dataset {urn}"
        )
    _assert_object_absent(s3, bucket, key)
    _poll_until_not_found(
        partial(client.catalog.secrets.get, secret_urn), f"catalog secret {secret_urn}"
    )


def test_s3_ingest_and_retrieve_inline(
    live_kamiwaza_client: KamiwazaClient,
    ingestion_environment: dict[str, str],
    ingestion_s3_secret_urn: str,
) -> None:
    client = live_kamiwaza_client
    with _seeded_dataset(
        client, ingestion_environment, ingestion_s3_secret_urn
    ) as seeded:
        job = client.retrieval.create_job(
            RetrievalRequest(
                dataset_urn=seeded.urn, transport="inline", format_hint="parquet"
            )
        )
        assert job.transport == TransportType.INLINE
        assert job.status == _COMPLETED
        assert job.inline is not None
        assert job.inline.media_type == "application/json"
        assert job.inline.row_count == len(seeded.rows)
        assert _sorted_rows(job.inline.data) == list(seeded.rows)

        status = client.retrieval.get_job(job.job_id)
        assert status.status == _COMPLETED
        assert status.progress.rows_processed == len(seeded.rows)


def test_s3_ingest_and_retrieve_grpc(
    live_kamiwaza_client: KamiwazaClient,
    ingestion_environment: dict[str, str],
    ingestion_s3_secret_urn: str,
) -> None:
    client = live_kamiwaza_client
    with _seeded_dataset(
        client, ingestion_environment, ingestion_s3_secret_urn
    ) as seeded:
        try:
            job = client.retrieval.create_job(
                RetrievalRequest(
                    dataset_urn=seeded.urn, transport="grpc", format_hint="parquet"
                )
            )
        except APIError as exc:
            if exc.status_code == 503 and _ENG_12300_503_DETAIL in str(exc):
                pytest.fail(
                    "gRPC (Arrow Flight) retrieval job creation returned HTTP 503: "
                    f"{exc}. ENG-12300 diagnoses this 503 on the Azure 1.2.1 "
                    "evidence instance, whose stored 'retrieval' runtime config "
                    "lacks flight_advertised_locations_raw. Check that ticket "
                    "before treating this as a new defect."
                )
            raise
        assert job.transport == TransportType.GRPC
        assert job.grpc is not None
        assert job.grpc.protocol == "arrow-flight"
        assert job.grpc.endpoints
        # The live client's KAMIWAZA_VERIFY_SSL=false outranks REQUESTS_CA_BUNDLE
        # for HTTP, so no CA reaches Flight on its own; pass it explicitly. When
        # it is unset, Flight verifies against the system roots.
        ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or None
        flight_rows = [
            row
            for batch in client.retrieval.flight_batches(
                job, ca_cert_path=ca_bundle, timeout_seconds=_FLIGHT_TIMEOUT_S
            )
            for row in batch.to_pylist()
        ]
        assert _sorted_rows(flight_rows) == list(seeded.rows)
        assert client.retrieval.get_job(job.job_id).status == _COMPLETED
