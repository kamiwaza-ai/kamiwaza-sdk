"""Live integration tests for Context Service endpoints."""

from __future__ import annotations

import base64
import os
import time
from uuid import uuid4

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.services.context import ContextService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live,
    pytest.mark.withoutresponses,
]

DEFAULT_WORKROOM_ID = os.getenv(
    "KAMIWAZA_CONTEXT_WORKROOM_ID",
    ContextService.DEFAULT_WORKROOM_ID,
)
DEFAULT_CONTEXT_LLM_REPO = os.getenv(
    "KAMIWAZA_CONTEXT_LLM_REPO",
    "mlx-community/Qwen3-4B-4bit",
)
DEFAULT_CONTEXT_LLM_DEPLOY_TIMEOUT_SECONDS = float(
    os.getenv("KAMIWAZA_CONTEXT_LLM_DEPLOY_TIMEOUT_SECONDS", "600")
)
TEST_VECTOR = [round(index * 0.01, 4) for index in range(1, 33)]


def _sample_vector() -> list[float]:
    return list(TEST_VECTOR)


def _sdk_collection_name() -> str:
    # Milvus collection names must be alphanumeric/underscore; hyphens are rejected.
    return f"sdk_context_col_{uuid4().hex[:8]}"


def _context_service(live_kamiwaza_client) -> ContextService:
    service = live_kamiwaza_client.context
    assert isinstance(service, ContextService)
    return service


def _deployment_ready(deployment) -> bool:
    deployment_status = str(getattr(deployment, "status", "")).upper()
    if deployment_status != "DEPLOYED":
        return False
    instances = getattr(deployment, "instances", []) or []
    return any(
        str(getattr(instance, "status", "")).upper() == "DEPLOYED"
        for instance in instances
    )


def _find_existing_ready_deployment(client) -> str | None:
    for deployment in client.serving.list_deployments():
        if _deployment_ready(deployment):
            return str(deployment.id)
    return None


def _wait_for_vectordb_ready(
    service: ContextService,
    vectordb_id: str,
    *,
    workroom_id: str | None = None,
    timeout_seconds: float = 300.0,
    poll_seconds: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status = "unknown"

    while True:
        try:
            instance = service.get_vectordb(vectordb_id, workroom_id=workroom_id)
        except APIError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_seconds)
            continue

        last_status = str(instance.get("status", "unknown")).lower()
        endpoint = instance.get("endpoint")
        if last_status == "running" and endpoint:
            return
        if last_status in {"failed", "stopped"}:
            raise RuntimeError(
                f"VectorDB {vectordb_id} entered non-recoverable status {last_status}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for VectorDB {vectordb_id} to be ready "
                f"(last_status={last_status}, endpoint={endpoint})"
            )
        time.sleep(poll_seconds)


def _wait_for_ontology_ready(
    service: ContextService,
    ontology_id: str,
    *,
    timeout_seconds: float = 180.0,
    poll_seconds: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status = "unknown"

    while True:
        try:
            instance = service.get_ontology(ontology_id)
        except APIError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_seconds)
            continue

        last_status = str(instance.get("status", "unknown")).lower()
        endpoint = instance.get("endpoint")
        if last_status == "running" and endpoint:
            return
        if last_status in {"failed", "stopped"}:
            raise RuntimeError(
                f"Ontology {ontology_id} entered non-recoverable status {last_status}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for ontology {ontology_id} to be ready "
                f"(last_status={last_status}, endpoint={endpoint})"
            )
        time.sleep(poll_seconds)


def _create_temp_vectordb(
    service: ContextService,
    *,
    prefix: str,
    workroom_id: str | None = None,
) -> str:
    created = service.create_vectordb(
        name=f"{prefix}-{uuid4().hex[:8]}",
        engine="milvus",
        workroom_id=workroom_id,
    )
    vectordb_id = created["id"]
    assert vectordb_id
    try:
        _wait_for_vectordb_ready(
            service,
            vectordb_id,
            workroom_id=workroom_id,
        )
    except Exception:
        _safe_delete_vectordb(service, vectordb_id, workroom_id=workroom_id)
        raise
    return vectordb_id


