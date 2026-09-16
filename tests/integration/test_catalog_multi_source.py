"""Multi-source catalog ingestion and retrieval against a live platform (T02).

Every retained test proves an outcome rather than a status string: ingestion must
return datasets, the catalog must retain source metadata, retrieval jobs must
reach COMPLETED, and the retrieved content must equal what the catalog stack
seeded. Optional paths (file roots, Kafka, Slack, oversized objects) skip before
their ingestion or retrieval starts and name the prerequisite they are missing;
they are excluded from the evidence map in tests/e2e/capability_map.yaml.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import NotFoundError, TransportNotSupportedError
from kamiwaza_sdk.schemas.catalog import ContainerCreate, Dataset
from kamiwaza_sdk.schemas.retrieval import (
    InlineData,
    RetrievalJobStatus,
    RetrievalRequest,
    RetrievalStreamEvent,
    TransportType,
)

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

# Rows seeded by tests/integration/catalog_stack/setup-test-data.sh ("Creating
# Postgres fixtures"). Totals are NUMERIC(10,2) and come back as strings.
SEEDED_ORDERS = frozenset(
    {("Ada Lovelace", "123.45"), ("Grace Hopper", "67.89"), ("Alan Turing", "250.00")}
)
ORDERS_FIELDS = ("order_id", "customer_name", "total", "created_at")

FILE_INGESTION_ROOT_ENV = "CATALOG_FILE_INGESTION_ROOT"
KNOWN_SSE_DEFECT = "ENG-12300"
TERMINAL_JOB_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELED"})
CATALOG_PROPAGATION_TIMEOUT_S = 30.0


@pytest.fixture
def created_datasets(live_kamiwaza_client: KamiwazaClient) -> Iterator[list[str]]:
    """Collect ingested dataset URNs; delete each and confirm it is gone."""
    urns: list[str] = []
    yield urns
    if os.environ.get("KEEP_CATALOG_DATASETS") == "1":
        return
    for urn in dict.fromkeys(urns):
        live_kamiwaza_client.catalog.datasets.delete(urn)
        with pytest.raises(NotFoundError):
            live_kamiwaza_client.catalog.datasets.get(urn)


@pytest.fixture
def created_containers(live_kamiwaza_client: KamiwazaClient) -> Iterator[list[str]]:
    """Collect container URNs; delete each and confirm it is gone."""
    urns: list[str] = []
    yield urns
    for urn in dict.fromkeys(urns):
        live_kamiwaza_client.catalog.containers.delete(urn)
        with pytest.raises(NotFoundError):
            live_kamiwaza_client.catalog.containers.get(urn)


def _ingest_s3(
    client: KamiwazaClient,
    cfg: dict[str, Any],
    *,
    prefix: str,
    secret_urn: str,
    created: list[str],
) -> list[str]:
    response = client.ingestion.run_active(
        "s3",
        bucket=cfg["bucket"],
        prefix=prefix,
        endpoint_url=cfg["endpoint"],
        region=cfg["region"],
        secret_name=secret_urn,
    )
    created.extend(response.urns)
    assert response.errors == [], (
        f"S3 ingestion of {prefix!r} reported errors: {response.errors}"
    )
    assert response.urns, f"S3 ingestion of {prefix!r} returned no datasets"
    return response.urns


def _dataset_at_path(
    client: KamiwazaClient, urns: Sequence[str], *, bucket: str, key: str
) -> Dataset:
    # Keyed on the retained path: directory-prefix scans name datasets relative to
    # the prefix, so the name and URN do not carry the full object key.
    expected = f"s3://{bucket}/{key}"
    datasets = [client.catalog.datasets.get(urn) for urn in urns]
    matches = [d for d in datasets if (d.properties or {}).get("path") == expected]
    assert len(matches) == 1, (
        f"expected one dataset at {expected}; ingested paths were "
        f"{[(d.properties or {}).get('path') for d in datasets]}"
    )
    return matches[0]


def _assert_s3_source_retained(
    dataset: Dataset, *, bucket: str, key: str, fmt: str
) -> None:
    properties = dataset.properties or {}
    assert dataset.platform == "s3"
    assert properties.get("path") == f"s3://{bucket}/{key}"
    assert properties.get("format") == fmt


def _completed_inline_job(client: KamiwazaClient, urn: str, *, fmt: str) -> InlineData:
    job = client.retrieval.create_job(
        RetrievalRequest(dataset_urn=urn, transport="inline", format_hint=fmt)
    )
    assert job.transport == TransportType.INLINE
    assert job.status == "COMPLETED", f"inline job {job.job_id} is {job.status}"
    inline = job.inline
    assert inline is not None, f"inline job {job.job_id} returned no payload"
    assert isinstance(inline.data, list)
    assert inline.row_count == len(inline.data)
    status = client.retrieval.get_job(job.job_id)
    assert status.status == "COMPLETED", (
        f"job {job.job_id} reads back as {status.status}"
    )
    assert status.progress.rows_processed == inline.row_count
    return inline


def _seeded_parquet(path: Path) -> tuple[int, set[str], list[int]]:
    """Row count, column names and ids of the parquet file the fixture uploaded."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    table = pq.read_table(path)
    return (
        table.num_rows,
        set(table.column_names),
        sorted(table.column("id").to_pylist()),
    )


