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

from typing import Any

import pytest

from kamiwaza_sdk.agent_tools.workflows import (
    complete_dataset_ingestion,
    deploy_app_from_garden,
    ingest_dataset_and_index,
    prepare_dataset_ingestion,
    rag_query,
)
from kamiwaza_sdk.agent_tools.workflows._contract import WORKFLOWS, Refusal
from kamiwaza_sdk.agent_tools.workflows.collaboration import AppRequest
from kamiwaza_sdk.agent_tools.workflows.data import DatasetTarget
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


def test_rag_query_refuses_a_transport_that_streams() -> None:
    """A stream cannot be returned as rows, and must not read as empty rows."""
    job = RetrievalJob(
        job_id="job-2",
        transport=TransportType.SSE,
        status="RUNNING",
        dataset=DatasetDescriptor(urn=_URN, platform="snowflake"),
    )
    retrieval = _Retrieval(RetrievalResult(job=job, stream=iter(())))

    with pytest.raises(TransportNotSupportedError, match="job-2"):
        rag_query(_client(retrieval=retrieval), _URN)


def test_staging_reports_only_what_the_run_model_carries() -> None:
    """``IngestResponse`` has ``urns``, ``status`` and ``errors``, no counts.

    ``IngestJobStatus`` has no row or schema count either, so the workflow
    publishes the urns the run created instead of counts that are always
    ``None``.
    """
    run = IngestResponse(urns=[_URN], status="success", errors=[])
    ingestion = _Ingestion(run=run)

    result = prepare_dataset_ingestion(_client(ingestion=ingestion), "s3", bucket="b")

    assert result == {"state": "success", "datasets": [_URN], "errors": []}
    assert ingestion.status_reads == [], "an immediate run has no job id to read"
    assert ingestion.runs == [("s3", {"bucket": "b"})]


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
        "prepare_dataset_ingestion",
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
