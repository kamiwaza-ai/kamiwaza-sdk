"""Regression tests for the dataset, retrieval, and app-deploy workflows.

Built from the platform's own return types — ``IngestResponse``,
``IngestJobStatus``, ``RetrievalResult`` and ``Dataset`` — rather than from a
fake that answers any attribute. The bugs these cover (rows read off a result
that has none, counts read off a status that has none, a job identifier read
off a response that has none) all survived a permissive fake, because a fake
that returns a canned value for every attribute cannot fail on a field that
does not exist.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from kamiwaza_sdk.agent_tools.workflows import (
    complete_dataset_ingestion,
    data,
    deploy_app_from_garden,
    ingest_dataset_and_index,
    rag_query,
    run_source_ingestion,
)
from kamiwaza_sdk.agent_tools.workflows._contract import WORKFLOWS, Refusal
from kamiwaza_sdk.agent_tools.workflows.collaboration import AppRequest
from kamiwaza_sdk.agent_tools.workflows.data import DatasetTarget
from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.exceptions import TransportNotSupportedError
from kamiwaza_sdk.schemas.catalog import Dataset
from kamiwaza_sdk.schemas.ingestion import IngestJobStatus, IngestResponse
from kamiwaza_sdk.schemas.retrieval import (
    DatasetDescriptor,
    InlineData,
    RetrievalJob,
    RetrievalRequest,
    TransportType,
)
from kamiwaza_sdk.services.ingestion import IngestionService
from kamiwaza_sdk.services.retrieval import RetrievalResult

pytestmark = pytest.mark.unit

_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,sales,PROD)"


class _Catalog:
    """A catalogue that returns the ``Dataset`` the real service returns."""

    def __init__(self) -> None:
        self.created: list[str] = []

    def create_dataset(
        self, *, dataset_name: str, platform: str, environment: str
    ) -> Dataset:
        """Record the registration and return the stored entry."""
        self.created.append(dataset_name)
        return Dataset(name=dataset_name, platform=platform, environment=environment, urn=_URN)


class _Ingestion:
    """An ingestion service returning the real response and status models."""

    def __init__(
        self,
        run: IngestResponse | None = None,
        status: IngestJobStatus | None = None,
    ) -> None:
        self._run = run or IngestResponse()
        self._status = status
        self.runs: list[tuple[str, dict[str, Any]]] = []
        self.status_reads: list[str] = []

    def run_active(self, source_type: str, **options: Any) -> IngestResponse:
        """Return the immediate run's own report."""
        self.runs.append((source_type, options))
        return self._run

    def get_job_status(self, job_id: str) -> IngestJobStatus:
        """Return the scheduled job's status."""
        self.status_reads.append(job_id)
        assert self._status is not None, "test did not stage a job status"
        return self._status


class _Retrieval:
    """A retrieval service returning a real ``RetrievalResult``."""

    def __init__(self, result: RetrievalResult) -> None:
        self._result = result
        self.requests: list[RetrievalRequest] = []

    def materialize(self, request: RetrievalRequest) -> RetrievalResult:
        """Record the request and return the staged result."""
        self.requests.append(request)
        return self._result


class _ClosingStream:
    """A stream that records the close the raising path owes it."""

    def __init__(self) -> None:
        self.closed = 0

    def __iter__(self) -> Any:
        """Yield nothing: the workflow must not read a stream it refuses."""
        return iter(())

    def close(self) -> None:
        """Record the close."""
        self.closed += 1


class _Apps:
    """An apps service whose deployment status is the one just after install."""

    def __init__(self, status: str) -> None:
        self._status = status
        self.status_reads = 0

    def find_template(self, name: str, version: str | None = None) -> object:
        """Return a template stand-in, since only its presence is read."""
        return object()

    def install_by_name(self, name: str, **kwargs: Any) -> Any:
        """Return a deployment carrying an identifier."""
        return type("Deployment", (), {"id": "dep-1"})()

    def get_deployment_status(self, deployment_id: Any) -> str:
        """Return the status the deployment reports right now."""
        self.status_reads += 1
        return self._status


def _client(**services: Any) -> Any:
    """Build a client exposing only the services a workflow uses."""
    return type("FakeClient", (), services)()


def _inline_result(rows: list[dict[str, Any]], *, urn: str) -> RetrievalResult:
    """Build the result an inline retrieval really returns."""
    inline = InlineData(media_type="application/json", data=rows, row_count=len(rows))
    job = RetrievalJob(
        job_id="job-1",
        transport=TransportType.INLINE,
        status="COMPLETED",
        dataset=DatasetDescriptor(urn=urn, platform="snowflake"),
        inline=inline,
    )
    return RetrievalResult(job=job, inline=inline)