def _assert_rows_match_seed(inline: InlineData, seeded: Path) -> None:
    num_rows, columns, ids = _seeded_parquet(seeded)
    rows = inline.data
    assert inline.row_count == num_rows, (
        f"{inline.row_count} rows retrieved, {num_rows} seeded"
    )
    assert {key for row in rows for key in row} == columns
    assert sorted(row["id"] for row in rows) == ids


def _wait_for(
    predicate: Callable[[], bool],
    *,
    what: str,
    timeout_s: float = CATALOG_PROPAGATION_TIMEOUT_S,
) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail(f"timed out after {timeout_s:.0f}s waiting for {what}")
        time.sleep(1)


def _terminal_status(client: KamiwazaClient, job_id: str) -> RetrievalJobStatus:
    status = client.retrieval.get_job(job_id)
    deadline = time.monotonic() + CATALOG_PROPAGATION_TIMEOUT_S
    while status.status not in TERMINAL_JOB_STATUSES and time.monotonic() < deadline:
        time.sleep(1)
        status = client.retrieval.get_job(job_id)
    return status


def test_catalog_file_ingestion_metadata(
    live_kamiwaza_client: KamiwazaClient, created_datasets: list[str]
) -> None:
    root = os.environ.get(FILE_INGESTION_ROOT_ENV, "").strip()
    if not root:
        pytest.skip(
            f"Optional path, not T02 coverage: set {FILE_INGESTION_ROOT_ENV} to a directory "
            "the platform's ingestion workers can read, with RETRIEVAL_FILESYSTEM_ALLOWED_ROOTS "
            "covering it"
        )

    response = live_kamiwaza_client.ingestion.run_active(
        "file", path=root, recursive=True
    )
    created_datasets.extend(response.urns)
    assert response.urns, f"file ingestion of {root} returned no datasets"

    formats = {".parquet": "parquet", ".json": "json", ".csv": "csv"}
    target: tuple[str, str] | None = None
    for urn in response.urns:
        dataset = live_kamiwaza_client.catalog.datasets.get(urn)
        assert dataset.platform == "file"
        path = str((dataset.properties or {}).get("path") or "").lower()
        fmt = next((f for suffix, f in formats.items() if path.endswith(suffix)), None)
        if fmt:
            target = (urn, fmt)
            break
    assert target is not None, f"no parquet/json/csv dataset among {response.urns}"

    inline = _completed_inline_job(live_kamiwaza_client, target[0], fmt=target[1])
    assert inline.data, f"file dataset {target[0]} retrieved no rows"


def test_catalog_object_ingestion_inline_retrieval(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    catalog_s3_secret_urn: str,
    created_datasets: list[str],
) -> None:
    cfg = catalog_stack_environment["object"]
    key = f"{cfg['prefix']}/objects/sample.json"
    urns = _ingest_s3(
        live_kamiwaza_client,
        cfg,
        prefix=f"{cfg['prefix']}/objects",
        secret_urn=catalog_s3_secret_urn,
        created=created_datasets,
    )
    dataset = _dataset_at_path(
        live_kamiwaza_client, urns, bucket=cfg["bucket"], key=key
    )
    _assert_s3_source_retained(dataset, bucket=cfg["bucket"], key=key, fmt="json")

    inline = _completed_inline_job(live_kamiwaza_client, dataset.urn, fmt="json")
    seeded = Path(catalog_stack_environment["file_root"]) / "objects" / "sample.json"
    assert inline.data == [json.loads(seeded.read_text())]


def test_catalog_parquet_ingestion_inline_retrieval(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    catalog_s3_secret_urn: str,
    created_datasets: list[str],
) -> None:
    cfg = catalog_stack_environment["object"]
    key = f"{cfg['prefix']}/sales_data_10k.parquet"
    urns = _ingest_s3(
        live_kamiwaza_client,
        cfg,
        prefix=key,
        secret_urn=catalog_s3_secret_urn,
        created=created_datasets,
    )
    dataset = _dataset_at_path(
        live_kamiwaza_client, urns, bucket=cfg["bucket"], key=key
    )
    _assert_s3_source_retained(dataset, bucket=cfg["bucket"], key=key, fmt="parquet")

    inline = _completed_inline_job(live_kamiwaza_client, dataset.urn, fmt="parquet")
    _assert_rows_match_seed(
        inline, Path(catalog_stack_environment["file_root"]) / "sales_data_10k.parquet"
    )