def _safe_delete_vectordb(
    service: ContextService,
    vectordb_id: str,
    *,
    workroom_id: str | None = None,
) -> None:
    try:
        service.delete_vectordb(vectordb_id, workroom_id=workroom_id)
    except APIError:
        pass


def _create_temp_ontology(service: ContextService, *, prefix: str) -> str:
    created = service.create_ontology(
        name=f"{prefix}-{uuid4().hex[:8]}",
        backend="graphiti",
    )
    ontology_id = created["id"]
    assert ontology_id
    try:
        _wait_for_ontology_ready(service, ontology_id)
    except Exception:
        _safe_delete_ontology(service, ontology_id)
        raise
    return ontology_id


def _safe_delete_ontology(service: ContextService, ontology_id: str) -> None:
    try:
        service.delete_ontology(ontology_id)
    except APIError:
        pass


def _safe_delete_collection(
    service: ContextService,
    *,
    workroom_id: str,
    collection_name: str,
) -> None:
    try:
        service.delete_collection(
            workroom_id=workroom_id,
            collection_name=collection_name,
        )
    except APIError:
        pass


def _safe_delete_group(
    service: ContextService,
    *,
    ontology_id: str,
    group_id: str,
) -> None:
    try:
        service.delete_group(ontology_id, group_id=group_id)
    except APIError:
        pass


@pytest.fixture(scope="session")
def shared_context_service(
    live_server_available: str,
    live_session_api_key: str,
    resolved_live_password: str,
    live_username: str,
) -> ContextService:
    """Session-scoped context service client for shared provisioning fixtures."""
    os.environ.setdefault("KAMIWAZA_VERIFY_SSL", "false")

    api_key = live_session_api_key.strip()
    if api_key:
        client = KamiwazaClient(live_server_available, api_key=api_key)
    else:
        username = live_username.strip()
        password = resolved_live_password.strip()
        if not username or not password:
            pytest.skip(
                "Unable to build authenticated context client. "
                "Provide username/password (kz-login-backed) or KAMIWAZA_API_KEY."
            )
        client = KamiwazaClient(live_server_available)
        client.authenticator = UserPasswordAuthenticator(
            username,
            password,
            client._auth_service,
        )

    service = client.context
    assert isinstance(service, ContextService)
    return service


@pytest.fixture(scope="session")
def context_required_llm(
    shared_context_service: ContextService, ensure_repo_ready
) -> str:
    """Ensure a deployed platform LLM exists for ontology operations."""
    client = shared_context_service.client

    existing_deployment_id = _find_existing_ready_deployment(client)
    if existing_deployment_id:
        yield existing_deployment_id
        return

    model = ensure_repo_ready(client, DEFAULT_CONTEXT_LLM_REPO)
    configs = client.models.get_model_configs(model.id)
    if not configs:
        pytest.fail(
            f"No model configs available for context LLM repo '{DEFAULT_CONTEXT_LLM_REPO}'"
        )
    default_config = next((config for config in configs if config.default), configs[0])

    created_deployment_id = client.serving.deploy_model(
        model_id=str(model.id),
        m_config_id=default_config.id,
        lb_port=0,
        autoscaling=False,
        min_copies=1,
        starting_copies=1,
    )
    deployment = client.serving.wait_for_deployment(
        created_deployment_id,
        poll_interval=5,
        timeout=DEFAULT_CONTEXT_LLM_DEPLOY_TIMEOUT_SECONDS,
    )
    if not _deployment_ready(deployment):
        pytest.fail(
            "Context ontology prerequisite deployment is not ready: "
            f"deployment_id={deployment.id}, status={deployment.status}, "
            f"instance_statuses={[instance.status for instance in deployment.instances]}"
        )

    deployment_id = deployment.id
    try:
        yield str(deployment_id)
    finally:
        try:
            client.serving.stop_deployment(deployment_id=deployment_id, force=True)
        except Exception:
            pass


