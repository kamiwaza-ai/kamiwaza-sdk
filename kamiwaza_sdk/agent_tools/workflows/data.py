"""Dataset, retrieval, and enclave workflows.

Ingestion is split into a staged pair rather than one call, because a member
approving a promotion should see what the staging run produced first: the
dataset urns it created, the state it reported, and the errors it hit. Those
are the three facts the ingestion API returns — ``IngestResponse`` carries
``urns``, ``status`` and ``errors``, and ``IngestJobStatus`` carries
``job_id``, ``last_run``, ``status``, ``error_count`` and ``created_urns`` —
so no row or schema count is published here. Neither model reports one.
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
    "prepare_dataset_ingestion",
    "rag_query",
]

#: Job states the platform reports for finished work. Anything else — a state
#: the platform spells differently, or no state at all — means the job is not
#: known to have finished, and promoting from such a job catalogues a partial
#: dataset. The check fails closed for that reason.
_FINISHED = frozenset({"complete", "completed", "succeeded", "success"})


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
        name="prepare_dataset_ingestion",
        summary="Stage a dataset ingestion and report what it produced for inspection.",
        terminal_artifact="The urns the staging run created, its state, and its errors.",
        polling_step=None,
        approval_step=None,
        idempotent=True,
        reads_only=False,
        destructive=False,
    )
)
def prepare_dataset_ingestion(
    client: Any, source_type: str, **options: Any
) -> dict[str, Any]:
    """Stage an ingestion without promoting it.

    The first half of the two-phase pair. Nothing here is approval-bearing,
    because nothing is promoted: staging is reversible by abandoning it. The
    run returns its own outcome, so there is nothing to wait for and no job
    identifier to resume from.

    Args:
        client: The platform client.
        source_type: Ingestion source type.
        **options: Source-specific options.

    Returns:
        Mapping with the run's ``state``, the ``datasets`` urns it created and
        the ``errors`` it reported, for inspection before anyone approves a
        promotion. No row or schema count: ``IngestResponse`` reports neither.
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
        summary="Promote a staged ingestion after its report has been checked.",
        terminal_artifact="The promoted dataset identifier.",
        polling_step=None,
        approval_step="Promoting the staged ingestion.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Promoting twice registers the same dataset name a second time in "
            "the catalogue. Call get_dataset_catalog first to check."
        ),
    )
)
def complete_dataset_ingestion(
    client: Any, job_id: str, target: DatasetTarget
) -> dict[str, Any] | Refusal:
    """Promote a staged ingestion into the catalogue.

    The second half of the pair, and the only approval-bearing half. Splitting
    them is what lets a member see the staging run's report before agreeing to
    anything.

    Args:
        client: The platform client.
        job_id: Identifier of a scheduled ingestion job, the kind
            ``get_job_status`` reports on. An immediate staging run reports no
            identifier, so its own report is what a member inspects.
        target: Where to register the promoted dataset.

    Returns:
        Mapping with the promoted ``dataset`` and its ``job``, or a
        :class:`Refusal` when the staged job has not reported a finished
        state — including when it reports no state at all. Promoting from a
        job that is not known to have finished catalogues a partial dataset,
        so an unrecognised state refuses rather than promotes.
    """
    status = client.ingestion.get_job_status(job_id)
    state = str(getattr(status, "status", "") or "").strip().lower()
    if state not in _FINISHED:
        reported = state or "unknown"
        return Refusal(
            reason=(
                "The staged ingestion has not reported a finished state, so "
                "there is nothing to promote."
            ),
            shortfall=f"job {job_id} is {reported!r}",
        )
    dataset = target.register(client)
    return {"dataset": str(getattr(dataset, "urn", None) or dataset), "job": job_id}


@register(
    WorkflowSpec(
        name="rag_query",
        summary="Answer a question over a dataset and return the rows it retrieved.",
        terminal_artifact="The rows the platform returned inline, with the dataset it resolved.",
        polling_step=None,
        approval_step=None,
        idempotent=True,
        reads_only=False,
        destructive=False,
    )
)
def rag_query(
    client: Any,
    dataset_urn: str,
    *,
    limit_rows: int | None = None,
    columns: list[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retrieve over a dataset and return the rows with their source.

    Takes the four arguments a retrieval needs rather than a whole
    ``RetrievalRequest``. Two reasons, and the second is not about tokens: the
    request model has eleven fields, and two of them — ``credential_override``
    and ``sdk_session`` — must never appear on an agent-facing surface, because
    a published field is an invitation to set it.

    The request asks for the inline transport, because returning the rows is
    what this workflow is for. ``materialize`` answers a streaming or Flight
    transport with an iterator or a handshake instead of a payload, and
    neither can be returned as rows here, so that answer raises rather than
    reporting empty rows.

    Args:
        client: The platform client.
        dataset_urn: Dataset to retrieve over.
        limit_rows: Most rows to return.
        columns: Columns to return, or every column when omitted.
        filters: Column filters to apply.

    Returns:
        Mapping with ``dataset``, ``rows``, ``row_count`` and ``media_type``.
        ``dataset`` is the urn on the descriptor the platform resolved, not
        the argument echoed back, so an agent reports the dataset the rows
        actually came from. ``InlineData`` carries no per-row source, so no
        citation list is published: the dataset urn and the media type are the
        attribution the payload can support.

    Raises:
        TransportNotSupportedError: When the platform answered on a transport
            that streams instead of returning a payload. The job exists and is
            named in the message, so it can be read with the retrieval
            streaming or Flight helpers.
    """
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

    Takes the connector's own fields rather than a ``ConnectorCreate``, so the
    published shape names what to supply instead of nesting a model definition
    a caller has to read twice.

    Nothing here waits: ``trigger_ingest`` returns as soon as the run is
    accepted, its ``TriggerResponse`` carries a status and nothing else, and
    the enclave API publishes no per-run status call to poll. Read the
    connector back with the connector-get or connector-list operations to see
    where its ingest got to.

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