def test_catalog_inline_small_object_succeeds(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    catalog_s3_secret_urn: str,
    created_datasets: list[str],
) -> None:
    cfg = catalog_stack_environment["object"]
    key = cfg["small_key"]
    urns = _ingest_s3(
        live_kamiwaza_client,
        cfg,
        prefix=key,
        secret_urn=catalog_s3_secret_urn,
        created=created_datasets,
    )
    dataset = _dataset_at_path(
        live_kamiwaza_client, urns, bucket=cfg["bucket"], key=key
    )
    _assert_s3_source_retained(dataset, bucket=cfg["bucket"], key=key, fmt="parquet")

    inline = _completed_inline_job(live_kamiwaza_client, dataset.urn, fmt="parquet")
    _assert_rows_match_seed(
        inline, Path(catalog_stack_environment["file_root"]) / "inline-small.parquet"
    )


def test_catalog_inline_large_object_hits_threshold(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    catalog_s3_secret_urn: str,
    created_datasets: list[str],
) -> None:
    oversized = Path(catalog_stack_environment["file_root"]) / "inline-large.parquet"
    if not oversized.is_file():
        pytest.skip(
            f"Optional oversized-object path, not T02 coverage: {oversized} was not seeded "
            "(catalog-stack setup needs pandas, numpy and pyarrow to generate it)"
        )

    cfg = catalog_stack_environment["object"]
    key = cfg["large_key"]
    urns = _ingest_s3(
        live_kamiwaza_client,
        cfg,
        prefix=key,
        secret_urn=catalog_s3_secret_urn,
        created=created_datasets,
    )
    dataset = _dataset_at_path(
        live_kamiwaza_client, urns, bucket=cfg["bucket"], key=key
    )
    with pytest.raises(TransportNotSupportedError, match="inline threshold"):
        live_kamiwaza_client.retrieval.create_job(
            RetrievalRequest(
                dataset_urn=dataset.urn, transport="inline", format_hint="parquet"
            )
        )


def test_catalog_sse_retrieval_emits_terminal_event(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    catalog_s3_secret_urn: str,
    created_datasets: list[str],
) -> None:
    cfg = catalog_stack_environment["object"]
    key = cfg["small_key"]
    urns = _ingest_s3(
        live_kamiwaza_client,
        cfg,
        prefix=key,
        secret_urn=catalog_s3_secret_urn,
        created=created_datasets,
    )
    dataset = _dataset_at_path(
        live_kamiwaza_client, urns, bucket=cfg["bucket"], key=key
    )

    job = live_kamiwaza_client.retrieval.create_job(
        RetrievalRequest(
            dataset_urn=dataset.urn, transport="sse", format_hint="parquet"
        )
    )
    assert job.transport == TransportType.SSE
    events: list[RetrievalStreamEvent] = list(
        live_kamiwaza_client.retrieval.stream_events(job.job_id)
    )
    status = _terminal_status(live_kamiwaza_client, job.job_id)

    observed = f"events={[event.event for event in events]}, job status={status.status}"
    assert any(event.event == "chunk" for event in events), (
        f"SSE stream for job {job.job_id} emitted no chunk events ({observed}); "
        f"known 1.2.1 defect {KNOWN_SSE_DEFECT}"
    )
    assert events[-1].event == "complete", (
        f"SSE stream for job {job.job_id} ended without a terminal complete event ({observed}); "
        f"known 1.2.1 defect {KNOWN_SSE_DEFECT}"
    )
    assert isinstance(events[-1].data.get("sequence"), int)
    assert status.status == "COMPLETED", f"SSE job {job.job_id}: {observed}"


def test_catalog_container_link_sets_dataset_container_urn(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    catalog_s3_secret_urn: str,
    created_datasets: list[str],
    created_containers: list[str],
) -> None:
    cfg = catalog_stack_environment["object"]
    key = cfg["small_key"]
    urns = _ingest_s3(
        live_kamiwaza_client,
        cfg,
        prefix=key,
        secret_urn=catalog_s3_secret_urn,
        created=created_datasets,
    )
    dataset = _dataset_at_path(
        live_kamiwaza_client, urns, bucket=cfg["bucket"], key=key
    )
    containers = live_kamiwaza_client.catalog.containers

    container_urn = containers.create(
        ContainerCreate(name=f"sdk-catalog-{uuid4().hex[:8]}", platform="integration")
    )
    created_containers.append(container_urn)
    containers.add_dataset(container_urn, dataset.urn)

    _wait_for(
        lambda: (
            live_kamiwaza_client.catalog.datasets.get(dataset.urn).container_urn
            == container_urn
        ),
        what=f"{dataset.urn} to report container {container_urn}",
    )
    _wait_for(
        lambda: dataset.urn in containers.get(container_urn).datasets,
        what=f"container {container_urn} to list {dataset.urn}",
    )

    containers.remove_dataset(container_urn, dataset.urn)
    _wait_for(
        lambda: dataset.urn not in containers.get(container_urn).datasets,
        what=f"container {container_urn} to stop listing {dataset.urn}",
    )


