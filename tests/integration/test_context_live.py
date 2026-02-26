"""Live integration tests for Context Service endpoints."""

from __future__ import annotations

import base64
import os
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


def _assert_context_error(
    exc: APIError,
    *,
    statuses: set[int],
    detail_contains: tuple[str, ...] = (),
) -> None:
    assert exc.status_code in statuses
    detail = _error_detail(exc).lower()
    if detail_contains:
        assert any(token.lower() in detail for token in detail_contains), detail


def _context_service(live_kamiwaza_client) -> ContextService:
    service = live_kamiwaza_client.context
    assert isinstance(service, ContextService)
    return service


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

        try:
            service.insert_vectors(
                vectordb_id,
                collection_name="sdk_context_collection",
                vectors=[[0.1, 0.2, 0.3]],
                metadata=[],
            )
            pytest.fail("Expected insert_vectors validation failure")
        except APIError as exc:
            _assert_context_error(
                exc,
                statuses={400, 404},
                detail_contains=("mismatch", "not found"),
            )

        try:
            service.insert_vectors_global(
                vectordb_id=vectordb_id,
                collection_name="sdk_context_collection",
                vectors=[[0.1, 0.2, 0.3]],
                metadata=[],
            )
            pytest.fail("Expected insert_vectors_global validation failure")
        except APIError as exc:
            _assert_context_error(
                exc,
                statuses={400, 404},
                detail_contains=("mismatch", "not found"),
            )

        try:
            service.query_vectors(
                vectordb_id,
                collection_name="sdk_context_collection",
                vectors=[],
            )
            pytest.fail("Expected query_vectors validation failure")
        except APIError as exc:
            _assert_context_error(
                exc,
                statuses={400, 404},
                detail_contains=("required", "not found"),
            )

        try:
            service.query_vectors_global(
                vectordb_id=vectordb_id,
                collection_name="sdk_context_collection",
                vectors=[],
            )
            pytest.fail("Expected query_vectors_global validation failure")
        except APIError as exc:
            _assert_context_error(
                exc,
                statuses={400, 404},
                detail_contains=("required", "not found"),
            )
    finally:
        service.delete_vectordb(vectordb_id)


def test_context_ontology_lifecycle_global(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)

    name = f"sdk-context-ontology-{uuid4().hex[:8]}"
    created = service.create_ontology(name=name, backend="graphiti")
    ontology_id = created["id"]
    group_id = f"sdk-group-{uuid4().hex[:8]}"

    try:
        fetched = service.get_ontology(ontology_id)
        assert fetched["id"] == ontology_id
        assert fetched["name"] == name

        health = service.ontology_health(ontology_id)
        assert health["ontology_id"] == ontology_id
        assert "healthy" in health

        try:
            knowledge = service.add_knowledge(
                ontology_id,
                group_id=group_id,
                messages=[{"content": "sdk test message", "role": "user"}],
            )
            assert knowledge["group_id"] == group_id
        except APIError as exc:
            _assert_context_error(exc, statuses={404, 500})

        try:
            entity = service.add_entity(
                ontology_id,
                group_id=group_id,
                name="SDK Entity",
                entity_type="concept",
            )
            assert "success" in entity
        except APIError as exc:
            _assert_context_error(exc, statuses={404, 500})

        for op in (
            lambda: service.search_knowledge(
                ontology_id,
                query="sdk query",
                group_ids=[group_id],
            ),
            lambda: service.get_memory(
                ontology_id,
                group_id=group_id,
                query="sdk memory",
            ),
            lambda: service.get_episodes(
                ontology_id,
                group_id=group_id,
                last_n=5,
            ),
            lambda: service.delete_group(
                ontology_id,
                group_id=group_id,
            ),
        ):
            try:
                response = op()
                assert isinstance(response, dict)
            except APIError as exc:
                _assert_context_error(exc, statuses={404, 500})
    finally:
        service.delete_ontology(ontology_id)


def test_context_workroom_endpoints_contract(live_kamiwaza_client) -> None:
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

    try:
        collections = service.list_collections(workroom_id=workroom_id)
        assert isinstance(collections, list)
    except APIError as exc:
        _assert_context_error(
            exc,
            statuses={500},
            detail_contains=("failed to list collections",),
        )

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
        job_id = job["id"]
        created_job_ids.append(job_id)

        try:
            fetched_job = service.get_pipeline_job(workroom_id=workroom_id, job_id=job_id)
            assert fetched_job["id"] == job_id
            # cancel may be a no-op depending on background processing state
            service.cancel_pipeline_job(workroom_id=workroom_id, job_id=job_id)
        except APIError as exc:
            _assert_context_error(exc, statuses={403}, detail_contains=("workroom_access_denied",))

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

        collection_name = f"sdk-collection-{uuid4().hex[:8]}"
        try:
            created = service.create_collection(
                workroom_id=workroom_id,
                name=collection_name,
                dimension=384,
            )
            assert created["display_name"] == collection_name
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
            _assert_context_error(
                exc,
                statuses={500},
                detail_contains=("failed to create collection", "failed to list collections"),
            )
    finally:
        for created_job_id in created_job_ids:
            try:
                service.cancel_pipeline_job(
                    workroom_id=workroom_id,
                    job_id=created_job_id,
                )
            except APIError:
                # Some workroom-scoped deployments currently reject follow-up access.
                pass


def test_context_search_retrieve_agentic_contract(live_kamiwaza_client) -> None:
    service = _context_service(live_kamiwaza_client)
    workroom_id = DEFAULT_WORKROOM_ID

    for op in (
        lambda: service.search(workroom_id=workroom_id, query="hello context"),
        lambda: service.retrieve(workroom_id=workroom_id, query="hello context"),
        lambda: service.agentic_search(workroom_id=workroom_id, query="hello context"),
    ):
        try:
            response = op()
            assert isinstance(response, dict)
        except APIError as exc:
            _assert_context_error(
                exc,
                statuses={400, 500},
                detail_contains=("failed", "search", "retrieval"),
            )