def _json_response(payload: dict[str, Any]) -> requests.Response:
    """Build the JSON response the transport hands back to the client."""
    response = requests.Response()
    response.status_code = 200
    response.url = "https://example.test/api"
    response._content = json.dumps(payload).encode()
    response.headers["Content-Type"] = "application/json"
    return response


class _RecordingResponse(requests.Response):
    """A streaming response that counts the closes it is given."""

    def __init__(self, body: bytes) -> None:
        """Hold the event-stream body the platform would send."""
        super().__init__()
        self.status_code = 200
        self.url = "https://example.test/api"
        self._content = body
        self._content_consumed = True
        self.headers["Content-Type"] = "text/event-stream"
        self.closes = 0

    def close(self) -> None:
        """Count the close instead of releasing a socket that never existed."""
        self.closes += 1


def test_rag_query_returns_the_rows_the_inline_payload_carries() -> None:
    """``RetrievalResult`` has no ``rows``, so reading one returned nothing.

    The rows live on ``result.inline.data``; the row count and media type are
    the other two fields ``InlineData`` publishes.
    """
    rows = [{"account": "acme", "revenue": 12}, {"account": "globex", "revenue": 7}]
    retrieval = _Retrieval(_inline_result(rows, urn=_URN))

    result = rag_query(_client(retrieval=retrieval), _URN, limit_rows=2)

    assert result["rows"] == rows
    assert result["row_count"] == 2
    assert result["media_type"] == "application/json"


def test_rag_query_attributes_the_dataset_the_platform_resolved() -> None:
    """Attribution comes off the result, not off the caller's own argument.

    The staged descriptor deliberately differs from the requested urn: a
    workflow echoing its argument back passes while attributing nothing.
    """
    resolved = "urn:li:dataset:(urn:li:dataPlatform:snowflake,sales_v2,PROD)"
    retrieval = _Retrieval(_inline_result([{"row": 1}], urn=resolved))

    result = rag_query(_client(retrieval=retrieval), _URN)

    assert result["dataset"] == resolved
    assert "citations" not in result, "InlineData carries no per-row source"


def test_rag_query_asks_for_the_transport_it_can_return() -> None:
    """Returning rows means asking for the payload transport, not ``auto``."""
    retrieval = _Retrieval(_inline_result([{"row": 1}], urn=_URN))

    rag_query(_client(retrieval=retrieval), _URN, columns=["row"])

    assert retrieval.requests[0].transport == TransportType.INLINE.value


def test_rag_query_declares_the_job_its_body_creates() -> None:
    """Its body calls ``retrieval.materialize``, which calls ``create_job``.

    That derives ``requires_approval=True`` and ``idempotent=False``, and a
    workflow declaring neither leaves a host never asking and reporting the
    repeat as ``safe_to_retry`` through ``envelopes.platform_fault``.
    """
    spec = WORKFLOWS["rag_query"]

    assert spec.approval_step, "a host never asks for what declares no approval"
    assert "retrieval" in spec.approval_step.lower()
    assert spec.idempotent is False
    assert spec.not_idempotent_because


def test_rag_query_refuses_a_transport_that_streams() -> None:
    """A stream cannot be returned as rows, and must not read as empty rows.

    The refusal also owes the stream a close: ``materialize`` opened the
    response before answering, so raising without closing abandons it.
    """
    job = RetrievalJob(
        job_id="job-2",
        transport=TransportType.SSE,
        status="RUNNING",
        dataset=DatasetDescriptor(urn=_URN, platform="snowflake"),
    )
    stream = _ClosingStream()
    retrieval = _Retrieval(RetrievalResult(job=job, stream=stream))

    with pytest.raises(TransportNotSupportedError, match="job-2"):
        rag_query(_client(retrieval=retrieval), _URN)

    assert stream.closed == 1, "raised while holding an open stream"


def test_rag_query_bounds_the_inline_payload_when_no_limit_is_given() -> None:
    """The transport is forced inline, so an unset limit is a whole dataset.

    The bound is the workflow's own default rather than the request model's,
    which is ``None``.
    """
    retrieval = _Retrieval(_inline_result([{"row": 1}], urn=_URN))

    rag_query(_client(retrieval=retrieval), _URN)

    assert retrieval.requests[0].limit_rows == 1000


