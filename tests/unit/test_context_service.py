from __future__ import annotations

import io

import pytest

from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.services.context import ContextService

pytestmark = pytest.mark.unit


def test_client_exposes_context_service(monkeypatch):
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://example.test/api")
    client = KamiwazaClient()

    assert isinstance(client.context, ContextService)
    assert client.context is client.context


def test_health_calls_context_health(dummy_client):
    client = dummy_client({("get", "/context/health"): {"status": "healthy"}})
    service = ContextService(client)

    result = service.health()

    assert result["status"] == "healthy"
    assert client.calls[0] == ("get", "/context/health", {})


def test_create_vectordb_builds_payload(dummy_client):
    client = dummy_client({("post", "/context/vectordbs"): {"id": "abc"}})
    service = ContextService(client)

    service.create_vectordb(name="vdb", engine="milvus")

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/vectordbs")
    assert kwargs["json"] == {"name": "vdb", "engine": "milvus", "replicas": 1}


def test_query_vectors_global_uses_body_vectordb_id(dummy_client):
    responses = {("post", "/context/vectordbs/query"): {"results": []}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.query_vectors_global(
        vectordb_id="vdb-1",
        collection_name="docs",
        vectors=[[0.1, 0.2, 0.3]],
        limit=3,
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/vectordbs/query")
    assert kwargs["json"] == {
        "vectordb_id": "vdb-1",
        "collection_name": "docs",
        "vectors": [[0.1, 0.2, 0.3]],
        "limit": 3,
    }


def test_insert_vectors_global_uses_body_vectordb_id(dummy_client):
    responses = {("post", "/context/vectordbs/insert"): {"inserted_count": 1}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.insert_vectors_global(
        vectordb_id="vdb-1",
        collection_name="docs",
        vectors=[[0.1, 0.2, 0.3]],
        metadata=[{"id": "row-1"}],
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/vectordbs/insert")
    assert kwargs["json"] == {
        "vectordb_id": "vdb-1",
        "collection_name": "docs",
        "vectors": [[0.1, 0.2, 0.3]],
        "metadata": [{"id": "row-1"}],
        "create_if_missing": True,
    }


def test_list_collections_sets_workroom_header(dummy_client):
    responses = {("get", "/context/collections/"): []}
    client = dummy_client(responses)
    service = ContextService(client)

    service.list_collections(workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff")

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/collections/")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_update_vectordb_uses_put_with_workroom_query(dummy_client):
    responses = {("put", "/context/vectordbs/vdb-1"): {"id": "vdb-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.update_vectordb(
        "vdb-1",
        config={"x": "1"},
        replicas=2,
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("put", "/context/vectordbs/vdb-1")
    assert kwargs["params"] == {"workroom_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"}
    assert kwargs["json"] == {"config": {"x": "1"}, "replicas": 2}


def test_list_pipeline_jobs_applies_filters(dummy_client):
    responses = {("get", "/context/pipelines/"): []}
    client = dummy_client(responses)
    service = ContextService(client)

    service.list_pipeline_jobs(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        status="running",
        limit=10,
        offset=5,
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/pipelines/")
    assert kwargs["params"] == {"status": "running", "limit": 10, "offset": 5}


def test_delete_ontology_calls_expected_path(dummy_client):
    responses = {("delete", "/context/ontologies/o-1"): {"message": "ok"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.delete_ontology("o-1")

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("delete", "/context/ontologies/o-1")
    assert kwargs["params"] is None


def test_upload_file_sends_files_and_optional_params(dummy_client):
    responses = {("post", "/context/upload/"): {"id": "job-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    payload = io.BytesIO(b"hello")
    service.upload_file(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        filename="sample.txt",
        file_content=payload,
        content_type="text/plain",
        collection_name="docs",
        source_urn="urn:test:sample",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/upload/")
    assert kwargs["params"] == {"collection_name": "docs", "source_urn": "urn:test:sample"}
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"
    file_info = kwargs["files"]["file"]
    assert file_info[0] == "sample.txt"
    assert file_info[2] == "text/plain"


def test_agentic_search_includes_optional_fields(dummy_client):
    responses = {("post", "/context/agentic/search"): {"results": []}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.agentic_search(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        query="hello",
        vectordb_ids=["v1"],
        collection="default",
        ontology_id="o1",
        group_ids=["g1"],
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/agentic/search")
    payload = kwargs["json"]
    assert payload["query"] == "hello"
    assert payload["vectordb_ids"] == ["v1"]
    assert payload["ontology_id"] == "o1"
    assert payload["group_ids"] == ["g1"]


def test_retrieve_builds_payload(dummy_client):
    responses = {("post", "/context/retrieve"): {"query": "q"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.retrieve(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        query="q",
        collection_names=["c1", "c2"],
        top_k=3,
        score_threshold=0.4,
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/retrieve")
    assert kwargs["json"]["query"] == "q"
    assert kwargs["json"]["collection_names"] == ["c1", "c2"]
    assert kwargs["json"]["top_k"] == 3
    assert kwargs["json"]["score_threshold"] == 0.4
