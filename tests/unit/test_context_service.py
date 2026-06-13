from __future__ import annotations

import base64
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


def test_delete_pipeline_job_calls_expected_path(dummy_client):
    responses = {("delete", "/context/pipelines/job-1"): None}
    client = dummy_client(responses)
    service = ContextService(client)

    service.delete_pipeline_job(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        job_id="job-1",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("delete", "/context/pipelines/job-1")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_cancel_pipeline_job_posts_graceful_cancel(dummy_client):
    responses = {("post", "/context/pipelines/job-1/cancel"): {"id": "job-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.cancel_pipeline_job(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        job_id="job-1",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/pipelines/job-1/cancel")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_get_import_options_calls_expected_path(dummy_client):
    responses = {("get", "/context/pipelines/import-options"): {"providers": []}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.get_import_options(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/pipelines/import-options")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_get_import_options_omits_header_without_workroom(dummy_client):
    responses = {("get", "/context/pipelines/import-options"): {"providers": []}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.get_import_options()

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/pipelines/import-options")
    assert "X-Workroom-ID" not in kwargs["headers"]


def test_evaluate_import_options_posts_payload(dummy_client):
    responses = {("post", "/context/pipelines/import-options"): {"can_submit": True}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.evaluate_import_options(
        sources=[{"provider": "m365"}],
        config={"collection_name": "docs"},
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/pipelines/import-options")
    assert kwargs["json"] == {
        "sources": [{"provider": "m365"}],
        "config": {"collection_name": "docs"},
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_evaluate_import_options_omits_config_when_absent(dummy_client):
    responses = {("post", "/context/pipelines/import-options"): {"can_submit": False}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.evaluate_import_options(sources=[])

    method, path, kwargs = client.calls[0]
    assert kwargs["json"] == {"sources": []}


def test_create_source_import_job_posts_payload(dummy_client):
    responses = {("post", "/context/pipelines/imports"): {"id": "job-9"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.create_source_import_job(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        sources=[{"provider": "m365"}],
        config={"collection_name": "docs"},
        callback={"url": "https://cb.test"},
        idempotency_key="key-1",
        force=True,
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/pipelines/imports")
    assert kwargs["json"] == {
        "sources": [{"provider": "m365"}],
        "force": True,
        "config": {"collection_name": "docs"},
        "callback": {"url": "https://cb.test"},
        "idempotency_key": "key-1",
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_create_source_import_job_defaults_force_false(dummy_client):
    responses = {("post", "/context/pipelines/imports"): {"id": "job-9"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.create_source_import_job(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        sources=[{"provider": "m365"}],
    )

    method, path, kwargs = client.calls[0]
    assert kwargs["json"] == {"sources": [{"provider": "m365"}], "force": False}


def test_list_import_items_calls_expected_path(dummy_client):
    responses = {("get", "/context/pipelines/items"): {"items": [], "total_items": 0}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.list_import_items(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/pipelines/items")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_rerun_import_items_posts_payload(dummy_client):
    responses = {("post", "/context/pipelines/items/rerun"): {"id": "job-2"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.rerun_import_items(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        item_keys=["item-a", "item-b"],
        config={"collection_name": "docs"},
        idempotency_key="key-2",
        force=False,
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/pipelines/items/rerun")
    assert kwargs["json"] == {
        "item_keys": ["item-a", "item-b"],
        "force": False,
        "config": {"collection_name": "docs"},
        "idempotency_key": "key-2",
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_rerun_import_items_defaults_force_true(dummy_client):
    responses = {("post", "/context/pipelines/items/rerun"): {"id": "job-2"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.rerun_import_items(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        item_keys=["item-a"],
    )

    method, path, kwargs = client.calls[0]
    assert kwargs["json"] == {"item_keys": ["item-a"], "force": True}


def test_list_pipeline_job_items_calls_expected_path(dummy_client):
    responses = {
        ("get", "/context/pipelines/job-1/items"): {"job_id": "job-1", "items": []}
    }
    client = dummy_client(responses)
    service = ContextService(client)

    service.list_pipeline_job_items(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        job_id="job-1",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/pipelines/job-1/items")
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_retry_pipeline_job_posts_payload(dummy_client):
    responses = {("post", "/context/pipelines/job-1/retry"): {"id": "job-3"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.retry_pipeline_job(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        job_id="job-1",
        idempotency_key="key-3",
        force=True,
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/pipelines/job-1/retry")
    assert kwargs["json"] == {"idempotency_key": "key-3", "force": True}
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_retry_pipeline_job_omits_unset_fields(dummy_client):
    responses = {("post", "/context/pipelines/job-1/retry"): {"id": "job-3"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.retry_pipeline_job(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        job_id="job-1",
    )

    method, path, kwargs = client.calls[0]
    assert kwargs["json"] == {}


def test_rerun_pipeline_job_posts_payload(dummy_client):
    responses = {("post", "/context/pipelines/job-1/rerun"): {"id": "job-4"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.rerun_pipeline_job(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        job_id="job-1",
        callback={"url": "https://cb.test"},
        force=False,
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/pipelines/job-1/rerun")
    assert kwargs["json"] == {"callback": {"url": "https://cb.test"}, "force": False}
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


def test_agentic_search_targets_unified_endpoint_with_synthesis(dummy_client):
    responses = {("post", "/context/search/unified"): {"results": []}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.agentic_search(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        query="why is the sky blue",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/search/unified")
    assert kwargs["json"] == {
        "query": "why is the sky blue",
        "top_k": 10,
        "synthesize": True,
        "max_iterations": 1,
        "relevance_threshold": 0.7,
        "enable_graph_search": False,
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_agentic_search_includes_optional_graph_and_vector_fields(dummy_client):
    responses = {("post", "/context/search/unified"): {"results": []}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.agentic_search(
        workroom_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        query="summarize incidents",
        collection_name="docs",
        top_k=5,
        score_threshold=0.55,
        vectordb_id="vdb-1",
        max_iterations=3,
        relevance_threshold=0.8,
        enable_graph_search=True,
        ontology_id="ont-1",
        group_ids=["g1", "g2"],
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/search/unified")
    assert kwargs["json"] == {
        "query": "summarize incidents",
        "top_k": 5,
        "synthesize": True,
        "max_iterations": 3,
        "relevance_threshold": 0.8,
        "enable_graph_search": True,
        "collection_name": "docs",
        "score_threshold": 0.55,
        "vectordb_id": "vdb-1",
        "ontology_id": "ont-1",
        "group_ids": ["g1", "g2"],
    }
    assert kwargs["headers"]["X-Workroom-ID"] == "ffffffff-ffff-ffff-ffff-ffffffffffff"


# --- Raw-file object storage CRUD ---

WORKROOM = "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_store_raw_file_base64_encodes_bytes(dummy_client):
    responses = {("post", "/context/storage/raw"): {"id": "rf-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.store_raw_file(
        workroom_id=WORKROOM,
        filename="notes.txt",
        content=b"hello world",
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/storage/raw")
    assert kwargs["json"] == {
        "filename": "notes.txt",
        "content_base64": base64.b64encode(b"hello world").decode("ascii"),
    }
    assert kwargs["headers"]["X-Workroom-ID"] == WORKROOM


def test_store_raw_file_encodes_str_as_utf8(dummy_client):
    responses = {("post", "/context/storage/raw"): {"id": "rf-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.store_raw_file(
        workroom_id=WORKROOM,
        filename="snowman.txt",
        content="snowman ☃",
    )

    _, _, kwargs = client.calls[0]
    expected = base64.b64encode("snowman ☃".encode("utf-8")).decode("ascii")
    assert kwargs["json"]["content_base64"] == expected


def test_store_raw_file_includes_optional_fields(dummy_client):
    responses = {("post", "/context/storage/raw"): {"id": "rf-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.store_raw_file(
        workroom_id=WORKROOM,
        filename="report.md",
        content=b"# Title",
        content_type="text/markdown",
        source_urn="inline://report",
        source_kind="inline",
        source_ref={"origin": "editor"},
        metadata={"tag": "draft"},
    )

    _, _, kwargs = client.calls[0]
    body = kwargs["json"]
    assert body["content_type"] == "text/markdown"
    assert body["source_urn"] == "inline://report"
    assert body["source_kind"] == "inline"
    assert body["source_ref"] == {"origin": "editor"}
    assert body["metadata"] == {"tag": "draft"}


def test_store_raw_file_omits_unset_optional_fields(dummy_client):
    responses = {("post", "/context/storage/raw"): {"id": "rf-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.store_raw_file(
        workroom_id=WORKROOM,
        filename="notes.txt",
        content=b"x",
    )

    _, _, kwargs = client.calls[0]
    assert set(kwargs["json"]) == {"filename", "content_base64"}


def test_list_raw_files_default_params(dummy_client):
    responses = {("get", "/context/storage/raw"): {"items": [], "count": 0}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.list_raw_files(workroom_id=WORKROOM)

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/storage/raw")
    assert kwargs["params"] == {"limit": 50, "offset": 0}
    assert kwargs["headers"]["X-Workroom-ID"] == WORKROOM


def test_list_raw_files_applies_filters_and_markings(dummy_client):
    responses = {("get", "/context/storage/raw"): {"items": [], "count": 0}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.list_raw_files(
        workroom_id=WORKROOM,
        source_urn="inline://report",
        job_id="job-1",
        connector_id="conn-1",
        limit=10,
        offset=5,
        include_markings=True,
    )

    _, _, kwargs = client.calls[0]
    assert kwargs["params"] == {
        "limit": 10,
        "offset": 5,
        "source_urn": "inline://report",
        "job_id": "job-1",
        "connector_id": "conn-1",
        "include_markings": True,
    }


def test_get_raw_file_no_optional_params(dummy_client):
    responses = {("get", "/context/storage/raw/rf-1"): {"id": "rf-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.get_raw_file("rf-1", workroom_id=WORKROOM)

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/storage/raw/rf-1")
    assert kwargs["params"] is None
    assert kwargs["headers"]["X-Workroom-ID"] == WORKROOM


def test_get_raw_file_requests_presigned_url(dummy_client):
    responses = {("get", "/context/storage/raw/rf-1"): {"id": "rf-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.get_raw_file(
        "rf-1",
        workroom_id=WORKROOM,
        include_download_url=True,
        expires_seconds=120,
    )

    _, _, kwargs = client.calls[0]
    assert kwargs["params"] == {
        "include_download_url": True,
        "expires_seconds": 120,
    }


def test_update_raw_file_sends_content_without_if_match(dummy_client):
    responses = {("put", "/context/storage/raw/rf-1"): {"id": "rf-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.update_raw_file("rf-1", workroom_id=WORKROOM, content="new body")

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("put", "/context/storage/raw/rf-1")
    assert kwargs["json"] == {"content": "new body"}
    assert "If-Match" not in kwargs["headers"]
    assert kwargs["headers"]["X-Workroom-ID"] == WORKROOM


def test_update_raw_file_sets_if_match_header(dummy_client):
    responses = {("put", "/context/storage/raw/rf-1"): {"id": "rf-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.update_raw_file(
        "rf-1",
        workroom_id=WORKROOM,
        content="new body",
        if_match="2026-06-12T00:00:00+00:00",
    )

    _, _, kwargs = client.calls[0]
    assert kwargs["headers"]["If-Match"] == "2026-06-12T00:00:00+00:00"
    assert kwargs["headers"]["X-Workroom-ID"] == WORKROOM


# --- OmniParse instance lifecycle CRUD ---


def test_list_omniparses_sets_workroom_header(dummy_client):
    responses = {("get", "/context/omniparses"): []}
    client = dummy_client(responses)
    service = ContextService(client)

    service.list_omniparses(workroom_id=WORKROOM)

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/omniparses")
    assert kwargs["headers"]["X-Workroom-ID"] == WORKROOM


def test_get_omniparse_calls_expected_path(dummy_client):
    responses = {("get", "/context/omniparses/op-1"): {"id": "op-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.get_omniparse("op-1", workroom_id=WORKROOM)

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/context/omniparses/op-1")
    assert kwargs["headers"]["X-Workroom-ID"] == WORKROOM


def test_create_omniparse_builds_payload_with_defaults(dummy_client):
    responses = {("post", "/context/omniparses"): {"id": "op-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.create_omniparse(name="parser-a", workroom_id=WORKROOM)

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/context/omniparses")
    assert kwargs["json"] == {
        "name": "parser-a",
        "template_name": "tool-omniparse",
    }
    assert kwargs["headers"]["X-Workroom-ID"] == WORKROOM


def test_create_omniparse_includes_optional_fields(dummy_client):
    responses = {("post", "/context/omniparses"): {"id": "op-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.create_omniparse(
        name="parser-b",
        workroom_id=WORKROOM,
        template_name="tool-omniparse-gpu",
        config={"replicas": 2},
    )

    _, _, kwargs = client.calls[0]
    assert kwargs["json"] == {
        "name": "parser-b",
        "template_name": "tool-omniparse-gpu",
        "config": {"replicas": 2},
    }


def test_create_omniparse_omits_config_when_absent(dummy_client):
    responses = {("post", "/context/omniparses"): {"id": "op-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.create_omniparse(name="parser-c", workroom_id=WORKROOM)

    _, _, kwargs = client.calls[0]
    assert "config" not in kwargs["json"]


def test_update_omniparse_sends_config(dummy_client):
    responses = {("put", "/context/omniparses/op-1"): {"id": "op-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.update_omniparse(
        "op-1",
        workroom_id=WORKROOM,
        config={"timeout": 30},
    )

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("put", "/context/omniparses/op-1")
    assert kwargs["json"] == {"config": {"timeout": 30}}
    assert kwargs["headers"]["X-Workroom-ID"] == WORKROOM


def test_update_omniparse_sends_empty_body_when_config_absent(dummy_client):
    responses = {("put", "/context/omniparses/op-1"): {"id": "op-1"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.update_omniparse("op-1", workroom_id=WORKROOM)

    _, _, kwargs = client.calls[0]
    assert kwargs["json"] == {}


def test_delete_omniparse_calls_expected_path(dummy_client):
    responses = {("delete", "/context/omniparses/op-1"): {"message": "deleted"}}
    client = dummy_client(responses)
    service = ContextService(client)

    service.delete_omniparse("op-1", workroom_id=WORKROOM)

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("delete", "/context/omniparses/op-1")
    assert kwargs["headers"]["X-Workroom-ID"] == WORKROOM