@pytest.fixture(scope="session")
def shared_vectordb(shared_context_service: ContextService) -> str:
    """Shared global VectorDB instance for non-destructive vector tests."""
    service = shared_context_service
    vectordb_id = _create_temp_vectordb(service, prefix="sdk-shared-vdb")
    try:
        yield vectordb_id
    finally:
        _safe_delete_vectordb(service, vectordb_id)


@pytest.fixture(scope="session")
def shared_workroom_vectordb(shared_context_service: ContextService) -> str:
    """Shared workroom-scoped VectorDB for collection/search/retrieve tests."""
    service = shared_context_service
    vectordb_id = _create_temp_vectordb(
        service,
        prefix="sdk-shared-vdb-workroom",
        workroom_id=DEFAULT_WORKROOM_ID,
    )
    try:
        yield vectordb_id
    finally:
        _safe_delete_vectordb(
            service,
            vectordb_id,
            workroom_id=DEFAULT_WORKROOM_ID,
        )


@pytest.fixture(scope="session")
def shared_ontology(
    shared_context_service: ContextService,
    context_required_llm: str,
) -> str:
    """Shared ontology instance for non-destructive ontology tests."""
    assert context_required_llm
    service = shared_context_service
    ontology_id = _create_temp_ontology(service, prefix="sdk-shared-ont")
    try:
        yield ontology_id
    finally:
        _safe_delete_ontology(service, ontology_id)


