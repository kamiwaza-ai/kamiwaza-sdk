"""Dataset, retrieval, and enclave workflows.

Ingestion is split into a staged pair rather than one call, because a member
approving a promotion should see real row and schema counts first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kamiwaza_sdk.schemas.enclaves import ConnectorCreate
from kamiwaza_sdk.schemas.retrieval import RetrievalRequest

from ._contract import Refusal, WorkflowSpec, register

__all__ = [
    "DatasetTarget",
    "complete_dataset_ingestion",
    "enclave_ingest",
    "ingest_dataset_and_index",
    "prepare_dataset_ingestion",
    "rag_query",
]

#: Job states the platform reports for finished work. Anything else means the
#: job is still moving, and promoting from a moving job catalogues a partial
#: dataset.
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


def _job_id(job: Any) -> str:
    """Return a job's identifier, however the platform spelled it.

    Args:
        job: A job or job response.

    Returns:
        The identifier as a string.
    """
    return str(getattr(job, "job_id", None) or getattr(job, "id", None) or job)


@register(
    WorkflowSpec(
        name="ingest_dataset_and_index",
        summary="Register a dataset, ingest it, and return the ingestion job.",
        terminal_artifact="The dataset identifier and the ingestion job to follow.",
        polling_step="Waiting for the ingestion job to reach a terminal state.",
        approval_step="Registering and ingesting the dataset.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Registering the same dataset name twice creates a second entry. "
            "Call get_dataset_catalog first to check."
        ),
        resume_hint="get_job_status_ingestion",
    )
)
def ingest_dataset_and_index(
    client: Any, target: DatasetTarget, source_type: str
) -> dict[str, Any]:
    """Register a dataset and start ingesting it.

    Args:
        client: The platform client.
        target: Where to register the dataset.
        source_type: Ingestion source type.

    Returns:
        Mapping with ``dataset``, ``job`` and the job's ``state``.
    """
    dataset = target.register(client)
    job = client.ingestion.run_active(source_type)
    status = client.ingestion.get_job_status(_job_id(job))
    return {
        "dataset": str(getattr(dataset, "urn", None) or dataset),
        "job": _job_id(job),
        "state": getattr(status, "status", None),
    }


@register(
    WorkflowSpec(
        name="prepare_dataset_ingestion",
        summary="Stage a dataset ingestion and report its counts for inspection.",
        terminal_artifact="A staged ingestion with its row and schema counts.",
        polling_step="Waiting for the staging job to reach a terminal state.",
        approval_step=None,
        idempotent=True,
        reads_only=False,
        destructive=False,
        resume_hint="get_job_status_ingestion",
    )
)
def prepare_dataset_ingestion(
    client: Any, source_type: str, **options: Any
) -> dict[str, Any]:
    """Stage an ingestion without promoting it.

    The first half of the two-phase pair. Nothing here is approval-bearing,
    because nothing is promoted: staging is reversible by abandoning it.

    Args:
        client: The platform client.
        source_type: Ingestion source type.
        **options: Source-specific options.

    Returns:
        Mapping with ``job``, ``rows``, ``schema_fields`` and ``state``, for
        inspection before anyone approves a promotion.
    """
    job = client.ingestion.run_active(source_type, **options)
    status = client.ingestion.get_job_status(_job_id(job))
    return {
        "job": _job_id(job),
        "rows": getattr(status, "rows", None),
        "schema_fields": getattr(status, "schema_fields", None),
        "state": getattr(status, "status", None),
    }


@register(
    WorkflowSpec(
        name="complete_dataset_ingestion",
        summary="Promote a staged ingestion after its counts have been checked.",
        terminal_artifact="The promoted dataset identifier.",
        polling_step=None,
        approval_step="Promoting the staged ingestion.",
        idempotent=True,
        reads_only=False,
        destructive=False,
    )
)
def complete_dataset_ingestion(
    client: Any, job_id: str, target: DatasetTarget
) -> dict[str, Any] | Refusal:
    """Promote a staged ingestion into the catalogue.

    The second half of the pair, and the only approval-bearing half. Splitting
    them is what lets a member see real counts before agreeing to anything.

    Args:
        client: The platform client.
        job_id: Identifier of the staged job.
        target: Where to register the promoted dataset.

    Returns:
        Mapping with the promoted ``dataset`` and its ``job``, or a
        :class:`Refusal` when the staged job has not finished — promoting from
        a moving job catalogues a partial dataset.
    """
    status = client.ingestion.get_job_status(job_id)
    state = str(getattr(status, "status", "")).lower()
    if state and state not in _FINISHED:
        return Refusal(
            reason=(
                "The staged ingestion has not finished, so there is nothing "
                "to promote."
            ),
            shortfall=f"job {job_id} is {state!r}",
        )
    dataset = target.register(client)
    return {"dataset": str(getattr(dataset, "urn", None) or dataset), "job": job_id}


@register(
    WorkflowSpec(
        name="rag_query",
        summary="Answer a question over a dataset and return its citations.",
        terminal_artifact="The retrieved rows and the dataset they came from.",
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
    """Retrieve over a dataset and return results with their source.

    Takes the four arguments a retrieval needs rather than a whole
    ``RetrievalRequest``. Two reasons, and the second is not about tokens: the
    request model has eleven fields, and two of them — ``credential_override``
    and ``sdk_session`` — must never appear on an agent-facing surface, because
    a published field is an invitation to set it.

    Args:
        client: The platform client.
        dataset_urn: Dataset to retrieve over.
        limit_rows: Most rows to return.
        columns: Columns to return, or every column when omitted.
        filters: Column filters to apply.

    Returns:
        Mapping with ``dataset``, ``rows`` and ``citations``. Citations are not
        decoration: an answer an agent cannot attribute is an answer a member
        cannot check.
    """
    request = RetrievalRequest(
        dataset_urn=dataset_urn,
        limit_rows=limit_rows,
        columns=columns,
        filters=filters,
    )
    result = client.retrieval.materialize(request)
    dataset = dataset_urn
    return {
        "dataset": dataset,
        "rows": getattr(result, "rows", None),
        "citations": [dataset] if dataset else [],
    }


@register(
    WorkflowSpec(
        name="enclave_ingest",
        summary="Register an enclave connector and trigger its first ingest.",
        terminal_artifact="The connector id and the triggered ingest's status.",
        polling_step="Waiting for the triggered ingest to reach a terminal state.",
        approval_step="Registering the connector.",
        idempotent=False,
        reads_only=False,
        destructive=False,
        not_idempotent_because=(
            "Each call registers another connector. List connectors first to "
            "reuse an existing one."
        ),
        resume_hint="get_enclaves_connectors",
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

    Args:
        client: The platform client.
        name: Name for the connector.
        source_type: Kind of source being connected.
        connector_type: Kind of connector to create for it.
        connection_config: How to reach the source.
        description: What this connector is for.

    Returns:
        Mapping with ``connector`` and the ``trigger`` response.
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
        "trigger": getattr(trigger, "status", None) or str(trigger),
    }