def test_rag_query_sends_the_bound_through_a_real_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the real service and transport, not a service double.

    A hand-written retrieval double cannot show what crosses the wire, and
    the bound only holds if the platform is told about it.
    """
    posted: list[bytes | None] = []
    job = {
        "job_id": "job-7",
        "transport": "inline",
        "status": "COMPLETED",
        "dataset": {"urn": _URN, "platform": "snowflake"},
        "inline": {
            "media_type": "application/json",
            "data": [{"row": 1}],
            "row_count": 1,
        },
    }

    def send(self: requests.Session, prepared: Any, **kwargs: Any) -> Any:
        """Record the request body and answer with the inline job."""
        posted.append(prepared.body)
        return _json_response(job)

    monkeypatch.setattr(requests.Session, "send", send)
    client = KamiwazaClient(base_url="https://example.test/api")

    result = rag_query(client, _URN)

    assert result["rows"] == [{"row": 1}]
    assert json.loads(posted[0] or b"{}")["limit_rows"] == 1000


def test_rag_query_releases_the_streaming_response_it_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the real service opens the socket the refusal has to release.

    ``materialize`` hands back an SSE generator that has not started, and a
    generator closed before its first ``next`` runs no ``finally``. Closing
    the iterator releases the response only because ``stream_events`` owns it.
    """
    streamed = _RecordingResponse(b'event: chunk\ndata: {"row": 1}\n\n')
    job = {
        "job_id": "job-2",
        "transport": "sse",
        "status": "RUNNING",
        "dataset": {"urn": _URN, "platform": "snowflake"},
    }

    def send(self: requests.Session, prepared: Any, **kwargs: Any) -> Any:
        """Answer the job POST with the job and the stream GET with the body."""
        if prepared.url.endswith("/retrieval/jobs"):
            return _json_response(job)
        return streamed

    monkeypatch.setattr(requests.Session, "send", send)
    client = KamiwazaClient(base_url="https://example.test/api")

    with pytest.raises(TransportNotSupportedError, match="job-2"):
        rag_query(client, _URN)

    assert streamed.closes >= 1, "refused the stream but left the response open"


def test_running_an_ingestion_reports_only_what_the_run_model_carries() -> None:
    """``IngestResponse`` has ``urns``, ``status`` and ``errors``, no counts.

    ``IngestJobStatus`` has no row or schema count either, so the workflow
    publishes the urns the run created instead of counts that are always
    ``None``.
    """
    run = IngestResponse(urns=[_URN], status="success", errors=[])
    ingestion = _Ingestion(run=run)

    result = run_source_ingestion(_client(ingestion=ingestion), "s3", bucket="b")

    assert result == {"state": "success", "datasets": [_URN], "errors": []}
    assert ingestion.status_reads == [], "an immediate run has no job id to read"
    assert ingestion.runs == [("s3", {"bucket": "b"})]


def test_running_an_ingestion_declares_the_write_its_body_performs() -> None:
    """Its whole body is ``ingestion.run_active``, which creates dataset urns
    and has the route grant their owner access.

    ``ingestion.run_active`` derives ``requires_approval=True`` and
    ``idempotent=False`` in ``descriptors``, and a workflow wrapping that one
    call cannot publish less. ``envelopes.platform_fault`` reports
    ``idempotent`` as ``safe_to_retry``, so declaring it would tell a host a
    repeat run is free when it creates a second set of urns.
    """
    spec = WORKFLOWS["run_source_ingestion"]

    assert spec.approval_step, "a write with no approval step is never asked about"
    assert "ingestion" in spec.approval_step.lower()
    assert spec.idempotent is False
    assert spec.not_idempotent_because
    assert "prepare_dataset_ingestion" not in WORKFLOWS


def test_no_published_ingestion_sentence_promises_a_dry_run() -> None:
    """The docstring and the summary are what an agent and an approver read.

    Nothing in this module stages anything: the one run is immediate and the
    catalogue entry it feeds is a registration.
    """
    specs = [WORKFLOWS["run_source_ingestion"], WORKFLOWS["complete_dataset_ingestion"]]
    published = " ".join(
        [data.__doc__ or "", run_source_ingestion.__doc__ or ""]
        + [f"{spec.summary} {spec.terminal_artifact} {spec.approval_step or ''}" for spec in specs]
    ).lower()

    for promise in ("stag", "reversible", "dry run", "without promoting", "two-phase"):
        assert promise not in published, f"published text still promises {promise!r}"


