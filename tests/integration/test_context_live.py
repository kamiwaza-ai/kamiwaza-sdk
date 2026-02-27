"""Live integration tests for Context Service endpoints."""

from __future__ import annotations

import base64
import os
import time
from uuid import uuid4

import pytest

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
TEST_VECTOR = [round(index * 0.01, 4) for index in range(1, 33)]


def _sample_vector() -> list[float]:
    return list(TEST_VECTOR)


def _error_detail(exc: APIError) -> str:
    if isinstance(exc.response_data, dict):
        detail = exc.response_data.get("detail")
        if isinstance(detail, dict):
            error = detail.get("error")
            if isinstance(error, str):
                return error
        if isinstance(detail, str):
            return detail
    return exc.response_text or str(exc)


def _xfail_known_defect(
    exc: APIError,
    *,
    defect_id: str,
    reason: str,
    statuses: set[int],
    detail_contains: tuple[str, ...],
) -> None:
    assert exc.status_code in statuses
    detail = _error_detail(exc).lower()
    assert any(token.lower() in detail for token in detail_contains), detail
    pytest.xfail(
        f"{defect_id}: {reason} (status={exc.status_code}, detail={detail})"
    )


def _context_service(live_kamiwaza_client) -> ContextService:
    service = live_kamiwaza_client.context
    assert isinstance(service, ContextService)
    return service


def _wait_for_vectordb_ready(
    service: ContextService,
    vectordb_id: str,
    *,
    timeout_seconds: float = 300.0,
    poll_seconds: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status = "unknown"

    while True:
        try:
            instance = service.get_vectordb(vectordb_id)
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


def _create_temp_vectordb(service: ContextService, *, prefix: str) -> str:
    created = service.create_vectordb(
        name=f"{prefix}-{uuid4().hex[:8]}",
        engine="milvus",
    )
    vectordb_id = created["id"]
    assert vectordb_id
    try:
        _wait_for_vectordb_ready(service, vectordb_id)
    except Exception:
        _safe_delete_vectordb(service, vectordb_id)
        raise
    return vectordb_id


def _safe_delete_vectordb(service: ContextService, vectordb_id: str) -> None:
    try:
        service.delete_vectordb(vectordb_id)
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


def test_context_health_endpoint(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    health = service.health()
    assert health["status"] == "healthy"
    assert health["service"] == "context"
    assert "features" in health


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


def test_context_vectordb_insert_vectors_instance(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    vectordb_id = _create_temp_vectordb(service, prefix="sdk-context-vdb-insert-inst")
    collection_name = f"sdk-context-col-{uuid4().hex[:8]}"

    try:
        inserted = service.insert_vectors(
            vectordb_id,
            collection_name=collection_name,
            vectors=[_sample_vector()],
            metadata=[{"source": "sdk-context-live"}],
        )
        assert inserted["inserted_count"] == 1
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-004",
            reason="VectorDB instance-scoped insert fails after lifecycle create",
            statuses={403, 404, 500},
            detail_contains=("not found", "workroom_access_denied", "failed to insert"),
        )
    finally:
        _safe_delete_vectordb(service, vectordb_id)


def test_context_vectordb_insert_vectors_global(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    vectordb_id = _create_temp_vectordb(service, prefix="sdk-context-vdb-insert-global")
    collection_name = f"sdk-context-col-{uuid4().hex[:8]}"

    try:
        inserted = service.insert_vectors_global(
            vectordb_id=vectordb_id,
            collection_name=collection_name,
            vectors=[_sample_vector()],
            metadata=[{"source": "sdk-context-live"}],
        )
        assert inserted["inserted_count"] == 1
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-004",
            reason="VectorDB global insert fails after lifecycle create",
            statuses={403, 404, 500},
            detail_contains=("not found", "workroom_access_denied", "failed to insert"),
        )
    finally:
        _safe_delete_vectordb(service, vectordb_id)


def test_context_vectordb_query_vectors_instance(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    vectordb_id = _create_temp_vectordb(service, prefix="sdk-context-vdb-query-inst")
    collection_name = f"sdk-context-col-{uuid4().hex[:8]}"

    try:
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
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-004",
            reason="VectorDB instance-scoped query fails after lifecycle create",
            statuses={403, 404, 500},
            detail_contains=("not found", "workroom_access_denied", "failed to query"),
        )
    finally:
        _safe_delete_vectordb(service, vectordb_id)


def test_context_vectordb_query_vectors_global(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    vectordb_id = _create_temp_vectordb(service, prefix="sdk-context-vdb-query-global")
    collection_name = f"sdk-context-col-{uuid4().hex[:8]}"

    try:
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
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-004",
            reason="VectorDB global query fails after lifecycle create",
            statuses={403, 404, 500},
            detail_contains=("not found", "workroom_access_denied", "failed to query"),
        )
    finally:
        _safe_delete_vectordb(service, vectordb_id)


def test_context_ontology_lifecycle_global(live_kamiwaza_client) -> None:
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


def test_context_ontology_add_knowledge(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    ontology_id = _create_temp_ontology(service, prefix="sdk-context-ont-knowledge")
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    try:
        knowledge = service.add_knowledge(
            ontology_id,
            group_id=group_id,
            messages=[{"content": "sdk test message", "role": "user"}],
        )
        assert knowledge["group_id"] == group_id
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-007",
            reason="Ontology add_knowledge is not functional end-to-end",
            statuses={404, 500},
            detail_contains=("not found", "failed to add knowledge"),
        )
    finally:
        _safe_delete_ontology(service, ontology_id)


def test_context_ontology_add_entity(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    ontology_id = _create_temp_ontology(service, prefix="sdk-context-ont-entity")
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
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-007",
            reason="Ontology add_entity is not functional end-to-end",
            statuses={404, 500},
            detail_contains=("not found", "failed to add entity"),
        )
    finally:
        _safe_delete_ontology(service, ontology_id)


def test_context_ontology_search_knowledge(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    ontology_id = _create_temp_ontology(service, prefix="sdk-context-ont-search")
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    try:
        search = service.search_knowledge(
            ontology_id,
            query="sdk query",
            group_ids=[group_id],
        )
        assert isinstance(search["facts"], list)
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-007",
            reason="Ontology search_knowledge is not functional end-to-end",
            statuses={404, 500},
            detail_contains=("not found", "failed to search knowledge"),
        )
    finally:
        _safe_delete_ontology(service, ontology_id)


def test_context_ontology_get_memory(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    ontology_id = _create_temp_ontology(service, prefix="sdk-context-ont-memory")
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    try:
        memory = service.get_memory(
            ontology_id,
            group_id=group_id,
            query="sdk memory",
        )
        assert isinstance(memory["facts"], list)
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-007",
            reason="Ontology get_memory is not functional end-to-end",
            statuses={404, 500},
            detail_contains=("not found", "failed to get memory"),
        )
    finally:
        _safe_delete_ontology(service, ontology_id)


def test_context_ontology_get_episodes(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    ontology_id = _create_temp_ontology(service, prefix="sdk-context-ont-episodes")
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    try:
        episodes = service.get_episodes(
            ontology_id,
            group_id=group_id,
            last_n=5,
        )
        assert isinstance(episodes["episodes"], list)
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-007",
            reason="Ontology get_episodes is not functional end-to-end",
            statuses={404, 500},
            detail_contains=("not found", "failed to get episodes"),
        )
    finally:
        _safe_delete_ontology(service, ontology_id)


def test_context_ontology_delete_group(live_kamiwaza_client) -> None:
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
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-007",
            reason="Ontology delete_group is not functional end-to-end",
            statuses={404, 500},
            detail_contains=("not found", "failed to delete group"),
        )
    finally:
        _safe_delete_ontology(service, ontology_id)


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
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D150-008",
            reason="Workroom follow-up access denied for context jobs",
            statuses={403},
            detail_contains=("workroom_access_denied",),
        )
    finally:
        try:
            service.cancel_pipeline_job(workroom_id=workroom_id, job_id=job_id)
        except APIError:
            pass


