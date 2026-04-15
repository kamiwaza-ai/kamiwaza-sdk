from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.enclaves import ConnectorCreate, ConnectorUpdate, IndexDocumentRequest
from kamiwaza_sdk.services.enclaves import EnclavesService

pytestmark = pytest.mark.unit


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connector_response(connector_id: UUID | str | None = None) -> dict:
    cid = str(connector_id or uuid4())
    return {
        "id": cid,
        "name": "demo-connector",
        "source_type": "s3",
        "connector_type": "s3",
        "description": "Demo connector",
        "tags": ["demo"],
        "allowed_roles": ["admin"],
        "require_encryption": True,
        "enabled": True,
        "system_high": "U",
        "default_security_marking": None,
        "last_ingestion_at": None,
        "last_success_at": None,
        "error_count": 0,
        "created_at": _now_iso(),
        "created_by": "urn:li:corpuser:admin",
        "updated_at": None,
        "updated_by": None,
    }


def _document_record(document_id: UUID | str | None = None, *, source_id: UUID | str | None = None) -> dict:
    did = str(document_id or uuid4())
    sid = str(source_id or uuid4())
    return {
        "id": did,
        "source_id": sid,
        "job_id": str(uuid4()),
        "source_ref": "s3://bucket/key.txt",
        "item_type": "document",
        "title": "Demo doc",
        "description": None,
        "content_type": "text/plain",
        "size_bytes": 123,
        "tags": [],
        "categories": [],
        "language": "en",
        "classification": "U",
        "security_marking": None,
        "handling_caveats": [],
        "control_markings": [],
        "sci_controls": [],
        "dissemination_controls": [],
        "releasable_to": [],
        "entities": None,
        "indexed_at": _now_iso(),
        "content_date": None,
        "confidence_score": None,
        "completeness_score": None,
        "access_count": 0,
    }


def test_list_connectors_passes_query(dummy_client):
    connector = _connector_response()
    responses = {
        ("get", "/enclaves/connectors/"): {
            "items": [connector],
            "total": 1,
            "limit": 10,
            "offset": 5,
        }
    }
    client = dummy_client(responses)
    service = EnclavesService(client)

    result = service.connectors.list(
        limit=10,
        offset=5,
        source_type="s3",
        enabled=True,
        tag="demo",
    )

    assert result.total == 1
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/enclaves/connectors/")
    assert kwargs["params"] == {
        "limit": 10,
        "offset": 5,
        "source_type": "s3",
        "enabled": True,
        "tag": "demo",
    }


def test_create_connector_posts_payload(dummy_client):
    connector = _connector_response()
    responses = {("post", "/enclaves/connectors/"): connector}
    client = dummy_client(responses)
    service = EnclavesService(client)

    payload = ConnectorCreate(
        name="demo-connector",
        source_type="s3",
        connector_type="s3",
        connection_config={"bucket": "demo"},
    )

    created = service.connectors.create(payload)

    assert created.name == "demo-connector"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/enclaves/connectors/")
    assert kwargs["json"]["connection_config"] == {"bucket": "demo"}


def test_get_update_delete_connector_round_trip(dummy_client):
    connector_id = uuid4()
    connector = _connector_response(connector_id)
    responses = {
        ("get", f"/enclaves/connectors/{connector_id}"): connector,
        ("put", f"/enclaves/connectors/{connector_id}"): connector,
    }
    client = dummy_client(responses)
    service = EnclavesService(client)
    client.delete = lambda path, **kwargs: client.calls.append(("delete", path, kwargs)) or None  # noqa: E731

    fetched = service.connectors.get(connector_id)
    updated = service.connectors.update(connector_id, ConnectorUpdate(name="new-name"))
    deleted = service.connectors.delete(connector_id)

    assert fetched.id == connector_id
    assert updated.id == connector_id
    assert deleted is None
    assert client.calls[1][2]["json"]["name"] == "new-name"
    assert client.calls[2][2]["expect_json"] is False


def test_trigger_ingest_posts_request(dummy_client):
    connector_id = uuid4()
    responses = {
        ("post", f"/enclaves/connectors/{connector_id}/trigger_ingest"): {"status": "queued"}
    }
    client = dummy_client(responses)
    service = EnclavesService(client)

    response = service.connectors.trigger_ingest(connector_id)

    assert response.status == "queued"
    assert client.calls[0][:2] == (
        "post",
        f"/enclaves/connectors/{connector_id}/trigger_ingest",
    )


