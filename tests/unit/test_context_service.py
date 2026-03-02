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


def test_list_vectordbs_sets_optional_workroom_header(dummy_client):
    client = dummy_client({("get", "/context/vectordbs"): []})
    service = ContextService(client)

    service.list_vectordbs(workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff")

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/vectordbs")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_get_vectordb_uses_workroom_query_and_header(dummy_client):
    client = dummy_client({("get", "/context/vectordbs/vdb-1"): {"id": "vdb-1"}})
    service = ContextService(client)

    service.get_vectordb(
        "vdb-1",
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/vectordbs/vdb-1")
    assert kwargs["params"] == {"workroom_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"}
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


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


def test_scale_vectordb_posts_replicas_and_workroom_query(dummy_client):
    responses = {("post", "/context/vectordbs/vdb-1/scale"): {"id": "vdb-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.scale_vectordb(
        "vdb-1",
        replicas=3,
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/vectordbs/vdb-1/scale")
    assert kwargs["json"] == {"replicas": 3}
    assert kwargs["params"] == {"workroom_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"}
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_delete_vectordb_uses_delete_with_optional_workroom_query(dummy_client):
    responses = {("delete", "/context/vectordbs/vdb-1"): {"message": "ok"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.delete_vectordb(
        "vdb-1",
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("delete", "/context/vectordbs/vdb-1")
    assert kwargs["params"] == {"workroom_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"}
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


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


def test_insert_vectors_payload_supports_optional_field_list(dummy_client):
    responses = {("post", "/context/vectordbs/vdb-1/insert"): {"inserted_count": 1}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.insert_vectors(
        "vdb-1",
        collection_name="docs",
        vectors=[[0.1, 0.2, 0.3]],
        metadata=[{"id": "1"}],
        field_list=[["id", "str"]],
        create_if_missing=False,
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/vectordbs/vdb-1/insert")
    assert kwargs["json"] == {
        "collection_name": "docs",
        "vectors": [[0.1, 0.2, 0.3]],
        "metadata": [{"id": "1"}],
        "field_list": [["id", "str"]],
        "create_if_missing": False,
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_query_vectors_payload_includes_optional_params(dummy_client):
    responses = {("post", "/context/vectordbs/vdb-1/query"): {"results": []}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.query_vectors(
        "vdb-1",
        collection_name="docs",
        vectors=[[0.9, 0.8, 0.7]],
        limit=5,
        params={"metric_type": "L2"},
        output_fields=["source"],
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/vectordbs/vdb-1/query")
    assert kwargs["json"] == {
        "collection_name": "docs",
        "vectors": [[0.9, 0.8, 0.7]],
        "limit": 5,
        "params": {"metric_type": "L2"},
        "output_fields": ["source"],
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_list_ontologies_sets_optional_workroom_header(dummy_client):
    responses = {("get", "/context/ontologies"): []}
    client = dummy_client(responses)
    service = ContextService(client)

    service.list_ontologies(workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff")

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/ontologies")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_get_ontology_uses_optional_workroom_query(dummy_client):
    responses = {("get", "/context/ontologies/o-1"): {"id": "o-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.get_ontology(
        "o-1",
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/ontologies/o-1")
    assert kwargs["params"] == {"workroom_id": "ffffffff-ffff-ffff-ffff-ffffffffffff"}
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_create_ontology_includes_optional_payload_fields(dummy_client):
    responses = {("post", "/context/ontologies"): {"id": "o-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.create_ontology(
        name="graph",
        backend="graphiti",
        config={"api_key": "abc"},
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/ontologies")
    assert kwargs["json"] == {
        "name": "graph",
        "backend": "graphiti",
        "config": {"api_key": "abc"},
        "workroom_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_add_knowledge_posts_expected_payload(dummy_client):
    responses = {
        ("post", "/context/ontologies/o-1/knowledge"): {"group_id": "g1"}
    }
    client = dummy_client(responses)
    service = ContextService(client)

    service.add_knowledge(
        "o-1",
        group_id="g1",
        messages=[{"role": "user", "content": "hello"}],
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/ontologies/o-1/knowledge")
    assert kwargs["json"] == {
        "group_id": "g1",
        "messages": [{"role": "user", "content": "hello"}],
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_add_entity_posts_expected_payload(dummy_client):
    responses = {("post", "/context/ontologies/o-1/entity"): {"success": True}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.add_entity(
        "o-1",
        group_id="g1",
        name="Entity One",
        entity_type="concept",
        summary="summary",
        properties={"priority": "high"},
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/ontologies/o-1/entity")
    assert kwargs["json"] == {
        "group_id": "g1",
        "name": "Entity One",
        "entity_type": "concept",
        "summary": "summary",
        "properties": {"priority": "high"},
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_search_knowledge_posts_expected_payload(dummy_client):
    responses = {("post", "/context/ontologies/o-1/search"): {"facts": []}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.search_knowledge(
        "o-1",
        query="where is file",
        group_ids=["g1", "g2"],
        max_results=7,
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/ontologies/o-1/search")
    assert kwargs["json"] == {
        "query": "where is file",
        "group_ids": ["g1", "g2"],
        "max_results": 7,
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_get_memory_posts_expected_payload(dummy_client):
    responses = {("post", "/context/ontologies/o-1/memory"): {"facts": []}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.get_memory(
        "o-1",
        group_id="g1",
        query="what happened",
        max_facts=4,
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/ontologies/o-1/memory")
    assert kwargs["json"] == {"group_id": "g1", "query": "what happened", "max_facts": 4}
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_get_episodes_uses_last_n_query_param(dummy_client):
    responses = {
        ("get", "/context/ontologies/o-1/episodes/g1"): {"episodes": []}
    }
    client = dummy_client(responses)
    service = ContextService(client)

    service.get_episodes(
        "o-1",
        group_id="g1",
        last_n=12,
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/ontologies/o-1/episodes/g1")
    assert kwargs["params"] == {"last_n": 12}
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_delete_group_calls_expected_path(dummy_client):
    responses = {
        ("delete", "/context/ontologies/o-1/groups/g1"): {"deleted": True}
    }
    client = dummy_client(responses)
    service = ContextService(client)

    service.delete_group(
        "o-1",
        group_id="g1",
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("delete", "/context/ontologies/o-1/groups/g1")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_ontology_health_calls_expected_path(dummy_client):
    responses = {("get", "/context/ontologies/o-1/health"): {"healthy": True}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.ontology_health(
        "o-1",
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/ontologies/o-1/health")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_create_collection_posts_expected_payload(dummy_client):
    responses = {("post", "/context/collections/"): {"display_name": "docs"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.create_collection(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        name="docs",
        dimension=768,
        description="documents",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/collections/")
    assert kwargs["json"] == {
        "name": "docs",
        "dimension": 768,
        "description": "documents",
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_get_collection_calls_expected_path(dummy_client):
    responses = {("get", "/context/collections/docs"): {"display_name": "docs"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.get_collection(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        collection_name="docs",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/collections/docs")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_delete_collection_calls_expected_path(dummy_client):
    responses = {("delete", "/context/collections/docs"): None}
    client = dummy_client(responses)
    service = ContextService(client)

    service.delete_collection(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        collection_name="docs",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("delete", "/context/collections/docs")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_create_pipeline_job_posts_expected_payload(dummy_client):
    responses = {("post", "/context/pipelines/"): {"id": "job-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.create_pipeline_job(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        files=[{"filename": "a.txt", "content_base64": "aGVsbG8="}],
        config={"collection_name": "docs"},
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/pipelines/")
    assert kwargs["json"] == {
        "files": [{"filename": "a.txt", "content_base64": "aGVsbG8="}],
        "config": {"collection_name": "docs"},
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_get_supported_file_types_calls_expected_path(dummy_client):
    responses = {("get", "/context/pipelines/supported-types"): [".txt"]}
    client = dummy_client(responses)
    service = ContextService(client)

    result = service.get_supported_file_types()

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/pipelines/supported-types")
    assert kwargs == {}
    assert ".txt" in result


def test_get_pipeline_job_calls_expected_path(dummy_client):
    responses = {("get", "/context/pipelines/job-1"): {"id": "job-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.get_pipeline_job(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        job_id="job-1",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/pipelines/job-1")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_cancel_pipeline_job_calls_expected_path(dummy_client):
    responses = {("delete", "/context/pipelines/job-1"): None}
    client = dummy_client(responses)
    service = ContextService(client)

    service.cancel_pipeline_job(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        job_id="job-1",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("delete", "/context/pipelines/job-1")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_search_builds_payload_with_optional_fields(dummy_client):
    responses = {("post", "/context/search"): {"results": []}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.search(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        query="find docs",
        collection_name="docs",
        top_k=9,
        score_threshold=0.65,
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/search")
    assert kwargs["json"] == {
        "query": "find docs",
        "top_k": 9,
        "collection_name": "docs",
        "score_threshold": 0.65,
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


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
