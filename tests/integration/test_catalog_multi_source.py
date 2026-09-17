"""Multi-source catalog ingestion and retrieval against a live platform (T02).

Registry tests ingest a seeded source and assert the catalog retained its source
metadata; they never retrieve, so a retrieval failure cannot fail the registry
evidence. Retrieval tests run a retrieval job over seeded data and assert the job
completed and returned the seeded rows. They need a registered dataset to retrieve,
so an ingestion or catalog-lookup failure also fails them (retrieval requires the
dataset registry). Parquet content is compared by row count, column names and
``id`` values; the other parquet columns are not compared.

Optional paths are not T02 coverage and are excluded from the evidence map in
tests/e2e/capability_map.yaml. The file, Slack and oversized-object tests skip
before their ingestion or retrieval starts and name the prerequisite they are
missing. The Kafka test has no skip of its own: catalog-stack setup waits for the
Kafka port, and when setup fails every test that uses the stack skips at setup.
The container test is T02 coverage but skips, before it ingests anything, when an
identity without the admin role is refused a container write.

The datasets an ingestion returns and the containers a test creates directly are
deleted in fixture teardown and must then read as 404 through the test's client
within ABSENCE_POLLS reads, so a cleanup failure fails the test. Catalog reads are
scoped to the caller's visible workrooms, so this confirms the object is gone for
that client, not that no copy exists in another workroom. Containers that ingestion
creates for the source are not returned by it and are left in place: at 1.2.1, S3
ingestion upserts a bucket container and one per key folder, and their names are
fixed, so reruns reuse them.
``KEEP_CATALOG_DATASETS=1`` skips dataset cleanup for debugging; under
``--emit-evidence`` it stops the run with a usage error, so no evidence is written.
The catalog stack, its object keys and the resulting dataset URNs are shared by
every checkout on a host: concurrent runs can reseed or delete data under each
other, so capture evidence with no other catalog run in progress.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
import requests
from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.exceptions import (
    KamiwazaError,
    NotFoundError,
    TransportNotSupportedError,
)
from kamiwaza_sdk.schemas.catalog import ContainerCreate, Dataset
from kamiwaza_sdk.schemas.ingestion import IngestResponse
from kamiwaza_sdk.schemas.retrieval import (
    InlineData,
    RetrievalJobStatus,
    RetrievalRequest,
    RetrievalStreamEvent,
    TransportType,
)
from pydantic import ValidationError
from requests.adapters import HTTPAdapter
from urllib3.exceptions import HTTPError as Urllib3HTTPError

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]

# Rows seeded by tests/integration/catalog_stack/setup-test-data.sh ("Creating
# Postgres fixtures"). Totals are NUMERIC(10,2) and come back as strings.
SEEDED_ORDERS = frozenset(
    {("Ada Lovelace", "123.45"), ("Grace Hopper", "67.89"), ("Alan Turing", "250.00")}
)
ORDERS_FIELDS = ("order_id", "customer_name", "total", "created_at")

FILE_INGESTION_ROOT_ENV = "CATALOG_FILE_INGESTION_ROOT"
SLACK_REQUIRED_ENV = ("SLACK_TEST_TOKEN", "SLACK_TEST_CHANNEL", "SLACK_TEST_TEAM")
# Default retrieval ``inline_max_bytes`` at 1.2.1; an object must exceed it for the
# oversized-object test to mean anything.
INLINE_MAX_BYTES_1_2_1 = 1_000_000
# The SSE symptom (HTTP 200, no events, job FAILED) was first observed on the Azure
# 1.2.1 evidence instance and is investigated there; its cause is not established.
SSE_SYMPTOM_REPORT = "ENG-12300"
TERMINAL_JOB_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELED"})
CATALOG_PROPAGATION_TIMEOUT_S = 30.0
# The SDK sets no timeout on the SSE request. The 1.2.1 stream sends only chunk and
# complete events and is silent while a chunk is prepared; a silence longer than
# SSE_READ_TIMEOUT_S fails the test even if the stream would have resumed. The
# stream must also end within SSE_STREAM_TIMEOUT_S: a timer shuts its socket down at
# that deadline, so bytes that complete no event, such as keep-alive comments, cannot
# hold it open.
SSE_READ_TIMEOUT_S = 60.0
SSE_STREAM_TIMEOUT_S = 120.0
# A deleted object must read as 404 within this budget, the same one T01's
# _poll_until_not_found (test_catalog_ingest_retrieval.py) allows for catalog deletes.
ABSENCE_POLLS = 15
ABSENCE_POLL_INTERVAL_S = 2.0
# The SDK wraps requests exceptions raised while sending in APIError. Errors raised
# while a stream is read arrive as requests exceptions (OSError subclasses), a bad
# CA bundle path as a plain OSError, and some urllib3 errors unwrapped.
TRANSPORT_ERRORS = (KamiwazaError, OSError, Urllib3HTTPError)
CLEANUP_ERRORS = (*TRANSPORT_ERRORS, ValidationError)


def _delete_and_confirm_absent(
    kind: str,
    urns: Iterable[str],
    delete: Callable[[str], None],
    read: Callable[[str], object],
) -> None:
    """Attempt every deletion, then fail once listing every URN not confirmed gone.

    Only a read that returns 404 counts as gone. A 404 from delete does not: the
    1.2.1 catalog also answers 404 when it refuses a delete. Reads are scoped to the
    caller's visible workrooms, so gone means gone for this client.
    """
    problems: list[str] = []
    for urn in dict.fromkeys(urns):
        delete_note = ""
        try:
            delete(urn)
        except NotFoundError:
            delete_note = " (delete returned 404)"
        except CLEANUP_ERRORS as exc:
            problems.append(f"delete {kind} {urn}: {type(exc).__name__}: {exc}")
            continue
        problem = _absence_problem(read, urn)
        if problem:
            problems.append(f"{kind} {urn}{delete_note}: {problem}")
    if problems:
        pytest.fail(f"{kind} cleanup incomplete:\n" + "\n".join(problems))


def _absence_problem(read: Callable[[str], object], urn: str) -> str:
    """Read ``urn`` until it returns 404; return why it did not, or "" once it does.

    Any other read error ends the poll at once. A delete the catalog shows late
    still counts as gone if it reads as 404 within the ABSENCE_POLLS budget.
    """
    for attempt in range(ABSENCE_POLLS):
        try:
            read(urn)
        except NotFoundError:
            return ""
        except CLEANUP_ERRORS as exc:
            return f"read back failed: {type(exc).__name__}: {exc}"
        if attempt < ABSENCE_POLLS - 1:
            time.sleep(ABSENCE_POLL_INTERVAL_S)
    waited_s = (ABSENCE_POLLS - 1) * ABSENCE_POLL_INTERVAL_S
    return f"still readable after delete ({ABSENCE_POLLS} reads over {waited_s:.0f}s)"


@pytest.fixture
def created_datasets(
    live_kamiwaza_client: KamiwazaClient, request: pytest.FixtureRequest
) -> Iterator[list[str]]:
    """Collect ingested dataset URNs; delete each and confirm it no longer reads back.

    ``KEEP_CATALOG_DATASETS=1`` skips both steps. Evidence claims cleanup was
    verified, so under ``--emit-evidence`` the run stops with a usage error instead:
    a failed setup would be recorded as failed evidence, an aborted run records none.
    """
    keep = os.environ.get("KEEP_CATALOG_DATASETS") == "1"
    if keep and request.config.getoption("emit_evidence", default=False):
        pytest.exit(
            "KEEP_CATALOG_DATASETS=1 skips the cleanup that --emit-evidence records",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
    urns: list[str] = []
    yield urns
    if keep:
        return
    datasets = live_kamiwaza_client.catalog.datasets
    _delete_and_confirm_absent("dataset", urns, datasets.delete, datasets.get)


@pytest.fixture
def created_containers(live_kamiwaza_client: KamiwazaClient) -> Iterator[list[str]]:
    """Collect container URNs; delete each and confirm it no longer reads back."""
    urns: list[str] = []
    yield urns
    containers = live_kamiwaza_client.catalog.containers
    _delete_and_confirm_absent("container", urns, containers.delete, containers.get)


@dataclass(frozen=True)
class _S3Seed:
    """Seeded bucket config, local seed files, catalog secret, and URNs to clean up."""

    cfg: dict[str, Any]
    file_root: Path
    secret_urn: str
    created: list[str]


@pytest.fixture
def s3_seed(
    catalog_stack_environment: dict[str, Any],
    catalog_s3_secret_urn: str,
    created_datasets: list[str],
) -> _S3Seed:
    return _S3Seed(
        cfg=catalog_stack_environment["object"],
        file_root=Path(catalog_stack_environment["file_root"]),
        secret_urn=catalog_s3_secret_urn,
        created=created_datasets,
    )


def _ingest_s3(client: KamiwazaClient, seed: _S3Seed, prefix: str) -> list[str]:
    response = client.ingestion.run_active(
        "s3",
        bucket=seed.cfg["bucket"],
        prefix=prefix,
        endpoint_url=seed.cfg["endpoint"],
        region=seed.cfg["region"],
        secret_name=seed.secret_urn,
    )
    seed.created.extend(response.urns)
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


def _ingest_s3_object(
    client: KamiwazaClient, seed: _S3Seed, *, prefix: str, key: str
) -> Dataset:
    urns = _ingest_s3(client, seed, prefix)
    return _dataset_at_path(client, urns, bucket=seed.cfg["bucket"], key=key)


def _ingest_postgres_orders(
    client: KamiwazaClient, pg: dict[str, Any], created: list[str]
) -> Dataset:
    response = client.ingestion.run_active(
        "postgres",
        host=pg["host"],
        port=pg["port"],
        database=pg["database"],
        user=pg["user"],
        password_secret_name=pg["password_secret_name"],
        schema=pg["schema"],
    )
    created.extend(response.urns)
    assert response.errors == [], (
        f"Postgres ingestion reported errors: {response.errors}"
    )
    orders = [urn for urn in response.urns if "catalog_test_orders" in urn]
    assert len(orders) == 1, (
        f"expected one catalog_test_orders dataset among {response.urns}"
    )
    return client.catalog.datasets.get(orders[0])


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


def _assert_rows_match_seed(rows: list[dict[str, Any]], seeded: Path) -> None:
    num_rows, columns, ids = _seeded_parquet(seeded)
    assert len(rows) == num_rows, f"{len(rows)} rows retrieved, {num_rows} seeded"
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


def _terminal_status(
    client: KamiwazaClient, job_id: str, *, context: str
) -> RetrievalJobStatus:
    deadline = time.monotonic() + CATALOG_PROPAGATION_TIMEOUT_S
    status = client.retrieval.get_job(job_id)
    while status.status not in TERMINAL_JOB_STATUSES:
        if time.monotonic() >= deadline:
            pytest.fail(
                f"job {job_id} still {status.status} after "
                f"{CATALOG_PROPAGATION_TIMEOUT_S:.0f}s waiting for a terminal status "
                f"({context})"
            )
        time.sleep(1)
        status = client.retrieval.get_job(job_id)
    return status


def _shut_down_socket(response: requests.Response) -> None:
    """End a read blocked on the response's socket; safe to call from another thread.

    urllib3's ``HTTPResponse.shutdown`` keeps the socket even when http.client has
    detached it from the connection. It raises ValueError once the response is
    closed, RuntimeError once its connection is back in the pool, and OSError for a
    socket already closed: in each case the stream has already ended.
    """
    with contextlib.suppress(ValueError, RuntimeError, OSError):
        response.raw.shutdown()


class _StreamDeadlineAdapter(HTTPAdapter):
    """Time out a silent read, and cut every response off at a wall-clock deadline.

    A request that sets no timeout gets ``read_timeout_s`` as its connect and read
    timeout. A timer started on construction sets ``expired`` after ``deadline_s``
    and shuts down the socket of each response sent through this adapter, which ends
    a read however the server keeps it busy; a response sent after that is shut down
    as it returns. ``stop`` cancels the timer, and ``expired`` stays False if
    ``stop`` came first.
    """

    def __init__(self, read_timeout_s: float, deadline_s: float) -> None:
        super().__init__()
        self.read_timeout_s = read_timeout_s
        self.expired = False
        self._stopped = False
        self._lock = threading.Lock()
        self._responses: list[requests.Response] = []
        self._timer = threading.Timer(deadline_s, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def send(  # type: ignore[override]
        self, request: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        # requests always passes ``timeout``, as None when the caller set none.
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.read_timeout_s
        response = super().send(request, **kwargs)
        with self._lock:
            self._responses.append(response)
            expired = self.expired
        if expired:
            _shut_down_socket(response)
        return response

    def _expire(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self.expired = True
            responses = list(self._responses)
        for response in responses:
            _shut_down_socket(response)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
        self._timer.cancel()
        self._timer.join()

    def close(self) -> None:
        self.stop()
        super().close()


def _collect_stream(
    client: KamiwazaClient,
    job_id: str,
    *,
    timeout_s: float = SSE_STREAM_TIMEOUT_S,
    read_timeout_s: float = SSE_READ_TIMEOUT_S,
) -> list[RetrievalStreamEvent]:
    """Read the SSE stream to its end, then close it.

    Fails if the stream is silent for ``read_timeout_s``, or has not ended
    ``timeout_s`` after this starts, whatever it sends meanwhile. The stream is
    closed and the session's adapters restored before this returns or fails. The
    events reported on failure are those the SDK had parsed; an event still in its
    read buffer is not listed.
    """
    session = client.session
    adapters = {prefix: session.adapters[prefix] for prefix in ("https://", "http://")}
    timed = _StreamDeadlineAdapter(read_timeout_s, timeout_s)
    events: list[RetrievalStreamEvent] = []
    failure = ""
    try:
        for prefix in adapters:
            session.mount(prefix, timed)
        # stream_events sends the request; closing its generator closes the response.
        stream = cast(
            Generator[RetrievalStreamEvent, None, None],
            client.retrieval.stream_events(job_id),
        )
        try:
            events.extend(stream)  # keeps the events parsed before an error
        finally:
            # Stop first: the timer must not cut off a stream that ended or is closing.
            timed.stop()
            stream.close()
    except TRANSPORT_ERRORS as exc:
        failure = f"failed: {type(exc).__name__}: {exc}"
    finally:
        for prefix, adapter in adapters.items():
            session.mount(prefix, adapter)
        timed.close()
    if timed.expired:
        # A shut-down stream ends quietly or with a transport error; report the cause.
        failure = f"still open after {timeout_s:.0f}s"
    # Fail outside the except block: a chained SDK error makes pytest print the
    # arguments of urllib3's frames, which include the Authorization header.
    if failure:
        pytest.fail(
            f"SSE stream for job {job_id} {failure}; events so far="
            f"{[seen.event for seen in events]}"
        )
    return events


def _chunk_rows(chunk: RetrievalStreamEvent, context: str) -> list[dict[str, Any]]:
    # Chunk payload per the 1.2.1 retrieval source (transports/sse_query.py encode,
    # engine/ray_adapter.py iter_records): {"media_type": "application/json",
    # "data": [row dicts], ...} for tabular datasets. Derived from source, not yet
    # observed live, because SSE fails on the evidence instance.
    assert chunk.data.get("media_type") == "application/json", (
        f"chunk {chunk.data.get('sequence')!r} media_type "
        f"{chunk.data.get('media_type')!r} ({context})"
    )
    rows = chunk.data.get("data")
    assert isinstance(rows, list), (
        f"chunk {chunk.data.get('sequence')!r} carries no row list ({context})"
    )
    return rows


def _stream_rows(
    events: list[RetrievalStreamEvent], context: str
) -> list[dict[str, Any]]:
    """Check the stream ends with one complete event counting its chunks; return rows."""
    names = [event.event for event in events]
    chunks = [event for event in events if event.event == "chunk"]
    assert chunks, (
        f"SSE stream emitted no chunk events ({context}); {SSE_SYMPTOM_REPORT} "
        "records this symptom on the Azure 1.2.1 evidence instance"
    )
    assert names[-1] == "complete" and names.count("complete") == 1, (
        f"SSE stream did not end with exactly one terminal complete event ({context})"
    )
    sequence = events[-1].data.get("sequence")
    assert sequence == len(chunks), (
        f"complete event sequence {sequence!r} does not count the {len(chunks)} "
        f"chunk events ({context})"
    )
    return [row for chunk in chunks for row in _chunk_rows(chunk, context)]


def _file_retrieval_target(
    client: KamiwazaClient, urns: Sequence[str]
) -> tuple[str, str]:
    """The first ingested file dataset with a retrievable format, and that format."""
    formats = {".parquet": "parquet", ".json": "json", ".csv": "csv"}
    for urn in urns:
        dataset = client.catalog.datasets.get(urn)
        assert dataset.platform == "file"
        path = str((dataset.properties or {}).get("path") or "").lower()
        fmt = next((f for suffix, f in formats.items() if path.endswith(suffix)), None)
        if fmt:
            return urn, fmt
    pytest.fail(f"no parquet/json/csv dataset among {list(urns)}")


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

    urn, fmt = _file_retrieval_target(live_kamiwaza_client, response.urns)
    inline = _completed_inline_job(live_kamiwaza_client, urn, fmt=fmt)
    assert inline.data, f"file dataset {urn} retrieved no rows"


# --- Registry: ingestion retains source metadata ---------------------------------


def test_catalog_object_ingestion_metadata(
    live_kamiwaza_client: KamiwazaClient, s3_seed: _S3Seed
) -> None:
    cfg = s3_seed.cfg
    key = f"{cfg['prefix']}/objects/sample.json"
    dataset = _ingest_s3_object(
        live_kamiwaza_client, s3_seed, prefix=f"{cfg['prefix']}/objects", key=key
    )
    _assert_s3_source_retained(dataset, bucket=cfg["bucket"], key=key, fmt="json")


def test_catalog_parquet_ingestion_metadata(
    live_kamiwaza_client: KamiwazaClient, s3_seed: _S3Seed
) -> None:
    cfg = s3_seed.cfg
    key = f"{cfg['prefix']}/sales_data_10k.parquet"
    dataset = _ingest_s3_object(live_kamiwaza_client, s3_seed, prefix=key, key=key)
    _assert_s3_source_retained(dataset, bucket=cfg["bucket"], key=key, fmt="parquet")


def test_catalog_postgres_ingestion_metadata(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    created_datasets: list[str],
) -> None:
    pg = catalog_stack_environment["postgres"]
    dataset = _ingest_postgres_orders(live_kamiwaza_client, pg, created_datasets)
    properties = dataset.properties or {}
    assert dataset.platform == "postgres"
    assert properties.get("schema") == pg["schema"]
    assert properties.get("table") == "catalog_test_orders"
    assert dataset.dataset_schema is not None
    assert tuple(field.name for field in dataset.dataset_schema.fields) == ORDERS_FIELDS


def _create_container_or_skip(client: KamiwazaClient) -> str:
    """Create a container, or skip if this identity may not create one.

    At 1.2.1, ``reject_global_write`` refuses a container write to the Global
    workroom with 403 unless the caller has the ``admin`` role. So a 403 skips
    for an identity without that role and fails for an identity that has it.
    """
    try:
        return client.catalog.containers.create(
            ContainerCreate(
                name=f"sdk-catalog-{uuid4().hex[:8]}", platform="integration"
            )
        )
    except KamiwazaError as exc:
        if exc.status_code != 403 or "admin" in client.auth.get_current_user().roles:
            raise
    pytest.skip(
        "Container create returned 403 for an identity without the admin role; "
        "1.2.1 lets only admin create containers in the Global workroom"
    )


def test_catalog_container_link_sets_dataset_container_urn(
    live_kamiwaza_client: KamiwazaClient,
    s3_seed: _S3Seed,
    created_containers: list[str],
) -> None:
    # Created before ingestion, so a refused container write skips before the
    # workflow starts.
    container_urn = _create_container_or_skip(live_kamiwaza_client)
    created_containers.append(container_urn)
    small_key = s3_seed.cfg["small_key"]
    dataset = _ingest_s3_object(
        live_kamiwaza_client, s3_seed, prefix=small_key, key=small_key
    )
    containers = live_kamiwaza_client.catalog.containers
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


# --- Retrieval: jobs complete and return the seeded rows -------------------------


def test_catalog_object_ingestion_inline_retrieval(
    live_kamiwaza_client: KamiwazaClient, s3_seed: _S3Seed
) -> None:
    prefix = f"{s3_seed.cfg['prefix']}/objects"
    dataset = _ingest_s3_object(
        live_kamiwaza_client, s3_seed, prefix=prefix, key=f"{prefix}/sample.json"
    )
    inline = _completed_inline_job(live_kamiwaza_client, dataset.urn, fmt="json")
    seeded = s3_seed.file_root / "objects" / "sample.json"
    assert inline.data == [json.loads(seeded.read_text())]


def test_catalog_parquet_ingestion_inline_retrieval(
    live_kamiwaza_client: KamiwazaClient, s3_seed: _S3Seed
) -> None:
    key = f"{s3_seed.cfg['prefix']}/sales_data_10k.parquet"
    dataset = _ingest_s3_object(live_kamiwaza_client, s3_seed, prefix=key, key=key)
    inline = _completed_inline_job(live_kamiwaza_client, dataset.urn, fmt="parquet")
    _assert_rows_match_seed(inline.data, s3_seed.file_root / "sales_data_10k.parquet")


def test_catalog_inline_small_object_succeeds(
    live_kamiwaza_client: KamiwazaClient, s3_seed: _S3Seed
) -> None:
    small_key = s3_seed.cfg["small_key"]
    dataset = _ingest_s3_object(
        live_kamiwaza_client, s3_seed, prefix=small_key, key=small_key
    )
    inline = _completed_inline_job(live_kamiwaza_client, dataset.urn, fmt="parquet")
    _assert_rows_match_seed(inline.data, s3_seed.file_root / "inline-small.parquet")


def test_catalog_postgres_inline_retrieval(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    created_datasets: list[str],
) -> None:
    pg = catalog_stack_environment["postgres"]
    dataset = _ingest_postgres_orders(live_kamiwaza_client, pg, created_datasets)
    inline = _completed_inline_job(live_kamiwaza_client, dataset.urn, fmt="parquet")
    assert {
        (row["customer_name"], row["total"]) for row in inline.data
    } == SEEDED_ORDERS
    assert inline.row_count == len(SEEDED_ORDERS)


def test_catalog_sse_retrieval_emits_terminal_event(
    live_kamiwaza_client: KamiwazaClient, s3_seed: _S3Seed
) -> None:
    small_key = s3_seed.cfg["small_key"]
    dataset = _ingest_s3_object(
        live_kamiwaza_client, s3_seed, prefix=small_key, key=small_key
    )

    job = live_kamiwaza_client.retrieval.create_job(
        RetrievalRequest(
            dataset_urn=dataset.urn, transport="sse", format_hint="parquet"
        )
    )
    assert job.transport == TransportType.SSE
    events = _collect_stream(live_kamiwaza_client, job.job_id)
    names = [event.event for event in events]
    status = _terminal_status(
        live_kamiwaza_client, job.job_id, context=f"job {job.job_id}: events={names}"
    )

    context = f"job {job.job_id}: events={names}, job status={status.status}"
    rows = _stream_rows(events, context)
    _assert_rows_match_seed(rows, s3_seed.file_root / "inline-small.parquet")
    assert status.status == "COMPLETED", f"SSE job did not complete ({context})"


# --- Optional paths: not T02 coverage, excluded from the evidence map ------------


def test_catalog_inline_large_object_hits_threshold(
    live_kamiwaza_client: KamiwazaClient, s3_seed: _S3Seed
) -> None:
    oversized = s3_seed.file_root / "inline-large.parquet"
    if not oversized.is_file() or oversized.stat().st_size <= INLINE_MAX_BYTES_1_2_1:
        pytest.skip(
            f"Optional oversized-object path, not T02 coverage: {oversized.name} is "
            f"missing or not larger than {INLINE_MAX_BYTES_1_2_1} bytes (assumes the server "
            "uses the 1.2.1 default inline_max_bytes); catalog-stack setup needs pandas, "
            "numpy and pyarrow to generate it"
        )

    large_key = s3_seed.cfg["large_key"]
    dataset = _ingest_s3_object(
        live_kamiwaza_client, s3_seed, prefix=large_key, key=large_key
    )
    with pytest.raises(TransportNotSupportedError, match="inline threshold"):
        live_kamiwaza_client.retrieval.create_job(
            RetrievalRequest(
                dataset_urn=dataset.urn, transport="inline", format_hint="parquet"
            )
        )


def test_catalog_kafka_ingestion_metadata(
    live_kamiwaza_client: KamiwazaClient,
    catalog_stack_environment: dict[str, Any],
    created_datasets: list[str],
) -> None:
    # No skip of its own: setup-test-data.sh waits for the Kafka port, and the stack
    # fixture skips this test when setup fails.
    kafka = catalog_stack_environment["kafka"]
    response = live_kamiwaza_client.ingestion.run_active(
        "kafka",
        bootstrap_servers=kafka["bootstrap"],
    )
    created_datasets.extend(response.urns)
    assert response.errors == [], f"Kafka ingestion reported errors: {response.errors}"
    topics = [urn for urn in response.urns if kafka["topic"] in urn]
    assert len(topics) == 1, (
        f"expected one dataset for topic {kafka['topic']!r} among {response.urns}"
    )
    dataset = live_kamiwaza_client.catalog.datasets.get(topics[0])
    assert dataset.platform == "kafka"


def _slack_channels(channel: str) -> list[str]:
    """SLACK_TEST_CHANNELS as a list, or just ``channel`` when it names no channel."""
    configured = os.environ.get("SLACK_TEST_CHANNELS", "").split(",")
    return [item.strip() for item in configured if item.strip()] or [channel]


def _ingest_slack(
    client: KamiwazaClient, channels: list[str], team_id: str
) -> IngestResponse:
    """Ingest with SLACK_TEST_TOKEN; any failure is re-raised without the token.

    pytest can print a traceback frame's arguments, and its locals under
    --showlocals, so the token is read inline and never becomes either here, and the
    SDK, requests and urllib3 frames below that do hold the token are dropped with
    ``from None``.
    """
    __tracebackhide__ = True
    try:
        return client.ingestion.run_slack_ingest(
            channels=channels,
            token=os.environ["SLACK_TEST_TOKEN"],
            team_id=team_id,
            max_messages=3,
        )
    except Exception as exc:  # deliberately broad: every failure crosses those frames
        status = exc.status_code if isinstance(exc, KamiwazaError) else None
        raise AssertionError(
            f"Slack ingestion failed: {type(exc).__name__} (status {status})"
        ) from None


def test_catalog_slack_ingestion_metadata(
    live_kamiwaza_client: KamiwazaClient, created_datasets: list[str]
) -> None:
    if not all(os.environ.get(name) for name in SLACK_REQUIRED_ENV):
        pytest.skip(
            "Optional path, not T02 coverage: provide SLACK_TEST_TOKEN, SLACK_TEST_CHANNEL and "
            "SLACK_TEST_TEAM to exercise Slack ingestion"
        )

    channels = _slack_channels(os.environ["SLACK_TEST_CHANNEL"])
    response = _ingest_slack(
        live_kamiwaza_client, channels, os.environ["SLACK_TEST_TEAM"]
    )
    created_datasets.extend(response.urns)
    assert response.urns, "Slack ingestion returned no datasets"
    dataset = live_kamiwaza_client.catalog.datasets.get(response.urns[0])
    assert dataset.platform == "slack"
    resolved_channel = (dataset.properties or {}).get("channel_id") or channels[0]

    rows = live_kamiwaza_client.retrieval.slack_messages(
        response.urns[0],
        channels=[resolved_channel],
        max_messages=3,
        include_replies=False,
    )
    assert rows, "Slack retrieval did not return inline payload"
