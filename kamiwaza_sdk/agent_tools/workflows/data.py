"""Dataset, retrieval, and enclave workflows.

Ingestion is immediate. ``ingestion.run_active`` runs the plugin at once,
creates the dataset urns it returns, and the route grants their owner access,
so a workflow over that call declares an approval step and no idempotence.
``IngestResponse`` carries ``urns``, ``status`` and ``errors``, and
``IngestJobStatus`` carries ``job_id``, ``last_run``, ``status``,
``error_count`` and ``created_urns``, so no row or schema count is published
here. Neither model reports one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kamiwaza_sdk.exceptions import TransportNotSupportedError
from kamiwaza_sdk.schemas.enclaves import ConnectorCreate
from kamiwaza_sdk.schemas.retrieval import RetrievalRequest, TransportType

from ._contract import Refusal, WorkflowSpec, register

__all__ = [
    "DatasetTarget",
    "complete_dataset_ingestion",
    "enclave_ingest",
    "ingest_dataset_and_index",
    "rag_query",
    "run_source_ingestion",
]

#: Job states the platform reports for finished work. Anything else — a state
#: the platform spells differently, or no state at all — means the job is not
#: known to have finished, and promoting from such a job catalogues a partial
#: dataset. The check fails closed for that reason.
_FINISHED = frozenset({"complete", "completed", "succeeded", "success"})

#: Rows one inline retrieval returns when the caller names no limit. The
#: transport is forced inline, so the platform sends the whole answer in one
#: response body and an unbounded limit is an unbounded payload.
_DEFAULT_LIMIT_ROWS = 1000


@dataclass(frozen=True, slots=True)
class DatasetTarget:
    """Where a dataset is registered in the catalogue.

    Three facts that always travel together, so they are one argument rather
    than three repeated at every call site.

    Attributes:
        name: Dataset name to register.
        platform: Platform the dataset belongs to.
        environment: Catalogue environment.
    """

    name: str
    platform: str
    environment: str = "PROD"

    def register(self, client: Any) -> Any:
        """Register this dataset in the catalogue.

        Args:
            client: The platform client.

        Returns:
            The created dataset.
        """
        return client.catalog.create_dataset(
            dataset_name=self.name,
            platform=self.platform,
            environment=self.environment,
        )


@register(
    WorkflowSpec(
        name="ingest_dataset_and_index",
        summary="Register a dataset, ingest it now, and return what the run produced.",
        terminal_artifact="The dataset identifier and the urns the run ingested.",
        polling_step=None,
        approval_step="Registering and ingesting the dataset.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Registering the same dataset name twice creates a second entry. "
            "Call get_dataset_catalog first to check."
        ),
    )
)
def ingest_dataset_and_index(
    client: Any, target: DatasetTarget, source_type: str
) -> dict[str, Any]:
    """Register a dataset and ingest it.

    There is no polling step and no job handle to resume from: ``run_active``
    runs the ingestion immediately and its ``IngestResponse`` is the outcome,
    carrying the urns it created, a status and any errors. The immediate run
    reports no identifier, so nothing here can be followed with a job-status
    call.

    Args:
        client: The platform client.
        target: Where to register the dataset.
        source_type: Ingestion source type.

    Returns:
        Mapping with ``dataset``, the ``ingested`` urns, the run's ``state``
        and the ``errors`` it reported.
    """
    dataset = target.register(client)
    run = client.ingestion.run_active(source_type)
    return {
        "dataset": str(getattr(dataset, "urn", None) or dataset),
        "ingested": list(run.urns),
        "state": run.status,
        "errors": list(run.errors),
    }


@register(
    WorkflowSpec(
        name="run_source_ingestion",
        summary="Run an ingestion now and return the urns it created.",
        terminal_artifact="The urns the run created, its state, and its errors.",
        polling_step=None,
        approval_step="Running the ingestion, which creates datasets.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Each call creates another set of dataset urns. Check "
            "get_dataset_catalog first."
        ),
    )
)
def run_source_ingestion(
    client: Any, source_type: str, **options: Any
) -> dict[str, Any]:
    """Run an ingestion for one source type and report what it created.

    The platform runs the plugin while the call is open, creates the dataset
    urns it returns, and grants their owner access. Nothing waits and no job
    identifier comes back.

    Args:
        client: The platform client.
        source_type: Ingestion source type.
        **options: Source-specific options.

    Returns:
        Mapping with the run's ``state``, the ``datasets`` urns it created and
        its ``errors``. ``IngestResponse`` reports no count.
    """
    run = client.ingestion.run_active(source_type, **options)
    return {
        "state": run.status,
        "datasets": list(run.urns),
        "errors": list(run.errors),
    }


@register(
    WorkflowSpec(
        name="complete_dataset_ingestion",
        summary="Catalogue a dataset for an ingestion job that reports finished.",
        terminal_artifact="The registered dataset identifier.",
        polling_step=None,
        approval_step="Registering the dataset for the finished job.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Registering twice creates a second entry under the same name. "
            "Check get_dataset_catalog first."
        ),
    )
)
def complete_dataset_ingestion(
    client: Any, job_id: str, target: DatasetTarget
) -> dict[str, Any] | Refusal:
    """Register a dataset once an ingestion job reports it finished.

    The job comes from ``ingestion.schedule_job``; an immediate run reports
    no identifier.

    Args:
        client: The platform client.
        job_id: The scheduled job ``get_job_status`` reports on.
        target: Where to register the dataset.

    Returns:
        Mapping with the registered ``dataset`` and its ``job``, or a
        :class:`Refusal` unless the job reports finished; no state at all is
        not finished.
    """
    status = client.ingestion.get_job_status(job_id)
    state = str(getattr(status, "status", "") or "").strip().lower()
    if state not in _FINISHED:
        reported = state or "unknown"
        return Refusal(
            reason=(
                "The ingestion job has not reported a finished state, so "
                "there is nothing to catalogue."
            ),
            shortfall=f"job {job_id} is {reported!r}",
        )
    dataset = target.register(client)
    return {"dataset": str(getattr(dataset, "urn", None) or dataset), "job": job_id}


@register(
    WorkflowSpec(
        name="rag_query",
        summary="Materialise rows from a dataset and return them with its urn.",
        terminal_artifact="The rows returned inline, with the dataset the platform resolved.",
        polling_step=None,
        approval_step="Running a retrieval, which creates a retrieval job.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Each call creates another retrieval job. The rows repeat; the "
            "job does not."
        ),
    )
)
def rag_query(
    client: Any,
    dataset_urn: str,
    *,
    limit_rows: int = _DEFAULT_LIMIT_ROWS,
    columns: list[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialise rows from a dataset and return them with the dataset's urn.

    Creates a retrieval job to do it, which is what the approval covers, and
    asks for the inline transport: the rows are the point. A streaming answer
    closes the iterator and raises.

    Args:
        client: The platform client.
        dataset_urn: Dataset to retrieve over.
        limit_rows: Most rows to return, 1000 unless given; the inline
            transport carries the whole answer at once.
        columns: Columns to return, or all when omitted.
        filters: Column filters to apply.

    Returns:
        Mapping with ``dataset``, ``rows``, ``row_count`` and ``media_type``.
        ``dataset`` is the urn the platform resolved, not the argument. No
        citations: the payload has no per-row source.

    Raises:
        TransportNotSupportedError: The platform answered on a streaming
            transport.
    """
    # Four arguments rather than a whole ``RetrievalRequest``: the request
    # model has eleven fields, and two of them — ``credential_override`` and
    # ``sdk_session`` — must never appear on an agent-facing surface, because
    # a published field is an invitation to set it.
    request = RetrievalRequest(
        dataset_urn=dataset_urn,
        transport=TransportType.INLINE.value,
        limit_rows=limit_rows,
        columns=columns,
        filters=filters,
    )
    result = client.retrieval.materialize(request)
    inline = result.inline
    if inline is None:
        # `materialize` opened the streaming response before it answered, so
        # the raise releases the iterator rather than abandoning it. Not every
        # iterator carries `close`, so the attribute is checked.
        # Measured before `stream_events` owned its response: closing an SSE
        # generator before its first `next` runs no `finally`, so the stubbed
        # response recorded 0 close calls. The service holds the response now,
        # which is what makes this call release the socket.
        closer = getattr(result.stream, "close", None)
        if callable(closer):
            closer()
        transport = getattr(result.job.transport, "value", result.job.transport)
        raise TransportNotSupportedError(
            f"retrieval job {result.job.job_id} answered on the {transport} "
            f"transport, which streams rather than returning rows; read it "
            f"with the retrieval stream or Flight helpers"
        )

    return {
        "dataset": result.job.dataset.urn,
        "rows": inline.data,
        "row_count": inline.row_count,
        "media_type": inline.media_type,
    }