def test_context_health_endpoint(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    health = service.health()
    assert health["status"] == "healthy"
    assert health["service"] == "context"
    assert "features" in health


def test_context_required_llm_available(context_required_llm: str) -> None:
    """Ensure context ontology prerequisites include a running model deployment."""
    assert context_required_llm


def test_context_vectordb_lifecycle_global(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)

    name = f"sdk-context-vdb-{uuid4().hex[:8]}"
    created = service.create_vectordb(name=name, engine="milvus")
    vectordb_id = created["id"]

    try:
        fetched = service.get_vectordb(vectordb_id)
        assert fetched["id"] == vectordb_id
        assert fetched["name"] == name

        updated = service.update_vectordb(
            vectordb_id,
            config={"SDK_CONTEXT_TEST": "1"},
            replicas=1,
        )
        assert updated["id"] == vectordb_id

        scaled = service.scale_vectordb(vectordb_id, replicas=1)
        assert scaled["id"] == vectordb_id
    finally:
        _safe_delete_vectordb(service, vectordb_id)


def test_context_vectordb_insert_vectors_instance(
    live_kamiwaza_client,
    shared_vectordb: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    vectordb_id = shared_vectordb
    collection_name = _sdk_collection_name()

    inserted = service.insert_vectors(
        vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        metadata=[{"source": "sdk-context-live"}],
    )
    assert inserted["inserted_count"] == 1


def test_context_vectordb_insert_vectors_global(
    live_kamiwaza_client,
    shared_vectordb: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    vectordb_id = shared_vectordb
    collection_name = _sdk_collection_name()

    inserted = service.insert_vectors_global(
        vectordb_id=vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        metadata=[{"source": "sdk-context-live"}],
    )
    assert inserted["inserted_count"] == 1


def test_context_vectordb_query_vectors_instance(
    live_kamiwaza_client,
    shared_vectordb: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    vectordb_id = shared_vectordb
    collection_name = _sdk_collection_name()

    service.insert_vectors(
        vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        metadata=[{"source": "sdk-context-live"}],
    )
    queried = service.query_vectors(
        vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        limit=1,
    )
    assert isinstance(queried["results"], list)


def test_context_vectordb_query_vectors_global(
    live_kamiwaza_client,
    shared_vectordb: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    vectordb_id = shared_vectordb
    collection_name = _sdk_collection_name()

    service.insert_vectors_global(
        vectordb_id=vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        metadata=[{"source": "sdk-context-live"}],
    )
    queried = service.query_vectors_global(
        vectordb_id=vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        limit=1,
    )
    assert isinstance(queried["results"], list)


@pytest.mark.requires_embedding_model
def test_context_ontology_lifecycle_global(
    live_kamiwaza_client,
    context_required_llm: str,
) -> None:
    assert context_required_llm
    service = _context_service(live_kamiwaza_client)
    ontology_id = _create_temp_ontology(service, prefix="sdk-context-ontology")

    try:
        fetched = service.get_ontology(ontology_id)
        assert fetched["id"] == ontology_id

        health = service.ontology_health(ontology_id)
        assert health["ontology_id"] == ontology_id
        assert isinstance(health["healthy"], bool)
    finally:
        _safe_delete_ontology(service, ontology_id)


@pytest.mark.requires_embedding_model
def test_context_ontology_add_knowledge(
    live_kamiwaza_client,
    shared_ontology: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    ontology_id = shared_ontology
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    try:
        knowledge = service.add_knowledge(
            ontology_id,
            group_id=group_id,
            messages=[{"content": "sdk test message", "role": "user"}],
        )
        assert knowledge["group_id"] == group_id
    finally:
        _safe_delete_group(service, ontology_id=ontology_id, group_id=group_id)


@pytest.mark.requires_embedding_model
def test_context_ontology_add_entity(
    live_kamiwaza_client,
    shared_ontology: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    ontology_id = shared_ontology
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    try:
        service.add_knowledge(
            ontology_id,
            group_id=group_id,
            messages=[{"content": "seed ontology group", "role": "user"}],
        )
        entity = service.add_entity(
            ontology_id,
            group_id=group_id,
            name="SDK Entity",
            entity_type="concept",
        )
        assert entity.get("success") is True
    finally:
        _safe_delete_group(service, ontology_id=ontology_id, group_id=group_id)


@pytest.mark.requires_embedding_model
def test_context_ontology_search_knowledge(
    live_kamiwaza_client,
    shared_ontology: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    ontology_id = shared_ontology
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    search = service.search_knowledge(
        ontology_id,
        query="sdk query",
        group_ids=[group_id],
    )
    assert isinstance(search["facts"], list)


@pytest.mark.requires_embedding_model
def test_context_ontology_get_memory(
    live_kamiwaza_client,
    shared_ontology: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    ontology_id = shared_ontology
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    memory = service.get_memory(
        ontology_id,
        group_id=group_id,
        query="sdk memory",
    )
    assert isinstance(memory["facts"], list)


@pytest.mark.requires_embedding_model
def test_context_ontology_get_episodes(
    live_kamiwaza_client,
    shared_ontology: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    ontology_id = shared_ontology
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    episodes = service.get_episodes(
        ontology_id,
        group_id=group_id,
        last_n=5,
    )
    assert isinstance(episodes["episodes"], list)


@pytest.mark.requires_embedding_model
def test_context_ontology_delete_group(
    live_kamiwaza_client,
    context_required_llm: str,
) -> None:
    assert context_required_llm
    service = _context_service(live_kamiwaza_client)
    ontology_id = _create_temp_ontology(service, prefix="sdk-context-ont-delete-group")
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    try:
        service.add_knowledge(
            ontology_id,
            group_id=group_id,
            messages=[{"content": "seed ontology group", "role": "user"}],
        )
        deleted = service.delete_group(
            ontology_id,
            group_id=group_id,
        )
        assert deleted.get("success") is True
    finally:
        _safe_delete_ontology(service, ontology_id)


@pytest.mark.requires_embedding_model
def test_context_workroom_lists_and_job_creation(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID
    created_job_ids: list[str] = []

    vectordbs = service.list_vectordbs(workroom_id=workroom_id)
    ontologies = service.list_ontologies(workroom_id=workroom_id)
    assert isinstance(vectordbs, list)
    assert isinstance(ontologies, list)

    supported_types = service.get_supported_file_types()
    assert isinstance(supported_types, list)
    assert ".txt" in supported_types

    jobs_before = service.list_pipeline_jobs(workroom_id=workroom_id)
    assert isinstance(jobs_before, list)

    payload = base64.b64encode(b"hello context service").decode("utf-8")
    try:
        job = service.create_pipeline_job(
            workroom_id=workroom_id,
            files=[
                {
                    "filename": "sdk-context.txt",
                    "content_base64": payload,
                    "content_type": "text/plain",
                }
            ],
            config={"collection_name": f"sdk-context-{uuid4().hex[:8]}"},
        )
        assert "id" in job
        created_job_ids.append(job["id"])

        upload_job = service.upload_file(
            workroom_id=workroom_id,
            filename="sdk-upload.txt",
            file_content=b"upload through context API",
            content_type="text/plain",
            collection_name=f"sdk-upload-{uuid4().hex[:8]}",
            source_urn=f"urn:sdk:upload:{uuid4().hex[:8]}",
        )
        assert "id" in upload_job
        created_job_ids.append(upload_job["id"])
    finally:
        for created_job_id in created_job_ids:
            try:
                service.cancel_pipeline_job(
                    workroom_id=workroom_id,
                    job_id=created_job_id,
                )
            except APIError:
                pass


@pytest.mark.requires_embedding_model
def test_context_workroom_pipeline_followup_access(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID

    payload = base64.b64encode(b"hello context service").decode("utf-8")
    job = service.create_pipeline_job(
        workroom_id=workroom_id,
        files=[
            {
                "filename": "sdk-context-followup.txt",
                "content_base64": payload,
                "content_type": "text/plain",
            }
        ],
        config={"collection_name": f"sdk-context-followup-{uuid4().hex[:8]}"},
    )
    job_id = job["id"]

    try:
        fetched_job = service.get_pipeline_job(workroom_id=workroom_id, job_id=job_id)
        assert fetched_job["id"] == job_id
        service.cancel_pipeline_job(workroom_id=workroom_id, job_id=job_id)
    finally:
        try:
            service.cancel_pipeline_job(workroom_id=workroom_id, job_id=job_id)
        except APIError:
            pass


def test_context_workroom_collection_lifecycle(
    live_kamiwaza_client,
    shared_workroom_vectordb: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID
    assert shared_workroom_vectordb
    collection_name = _sdk_collection_name()
    created = False

    try:
        collections = service.list_collections(workroom_id=workroom_id)
        assert isinstance(collections, list)

        created_collection = service.create_collection(
            workroom_id=workroom_id,
            name=collection_name,
            dimension=384,
        )
        assert created_collection["display_name"] == collection_name
        created = True

        fetched = service.get_collection(
            workroom_id=workroom_id,
            collection_name=collection_name,
        )
        assert fetched["display_name"] == collection_name

        service.delete_collection(
            workroom_id=workroom_id,
            collection_name=collection_name,
        )
    finally:
        if created:
            _safe_delete_collection(
                service,
                workroom_id=workroom_id,
                collection_name=collection_name,
            )


@pytest.mark.requires_embedding_model
def test_context_search_contract(
    live_kamiwaza_client,
    shared_workroom_vectordb: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID
    assert shared_workroom_vectordb

    search = service.search(workroom_id=workroom_id, query="hello context")
    assert isinstance(search.get("results"), list)


@pytest.mark.requires_embedding_model
def test_context_retrieve_contract(
    live_kamiwaza_client,
    shared_workroom_vectordb: str,
) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID
    assert shared_workroom_vectordb

    retrieve = service.retrieve(workroom_id=workroom_id, query="hello context")
    assert isinstance(retrieve.get("sources"), list)


@pytest.mark.requires_embedding_model
def test_context_agentic_search_contract(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID

    # Agentic search should degrade gracefully even with no workroom-scoped VectorDB.
    agentic = service.agentic_search(
        workroom_id=workroom_id,
        query="hello context",
    )
    assert isinstance(agentic.get("results"), list)