def test_catalog_postgres_ingestion_metadata(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    created_datasets: list[str],
) -> None:
    pg = catalog_stack_environment["postgres"]
    response = live_kamiwaza_client.ingestion.run_active(
        "postgres",
        host=pg["host"],
        port=pg["port"],
        database=pg["database"],
        user=pg["user"],
        password_secret_name=pg["password_secret_name"],
        schema=pg["schema"],
    )
    created_datasets.extend(response.urns)
    assert response.errors == [], (
        f"Postgres ingestion reported errors: {response.errors}"
    )
    orders = [urn for urn in response.urns if "catalog_test_orders" in urn]
    assert len(orders) == 1, (
        f"expected one catalog_test_orders dataset among {response.urns}"
    )

    dataset = live_kamiwaza_client.catalog.datasets.get(orders[0])
    properties = dataset.properties or {}
    assert dataset.platform == "postgres"
    assert properties.get("schema") == pg["schema"]
    assert properties.get("table") == "catalog_test_orders"
    assert dataset.dataset_schema is not None
    assert tuple(field.name for field in dataset.dataset_schema.fields) == ORDERS_FIELDS

    inline = _completed_inline_job(live_kamiwaza_client, dataset.urn, fmt="parquet")
    assert {
        (row["customer_name"], row["total"]) for row in inline.data
    } == SEEDED_ORDERS
    assert inline.row_count == len(SEEDED_ORDERS)


@pytest.mark.skip(
    reason=(
        "Optional path, not T02 coverage: Kafka retrieval is not implemented at 1.2.1 and the "
        "SDK rejects Kafka dataset URNs before job creation; see "
        "docs-local/00-server-defects.md#kafka-retrieval-missing"
    )
)
def test_catalog_kafka_ingestion_metadata(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    created_datasets: list[str],
) -> None:
    kafka = catalog_stack_environment["kafka"]
    response = live_kamiwaza_client.ingestion.run_active(
        "kafka",
        bootstrap_servers=kafka["bootstrap"],
    )
    created_datasets.extend(response.urns)
    assert response.urns, "Kafka ingestion did not return datasets"
    topic = next(
        (urn for urn in response.urns if kafka["topic"] in urn), response.urns[0]
    )
    dataset = live_kamiwaza_client.catalog.datasets.get(topic)
    assert dataset.platform == "kafka"


def test_catalog_slack_ingestion_metadata(
    live_kamiwaza_client: KamiwazaClient, created_datasets: list[str]
) -> None:
    token = os.environ.get("SLACK_TEST_TOKEN", "")
    channel = os.environ.get("SLACK_TEST_CHANNEL", "")
    multi_channels = os.environ.get("SLACK_TEST_CHANNELS", "")
    team_id = os.environ.get("SLACK_TEST_TEAM", "")
    if not token or not channel or not team_id:
        pytest.skip(
            "Optional path, not T02 coverage: provide SLACK_TEST_TOKEN, SLACK_TEST_CHANNEL and "
            "SLACK_TEST_TEAM to exercise Slack ingestion"
        )

    channel_list: list[str] = []
    if multi_channels:
        channel_list.extend(
            [item.strip() for item in multi_channels.split(",") if item.strip()]
        )
    if not channel_list:
        channel_list = [channel]

    response = live_kamiwaza_client.ingestion.run_slack_ingest(
        channels=channel_list,
        token=token,
        team_id=team_id,
        max_messages=3,
    )
    created_datasets.extend(response.urns)
    assert response.urns, "Slack ingestion returned no datasets"
    dataset = live_kamiwaza_client.catalog.datasets.get(response.urns[0])
    assert dataset.platform == "slack"
    resolved_channel = (dataset.properties or {}).get("channel_id") or channel_list[0]

    rows = live_kamiwaza_client.retrieval.slack_messages(
        response.urns[0],
        channels=[resolved_channel],
        max_messages=3,
        include_replies=False,
    )
    assert rows, "Slack retrieval did not return inline payload"