@register(
    WorkflowSpec(
        name="enclave_ingest",
        summary="Register an enclave connector and trigger its first ingest.",
        terminal_artifact="The connector id and the status the trigger was accepted with.",
        polling_step=None,
        approval_step="Registering the connector.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Each call registers another connector. List connectors first to "
            "reuse an existing one."
        ),
    )
)
def enclave_ingest(
    client: Any,
    name: str,
    source_type: str,
    connector_type: str,
    connection_config: dict[str, Any],
    *,
    description: str | None = None,
) -> dict[str, Any]:
    """Create an enclave connector and start its ingest.

    Nothing waits: the trigger returns on acceptance and the enclave API
    publishes no per-run status call. Read the connector back to follow it.

    Args:
        client: The platform client.
        name: Name for the connector.
        source_type: Kind of source being connected.
        connector_type: Kind of connector to create for it.
        connection_config: How to reach the source.
        description: What this connector is for.

    Returns:
        Mapping with ``connector`` and the ``trigger`` status the platform
        accepted the run with.
    """
    payload = ConnectorCreate(
        name=name,
        source_type=source_type,
        connector_type=connector_type,
        connection_config=connection_config,
        description=description,
    )
    connector = client.enclaves.connectors.create(payload)
    connector_id = getattr(connector, "id", None) or getattr(
        connector, "connector_id", None
    )
    trigger = client.enclaves.connectors.trigger_ingest(connector_id)
    return {
        "connector": str(connector_id),
        "trigger": trigger.status,
    }