def test_ingestion_service_docstrings_promise_no_field_the_models_lack() -> None:
    """These docstrings are the descriptions an agent reads before calling.

    ``IngestResponse`` has no job identifier and ``IngestJobStatus`` has no
    row count, so a description offering either sends an agent looking for a
    field that is never there.
    """
    assert "job_id" not in IngestResponse.model_fields
    assert not [name for name in IngestJobStatus.model_fields if "row" in name]
    published = {
        "run_active": (IngestionService.run_active.__doc__ or "").lower(),
        "get_job_status": (IngestionService.get_job_status.__doc__ or "").lower(),
    }

    assert "its job identifier" not in published["run_active"]
    assert "row count" not in published["get_job_status"].replace(
        "no row or schema count", ""
    )


def test_ingest_and_index_returns_the_urns_not_a_stringified_response() -> None:
    """``run_active`` returns no identifier, so no job handle is published."""
    run = IngestResponse(urns=[_URN], status="success", errors=["one row skipped"])
    catalog = _Catalog()
    ingestion = _Ingestion(run=run)
    target = DatasetTarget(name="sales", platform="snowflake")

    result = ingest_dataset_and_index(
        _client(catalog=catalog, ingestion=ingestion), target, "s3"
    )

    assert result == {
        "dataset": _URN,
        "ingested": [_URN],
        "state": "success",
        "errors": ["one row skipped"],
    }
    assert "job" not in result
    assert ingestion.status_reads == []


def test_promotion_refuses_a_job_reporting_no_state() -> None:
    """A status with no state is not a finished state, and must not promote."""
    catalog = _Catalog()
    ingestion = _Ingestion(status=IngestJobStatus(job_id="job-1", status=""))
    target = DatasetTarget(name="sales", platform="snowflake")

    outcome = complete_dataset_ingestion(
        _client(catalog=catalog, ingestion=ingestion), "job-1", target
    )

    assert isinstance(outcome, Refusal)
    assert "unknown" in outcome.shortfall
    assert catalog.created == [], "refused but registered anyway"


def test_promotion_refuses_a_state_it_does_not_recognise() -> None:
    """Fail closed: an unrecognised state is not evidence of a finished job."""
    catalog = _Catalog()
    ingestion = _Ingestion(status=IngestJobStatus(job_id="job-1", status="quiesced"))
    target = DatasetTarget(name="sales", platform="snowflake")

    outcome = complete_dataset_ingestion(
        _client(catalog=catalog, ingestion=ingestion), "job-1", target
    )

    assert isinstance(outcome, Refusal)
    assert catalog.created == []


def test_promotion_registers_once_a_job_reports_finished() -> None:
    """The other side of the gate: a finished job does promote."""
    catalog = _Catalog()
    ingestion = _Ingestion(
        status=IngestJobStatus(job_id="job-1", status="COMPLETED", created_urns=[_URN])
    )
    target = DatasetTarget(name="sales", platform="snowflake")

    outcome = complete_dataset_ingestion(
        _client(catalog=catalog, ingestion=ingestion), "job-1", target
    )

    assert outcome == {"dataset": _URN, "job": "job-1"}
    assert catalog.created == ["sales"]


def test_promotion_does_not_declare_itself_safe_to_retry() -> None:
    """It calls ``catalog.create_dataset``, the same act its sibling declares
    non-idempotent: a retry after a transport error duplicates the entry.

    ``envelopes.platform_fault`` reports ``safe_to_retry`` straight off this
    flag, so the flag is what a host acts on.
    """
    spec = WORKFLOWS["complete_dataset_ingestion"]

    assert spec.idempotent is False
    assert spec.not_idempotent_because


@pytest.mark.parametrize(
    "name",
    [
        "ingest_dataset_and_index",
        "run_source_ingestion",
        "enclave_ingest",
        "deploy_app_from_garden",
    ],
)
def test_a_workflow_that_never_waits_declares_no_polling_step(name: str) -> None:
    """Each of these makes one call and returns; a declared wait is a lie.

    A host reading ``polling_step`` schedules a bounded wait and a resume, and
    neither has anything to act on when the workflow returns immediately.
    """
    spec = WORKFLOWS[name]

    assert spec.polling_step is None
    assert spec.resume_hint is None


def test_app_deploy_publishes_the_status_it_read_without_claiming_it_settled() -> None:
    """A transient status is returned as a starting status, read once."""
    apps = _Apps("PENDING")

    result = deploy_app_from_garden(_client(apps=apps), AppRequest(name="kaizen"))

    assert result == {"deployment": "dep-1", "status": "PENDING"}
    assert apps.status_reads == 1, "one read, so nothing here polls"