def test_context_workroom_collection_lifecycle(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID
    collection_name = f"sdk-collection-{uuid4().hex[:8]}"
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
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-005",
            reason="Workroom collection APIs fail in deployed context stack",
            statuses={500},
            detail_contains=("failed to list collections", "failed to create collection"),
        )
    finally:
        if created:
            _safe_delete_collection(
                service,
                workroom_id=workroom_id,
                collection_name=collection_name,
            )


def test_context_search_contract(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID

    try:
        search = service.search(workroom_id=workroom_id, query="hello context")
        assert isinstance(search.get("results"), list)
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-006",
            reason="Search endpoint fails under current context backend state",
            statuses={500},
            detail_contains=("search operation failed",),
        )


def test_context_retrieve_contract(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID

    try:
        retrieve = service.retrieve(workroom_id=workroom_id, query="hello context")
        assert isinstance(retrieve.get("sources"), list)
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-006",
            reason="Retrieve endpoint fails under current context backend state",
            statuses={500},
            detail_contains=("retrieval operation failed",),
        )


def test_context_agentic_search_contract(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID

    try:
        agentic = service.agentic_search(
            workroom_id=workroom_id,
            query="hello context",
        )
        assert isinstance(agentic.get("results"), list)
    except APIError as exc:
        _xfail_known_defect(
            exc,
            defect_id="D260-006",
            reason="Agentic search endpoint fails under current context backend state",
            statuses={500},
            detail_contains=("agentic search failed",),
        )
