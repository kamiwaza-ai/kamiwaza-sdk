"""Live integration tests for Context Service endpoints."""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import APIError, NotFoundError
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


def _safe_scale_vectordb(
    service: ContextService,
    vectordb_id: str,
    *,
    replicas: int,
    workroom_id: str | None = None,
) -> None:
    try:
        service.scale_vectordb(
            vectordb_id, replicas=replicas, workroom_id=workroom_id
        )
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
    vectordb_id: str | None = None,
) -> None:
    try:
        service.delete_collection(
            workroom_id=workroom_id,
            collection_name=collection_name,
            vectordb_id=vectordb_id,
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
def context_required_llm(context_llm_prerequisite: str) -> str:
    """Expose the shared context LLM prerequisite for context tests."""
    return context_llm_prerequisite


def _is_stale_sdk_resource(resource: dict, max_age: timedelta) -> bool:
    """Return True if resource has an sdk-* name and is older than *max_age*."""
    if not resource.get("name", "").startswith("sdk-") or not resource.get("id"):
        return False
    created_at = resource.get("created_at", "")
    if not created_at:
        return True  # No timestamp — assume stale
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(
            created_at.replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return True
    return age >= max_age


_STALE_THRESHOLD = timedelta(minutes=15)


def _api_error_code(error: APIError) -> str | None:
    """Extract the stable server error code from an APIError payload."""
    payload = error.response_data
    if not isinstance(payload, dict):
        return None

    detail = payload.get("detail")
    if isinstance(detail, dict):
        for key in ("error", "code", "reason"):
            value = detail.get(key)
            if isinstance(value, str):
                return value

    for key in ("error", "code", "reason"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


@pytest.fixture(scope="session", autouse=True)
def _cleanup_stale_sdk_vdbs(shared_context_service: ContextService) -> None:
    """Delete leftover sdk-* VDB/ontology instances from prior crashed runs.

    Only targets resources older than 15 minutes to avoid interfering with
    concurrent test sessions or manually-created resources.
    """
    service = shared_context_service
    for workroom_id in (None, DEFAULT_WORKROOM_ID):
        try:
            vdbs = service.list_vectordbs(workroom_id=workroom_id)
        except APIError:
            vdbs = []
        for vdb in vdbs:
            if _is_stale_sdk_resource(vdb, _STALE_THRESHOLD):
                _safe_delete_vectordb(
                    service, vdb["id"], workroom_id=workroom_id
                )
        try:
            ontologies = service.list_ontologies(workroom_id=workroom_id)
        except APIError:
            ontologies = []
        for ont in ontologies:
            if _is_stale_sdk_resource(ont, _STALE_THRESHOLD):
                _safe_delete_ontology(service, ont["id"])


@pytest.fixture(scope="session")
def session_workroom(
    shared_context_service: ContextService,
) -> Generator[str, None, None]:
    """Per-session writable workroom for Context Service write-path tests.

    Room-scoped Context routes require an explicit non-Global workroom scope.
    Exercise the backend enter endpoint as a checked lifecycle seam, while
    Context calls below pass explicit workroom_id so authority is not inferred
    from SDK-local session state.
    """
    workrooms = shared_context_service.client.workrooms
    workroom = workrooms.create(
        f"sdk-ctx-session-{uuid4().hex[:8]}",
        "ephemeral",
        description="Ephemeral workroom for SDK context live tests",
    )
    workroom_id = str(workroom.id)
    try:
        entered = workrooms.enter(workroom_id)
        assert str(entered.workroom_id) == workroom_id
        yield workroom_id
    finally:
        try:
            workrooms.leave()
        except (APIError, ValidationError):
            pass
        # delete() raises NotFoundError (a sibling of APIError, not a subclass)
        # when the workroom is already gone, so catch both to keep teardown
        # best-effort -- matching the sibling test_workroom_isolation_live.py.
        try:
            workrooms.delete(workroom_id)
        except (APIError, NotFoundError):
            pass


@pytest.fixture(scope="session")
def shared_workroom_vectordb(
    shared_context_service: ContextService,
    session_workroom: str,
) -> Generator[str, None, None]:
    """Shared workroom-scoped VectorDB for collection/search/retrieve tests."""
    service = shared_context_service
    vectordb_id = _create_temp_vectordb(
        service,
        prefix="sdk-shared-vdb-workroom",
        workroom_id=session_workroom,
    )
    try:
        yield vectordb_id
    finally:
        _safe_delete_vectordb(
            service,
            vectordb_id,
            workroom_id=session_workroom,
        )


@pytest.fixture(scope="session")
def shared_ontology(
    shared_context_service: ContextService,
    context_required_llm: str,
) -> Generator[str, None, None]:
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


def test_context_vectordb_create_without_workroom_requires_scope(
    live_kamiwaza_client,
) -> None:
    """VectorDB creation is room-scoped and requires a non-Global workroom."""
    service = _context_service(live_kamiwaza_client)

    with pytest.raises(APIError) as exc_info:
        service.create_vectordb(
            name=f"sdk-context-vdb-global-{uuid4().hex[:8]}",
            engine="milvus",
        )

    assert exc_info.value.status_code == 400
    assert _api_error_code(exc_info.value) == "workroom_scope_required"


# A dedicated non-Global VectorDB *instance* lifecycle test (create/scale/
# delete the VDB itself in a user workroom) stays in the kamiwaza repo's
# tests/integration/services/context. Workroom-scoped *write* coverage on the
# SDK side runs through the ``session_workroom`` fixture instead: the
# collection, pipeline, search, and retrieve tests below exercise writes
# against a real per-session workroom. The ``X-Workroom-ID`` header the SDK
# sets is preserved end-to-end -- the istio ingress strip-identity-headers
# filter explicitly exempts it (it is a client scope *request*, authorized
# server-side against verified workroom membership, not a spoofable identity
# assertion), so the header reaches the Context Service unmodified.


def test_context_vectordb_insert_vectors_instance(
    shared_context_service: ContextService,
    session_workroom: str,
    shared_workroom_vectordb: str,
) -> None:
    service = shared_context_service
    vectordb_id = shared_workroom_vectordb
    collection_name = _sdk_collection_name()

    inserted = service.insert_vectors(
        vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        metadata=[{"source": "sdk-context-live"}],
        workroom_id=session_workroom,
    )
    assert inserted["inserted_count"] == 1


def test_context_vectordb_insert_vectors_global(
    shared_context_service: ContextService,
    session_workroom: str,
    shared_workroom_vectordb: str,
) -> None:
    service = shared_context_service
    vectordb_id = shared_workroom_vectordb
    collection_name = _sdk_collection_name()

    inserted = service.insert_vectors_global(
        vectordb_id=vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        metadata=[{"source": "sdk-context-live"}],
        workroom_id=session_workroom,
    )
    assert inserted["inserted_count"] == 1


def test_context_vectordb_query_vectors_instance(
    shared_context_service: ContextService,
    session_workroom: str,
    shared_workroom_vectordb: str,
) -> None:
    service = shared_context_service
    vectordb_id = shared_workroom_vectordb
    collection_name = _sdk_collection_name()

    service.insert_vectors(
        vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        metadata=[{"source": "sdk-context-live"}],
        workroom_id=session_workroom,
    )
    queried = service.query_vectors(
        vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        limit=1,
        workroom_id=session_workroom,
    )
    assert isinstance(queried["results"], list)


def test_context_vectordb_query_vectors_global(
    shared_context_service: ContextService,
    session_workroom: str,
    shared_workroom_vectordb: str,
) -> None:
    service = shared_context_service
    vectordb_id = shared_workroom_vectordb
    collection_name = _sdk_collection_name()

    service.insert_vectors_global(
        vectordb_id=vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        metadata=[{"source": "sdk-context-live"}],
        workroom_id=session_workroom,
    )
    queried = service.query_vectors_global(
        vectordb_id=vectordb_id,
        collection_name=collection_name,
        vectors=[_sample_vector()],
        limit=1,
        workroom_id=session_workroom,
    )
    assert isinstance(queried["results"], list)


def _vectordb_replicas(instance: dict) -> int | None:
    """Read the replica count from a VectorDB instance payload.

    The server may surface ``replicas`` at the top level or nested under
    ``config`` depending on engine/version, so check both before giving up.
    """
    for source in (instance, instance.get("config") or {}):
        if not isinstance(source, dict):
            continue
        value = source.get("replicas")
        if isinstance(value, bool):  # guard: bool is an int subclass
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def test_context_vectordb_update_round_trips(
    shared_context_service: ContextService,
    session_workroom: str,
    shared_workroom_vectordb: str,
) -> None:
    """update_vectordb mutation is observable via a follow-up get_vectordb.

    Assertion posture is API round-trip: confirm the PUT is accepted and the
    requested config/replicas change is reflected when the instance is re-read.
    Physical replica provisioning is out of scope (local Milvus is single-node).
    """
    service = shared_context_service
    vectordb_id = shared_workroom_vectordb

    before = service.get_vectordb(vectordb_id, workroom_id=session_workroom)
    baseline_replicas = _vectordb_replicas(before) or 1
    config_marker = f"sdk-live-update-{uuid4().hex[:8]}"

    updated = service.update_vectordb(
        vectordb_id,
        config={"sdk_test_marker": config_marker},
        replicas=baseline_replicas,
        workroom_id=session_workroom,
    )
    assert updated["id"] == vectordb_id

    refetched = service.get_vectordb(vectordb_id, workroom_id=session_workroom)
    assert refetched["id"] == vectordb_id
    config = refetched.get("config")
    assert isinstance(config, dict)
    assert config.get("sdk_test_marker") == config_marker


def test_context_vectordb_scale_reflects_requested_replicas(
    shared_context_service: ContextService,
    session_workroom: str,
    shared_workroom_vectordb: str,
) -> None:
    """scale_vectordb is accepted and the response echoes the requested replicas.

    Assertion posture is API round-trip, not physical provisioning: a single-node
    local Milvus may clamp the effective replica count, so we assert the call
    succeeds and the returned instance reflects the requested ``replicas``, then
    scale back to the baseline (session teardown also deletes the VDB).
    """
    service = shared_context_service
    vectordb_id = shared_workroom_vectordb

    before = service.get_vectordb(vectordb_id, workroom_id=session_workroom)
    baseline_replicas = _vectordb_replicas(before) or 1
    target_replicas = baseline_replicas + 1

    try:
        scaled = service.scale_vectordb(
            vectordb_id,
            replicas=target_replicas,
            workroom_id=session_workroom,
        )
        assert scaled["id"] == vectordb_id
        assert _vectordb_replicas(scaled) == target_replicas
    finally:
        _safe_scale_vectordb(
            service,
            vectordb_id,
            replicas=baseline_replicas,
            workroom_id=session_workroom,
        )


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
def test_context_workroom_lists_and_job_creation(
    shared_context_service: ContextService,
    session_workroom: str,
) -> None:
    service = shared_context_service
    workroom_id = session_workroom
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
def test_context_workroom_pipeline_followup_access(
    shared_context_service: ContextService,
    session_workroom: str,
) -> None:
    service = shared_context_service
    workroom_id = session_workroom

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
    shared_context_service: ContextService,
    session_workroom: str,
    shared_workroom_vectordb: str,
) -> None:
    service = shared_context_service
    workroom_id = session_workroom
    assert shared_workroom_vectordb
    collection_name = _sdk_collection_name()
    created = False

    try:
        collections = service.list_collections(
            workroom_id=workroom_id,
            vectordb_id=shared_workroom_vectordb,
        )
        assert isinstance(collections, list)

        created_collection = service.create_collection(
            workroom_id=workroom_id,
            name=collection_name,
            dimension=384,
            vectordb_id=shared_workroom_vectordb,
        )
        assert created_collection["display_name"] == collection_name
        created = True

        fetched = service.get_collection(
            workroom_id=workroom_id,
            collection_name=collection_name,
            vectordb_id=shared_workroom_vectordb,
        )
        assert fetched["display_name"] == collection_name

        service.delete_collection(
            workroom_id=workroom_id,
            collection_name=collection_name,
            vectordb_id=shared_workroom_vectordb,
        )
    finally:
        if created:
            _safe_delete_collection(
                service,
                workroom_id=workroom_id,
                collection_name=collection_name,
                vectordb_id=shared_workroom_vectordb,
            )


@pytest.mark.requires_embedding_model
def test_context_search_contract(
    shared_context_service: ContextService,
    session_workroom: str,
    shared_workroom_vectordb: str,
) -> None:
    service = shared_context_service
    workroom_id = session_workroom
    assert shared_workroom_vectordb

    search = service.search(
        workroom_id=workroom_id,
        query="hello context",
        vectordb_id=shared_workroom_vectordb,
    )
    assert isinstance(search.get("results"), list)


@pytest.mark.requires_embedding_model
def test_context_retrieve_contract(
    shared_context_service: ContextService,
    session_workroom: str,
    shared_workroom_vectordb: str,
) -> None:
    service = shared_context_service
    workroom_id = session_workroom
    assert shared_workroom_vectordb

    retrieve = service.retrieve(
        workroom_id=workroom_id,
        query="hello context",
        vectordb_id=shared_workroom_vectordb,
    )
    assert isinstance(retrieve.get("sources"), list)


@pytest.mark.requires_embedding_model
def test_context_agentic_search_contract(
    shared_context_service: ContextService,
    session_workroom: str,
    shared_workroom_vectordb: str,
    context_required_llm: str,
) -> None:
    # agentic_search always sends synthesize=True, so it needs a context LLM in
    # addition to an embedding model -- gate on both prerequisites (like the
    # ontology tests) so the test skips, rather than fails, on an LLM-less host.
    assert context_required_llm
    service = shared_context_service
    workroom_id = session_workroom
    assert shared_workroom_vectordb

    result = service.agentic_search(
        workroom_id=workroom_id,
        query="hello context",
        vectordb_id=shared_workroom_vectordb,
    )
    assert isinstance(result.get("results"), list)
    assert isinstance(result.get("sources"), list)
    # synthesize=True is always sent, so the unified response must carry the
    # synthesis/citations fields (content depends on the LLM); assert presence
    # so a server-side regression that silently drops synthesis is caught.
    assert "synthesis" in result
    assert "citations" in result