def test_connector_operations_validate_uuid_inputs(dummy_client):
    client = dummy_client({})
    service = EnclavesService(client)

    with pytest.raises(APIError, match="Invalid connector_id"):
        service.connectors.get("not-a-uuid")

    with pytest.raises(APIError, match="Invalid connector_id"):
        service.connectors.trigger_ingest("still-not-a-uuid")


def test_create_document_posts_payload(dummy_client):
    document = _document_record()
    responses = {("post", "/enclaves/documents/"): document}
    client = dummy_client(responses)
    service = EnclavesService(client)

    request = IndexDocumentRequest(
        source_id=uuid4(),
        source_ref="s3://bucket/key.txt",
        item_type="document",
        metadata={"foo": "bar"},
    )

    created = service.documents.create(request)

    assert created.source_ref == "s3://bucket/key.txt"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/enclaves/documents/")
    assert kwargs["json"]["metadata"] == {"foo": "bar"}


def test_list_documents_sets_system_high_header(dummy_client):
    source_id = uuid4()
    document = _document_record(source_id=source_id)
    responses = {
        ("get", "/enclaves/documents/"): {
            "items": [document],
            "total": 1,
            "limit": 5,
            "offset": 0,
            "rejections": [],
        }
    }
    client = dummy_client(responses)
    service = EnclavesService(client)

    result = service.documents.list(
        source_id,
        limit=5,
        offset=0,
        item_type="document",
        tag="demo",
        system_high="U",
    )

    assert result.total == 1
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/enclaves/documents/")
    assert kwargs["params"]["source_id"] == str(source_id)
    assert kwargs["headers"]["X-User-System-High"] == "U"


def test_list_documents_preserves_caller_supplied_system_high_header(dummy_client):
    source_id = uuid4()
    document = _document_record(source_id=source_id)
    responses = {
        ("get", "/enclaves/documents/"): {
            "items": [document],
            "total": 1,
            "limit": 5,
            "offset": 0,
            "rejections": [],
        }
    }
    client = dummy_client(responses)
    service = EnclavesService(client)

    service.documents.list(
        source_id,
        headers={"X-User-System-High": "TS"},
        system_high="U",
    )

    assert client.calls[0][2]["headers"]["X-User-System-High"] == "TS"


def test_get_document_passes_source_id_and_header(dummy_client):
    source_id = uuid4()
    document_id = uuid4()
    document = _document_record(document_id, source_id=source_id)
    responses = {("get", f"/enclaves/documents/{document_id}"): document}
    client = dummy_client(responses)
    service = EnclavesService(client)

    result = service.documents.get(document_id, source_id=source_id, system_high="U")

    assert result.id == document_id
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", f"/enclaves/documents/{document_id}")
    assert kwargs["params"] == {"source_id": str(source_id)}
    assert kwargs["headers"]["X-User-System-High"] == "U"


def test_get_document_validates_ids(dummy_client):
    service = EnclavesService(dummy_client({}))

    with pytest.raises(APIError, match="Invalid source_id"):
        service.documents.list("not-a-uuid")

    with pytest.raises(APIError, match="Invalid document_id"):
        service.documents.get("not-a-uuid", source_id=uuid4())


def test_trigger_response_requires_status(dummy_client):
    connector_id = uuid4()
    responses = {("post", f"/enclaves/connectors/{connector_id}/trigger_ingest"): {}}
    client = dummy_client(responses)
    service = EnclavesService(client)

    with pytest.raises(ValidationError):
        service.connectors.trigger_ingest(connector_id)


def test_connector_api_errors_propagate(dummy_client):
    client = dummy_client({})
    client.get = lambda path, **kwargs: (_ for _ in ()).throw(APIError("boom", status_code=404, response_text=""))  # noqa: E731
    service = EnclavesService(client)

    with pytest.raises(APIError) as exc_info:
        service.connectors.get(uuid4())

    assert exc_info.value.status_code == 404


def test_client_retains_existing_service_properties(client_factory):
    from kamiwaza_sdk.services.context import ContextService
    from kamiwaza_sdk.services.extensions import ExtensionService
    from kamiwaza_sdk.services.skills import SkillsService

    client = client_factory(api_key="test-token")

    assert isinstance(client.context, ContextService)
    assert isinstance(client.skills, SkillsService)
    assert isinstance(client.extensions, ExtensionService)
    assert isinstance(client.enclaves, EnclavesService)
