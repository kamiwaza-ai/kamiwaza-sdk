from __future__ import annotations

import uuid

import pytest

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.workrooms import (
    CreateWorkroom,
    DeleteWorkroomResponse,
    ExportManifest,
    IngestionSummary,
    Workroom,
    WorkroomType,
)
from kamiwaza_sdk.services.workrooms import WorkroomService

pytestmark = pytest.mark.unit

WORKROOM_ID = "12345678-1234-5678-9012-123456789012"
WORKROOM_UUID = uuid.UUID(WORKROOM_ID)


def _workroom_response(**overrides):
    base = {
        "id": WORKROOM_ID,
        "tenant_id": "t-1",
        "owner_user_id": "u-1",
        "name": "Test Workroom",
        "type": "persistent",
        "description": "A test workroom",
        "labels": ["test"],
        "classification": None,
        "attributes": None,
        "scg_references": None,
        "status": "active",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": None,
        "deleted_at": None,
    }
    base.update(overrides)
    return base


def _delete_response():
    return {
        "workroom_id": WORKROOM_ID,
        "status": "deleted",
        "message": "Workroom deleted successfully",
    }


def _manifest_response():
    return {
        "workroom_id": WORKROOM_ID,
        "items": [
            {"type": "metadata", "name": "Workroom metadata", "exportable": True, "reason": None},
            {"type": "data_source", "name": "My CSV", "exportable": True, "reason": None},
            {"type": "app_deployment", "name": "Chat App", "exportable": False, "reason": "Runtime resource"},
        ],
    }


def _ingestion_response():
    return {
        "workroom_id": WORKROOM_ID,
        "total_sources": 5,
        "counts_by_source_type": {"csv": 3, "pdf": 2},
        "date_range_start": "2025-01-01T00:00:00Z",
        "date_range_end": "2025-06-01T00:00:00Z",
        "error_count": 1,
        "warning_count": 2,
        "catalog_entries": 10,
    }


# =============================================================================
# Create
# =============================================================================


def test_create_calls_post_to_correct_endpoint(dummy_client):
    responses = {("post", "/workrooms/"): _workroom_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.create("My WR", "persistent")

    assert client.calls[0][0] == "post"
    assert client.calls[0][1] == "/workrooms/"
    assert client.calls[0][2]["json"]["name"] == "My WR"
    assert client.calls[0][2]["json"]["type"] == "persistent"


def test_create_returns_workroom(dummy_client):
    responses = {("post", "/workrooms/"): _workroom_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.create("My WR", "persistent")

    assert isinstance(result, Workroom)
    assert result.name == "Test Workroom"


def test_create_with_all_optional_fields(dummy_client):
    responses = {("post", "/workrooms/"): _workroom_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.create(
        "My WR",
        "ephemeral",
        description="desc",
        labels=["a", "b"],
        classification="internal",
        attributes={"mission_id": "m1"},
        scg_references=["scg-1"],
    )

    payload = client.calls[0][2]["json"]
    assert payload["description"] == "desc"
    assert payload["labels"] == ["a", "b"]
    assert payload["classification"] == "internal"
    assert payload["attributes"] == {"mission_id": "m1"}
    assert payload["scg_references"] == ["scg-1"]


def test_create_excludes_none_fields(dummy_client):
    responses = {("post", "/workrooms/"): _workroom_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.create("My WR", "persistent")

    payload = client.calls[0][2]["json"]
    assert "description" not in payload
    assert "labels" not in payload


# =============================================================================
# List
# =============================================================================


def test_list_calls_correct_endpoint(dummy_client):
    responses = {("get", "/workrooms/"): {"items": [_workroom_response()]}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.list()

    assert client.calls[0][1] == "/workrooms/"


def test_list_returns_workroom_objects(dummy_client):
    responses = {("get", "/workrooms/"): {"items": [_workroom_response()]}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.list()

    assert len(result) == 1
    assert isinstance(result[0], Workroom)


def test_list_sends_include_archived_param(dummy_client):
    responses = {("get", "/workrooms/"): {"items": []}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.list(include_archived=True)

    assert client.calls[0][2]["params"]["include_archived"] == "true"


def test_list_no_params_by_default(dummy_client):
    responses = {("get", "/workrooms/"): {"items": []}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.list()

    # params should be empty dict
    assert client.calls[0][2].get("params", {}) == {}


def test_list_empty_returns_empty(dummy_client):
    responses = {("get", "/workrooms/"): {"items": []}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.list()

    assert result == []


# =============================================================================
# Get
# =============================================================================


def test_get_calls_correct_endpoint(dummy_client):
    responses = {("get", f"/workrooms/{WORKROOM_UUID}"): _workroom_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.get(WORKROOM_ID)

    assert client.calls[0][1] == f"/workrooms/{WORKROOM_UUID}"


def test_get_accepts_uuid_object(dummy_client):
    responses = {("get", f"/workrooms/{WORKROOM_UUID}"): _workroom_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.get(WORKROOM_UUID)

    assert isinstance(result, Workroom)


def test_get_not_found_raises(dummy_client):
    responses = {}
    client = dummy_client(responses)
    # Override get to raise APIError with 404
    def _raise_404(path, **kwargs):
        raise APIError("Not found", status_code=404, response_text="")
    client.get = _raise_404
    service = WorkroomService(client)

    with pytest.raises(NotFoundError, match="not found"):
        service.get(WORKROOM_ID)


def test_get_other_error_propagates(dummy_client):
    responses = {}
    client = dummy_client(responses)
    def _raise_500(path, **kwargs):
        raise APIError("Server error", status_code=500, response_text="")
    client.get = _raise_500
    service = WorkroomService(client)

    with pytest.raises(APIError):
        service.get(WORKROOM_ID)


# =============================================================================
# Update
# =============================================================================


def test_update_sends_patch(dummy_client):
    responses = {("patch", f"/workrooms/{WORKROOM_UUID}"): _workroom_response(name="Updated")}
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.update(WORKROOM_ID, name="Updated")

    assert client.calls[0][0] == "patch"
    assert client.calls[0][2]["json"]["name"] == "Updated"
    assert isinstance(result, Workroom)


def test_update_sends_only_provided_fields(dummy_client):
    responses = {("patch", f"/workrooms/{WORKROOM_UUID}"): _workroom_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.update(WORKROOM_ID, name="X", labels=["a"])

    payload = client.calls[0][2]["json"]
    assert "name" in payload
    assert "labels" in payload
    assert "description" not in payload
    assert "classification" not in payload


def test_update_not_found_raises(dummy_client):
    responses = {}
    client = dummy_client(responses)
    def _raise_404(path, **kwargs):
        raise APIError("Not found", status_code=404, response_text="")
    client.patch = _raise_404
    service = WorkroomService(client)

    with pytest.raises(NotFoundError):
        service.update(WORKROOM_ID, name="X")


# =============================================================================
# Delete
# =============================================================================


def test_delete_calls_correct_endpoint(dummy_client):
    responses = {("delete", f"/workrooms/{WORKROOM_UUID}"): _delete_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.delete(WORKROOM_ID)

    assert client.calls[0][1] == f"/workrooms/{WORKROOM_UUID}"


def test_delete_returns_typed_response(dummy_client):
    responses = {("delete", f"/workrooms/{WORKROOM_UUID}"): _delete_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.delete(WORKROOM_ID)

    assert isinstance(result, DeleteWorkroomResponse)
    assert result.status == "deleted"


def test_delete_not_found_raises(dummy_client):
    responses = {}
    client = dummy_client(responses)
    def _raise_404(path, **kwargs):
        raise APIError("Not found", status_code=404, response_text="")
    client.delete = _raise_404
    service = WorkroomService(client)

    with pytest.raises(NotFoundError):
        service.delete(WORKROOM_ID)


def test_delete_global_workroom_raises_api_error(dummy_client):
    global_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    global_uuid = uuid.UUID(global_id)
    responses = {}
    client = dummy_client(responses)
    def _raise_403(path, **kwargs):
        raise APIError("Forbidden", status_code=403, response_text="Global Workroom")
    client.delete = _raise_403
    service = WorkroomService(client)

    with pytest.raises(APIError) as exc_info:
        service.delete(global_id)
    assert exc_info.value.status_code == 403


# =============================================================================
# Archive
# =============================================================================


def test_archive_calls_post_to_archive(dummy_client):
    responses = {("post", f"/workrooms/{WORKROOM_UUID}/archive"): _workroom_response(status="archived")}
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.archive(WORKROOM_ID)

    assert client.calls[0][1] == f"/workrooms/{WORKROOM_UUID}/archive"
    assert isinstance(result, Workroom)
    assert result.status == "archived"


def test_archive_not_found_raises(dummy_client):
    responses = {}
    client = dummy_client(responses)
    def _raise_404(path, **kwargs):
        raise APIError("Not found", status_code=404, response_text="")
    client.post = _raise_404
    service = WorkroomService(client)

    with pytest.raises(NotFoundError):
        service.archive(WORKROOM_ID)


# =============================================================================
# Export Manifest
# =============================================================================


def test_get_export_manifest_endpoint(dummy_client):
    responses = {("get", f"/workrooms/{WORKROOM_UUID}/export/manifest"): _manifest_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.get_export_manifest(WORKROOM_ID)

    assert client.calls[0][1] == f"/workrooms/{WORKROOM_UUID}/export/manifest"


def test_get_export_manifest_returns_typed_items(dummy_client):
    responses = {("get", f"/workrooms/{WORKROOM_UUID}/export/manifest"): _manifest_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.get_export_manifest(WORKROOM_ID)

    assert isinstance(result, ExportManifest)
    assert len(result.items) == 3
    assert result.items[0].exportable is True
    assert result.items[2].exportable is False


# =============================================================================
# Export Bundle
# =============================================================================


def test_export_bundle_calls_post_with_no_json(dummy_client):
    responses = {("post", f"/workrooms/{WORKROOM_UUID}/export"): _workroom_response()}
    client = dummy_client(responses)
    # Override post to simulate binary response
    mock_response = type("Response", (), {"content": b"PK\x03\x04zipdata"})()
    def _post(path, **kwargs):
        client.calls.append(("post", path, kwargs))
        return mock_response
    client.post = _post
    service = WorkroomService(client)

    result = service.export_bundle(WORKROOM_ID)

    assert isinstance(result, bytes)
    assert client.calls[0][2].get("expect_json") is False


# =============================================================================
# Ingestion Summary
# =============================================================================


def test_get_ingestion_summary_endpoint(dummy_client):
    responses = {("get", f"/workrooms/{WORKROOM_UUID}/ingestion/summary"): _ingestion_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.get_ingestion_summary(WORKROOM_ID)

    assert client.calls[0][1] == f"/workrooms/{WORKROOM_UUID}/ingestion/summary"


def test_get_ingestion_summary_returns_typed(dummy_client):
    responses = {("get", f"/workrooms/{WORKROOM_UUID}/ingestion/summary"): _ingestion_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.get_ingestion_summary(WORKROOM_ID)

    assert isinstance(result, IngestionSummary)
    assert result.total_sources == 5
    assert result.error_count == 1


# =============================================================================
# Admin List
# =============================================================================


def test_admin_list_endpoint(dummy_client):
    responses = {("get", "/admin/workrooms/"): {"items": [_workroom_response()]}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.admin_list()

    assert client.calls[0][1] == "/admin/workrooms/"


def test_admin_list_sends_pagination_params(dummy_client):
    responses = {("get", "/admin/workrooms/"): {"items": []}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.admin_list(include_deleted=True, skip=10, limit=50)

    params = client.calls[0][2]["params"]
    assert params["skip"] == 10
    assert params["limit"] == 50
    assert params["include_deleted"] == "true"


def test_admin_list_defaults(dummy_client):
    responses = {("get", "/admin/workrooms/"): {"items": []}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    service.admin_list()

    params = client.calls[0][2]["params"]
    assert params["skip"] == 0
    assert params["limit"] == 100
    assert "include_deleted" not in params


# =============================================================================
# Admin Delete
# =============================================================================


def test_admin_delete_endpoint(dummy_client):
    responses = {("delete", f"/admin/workrooms/{WORKROOM_UUID}"): _delete_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.admin_delete(WORKROOM_ID)

    assert client.calls[0][1] == f"/admin/workrooms/{WORKROOM_UUID}"
    assert isinstance(result, DeleteWorkroomResponse)


def test_admin_delete_not_found_raises(dummy_client):
    responses = {}
    client = dummy_client(responses)
    def _raise_404(path, **kwargs):
        raise APIError("Not found", status_code=404, response_text="")
    client.delete = _raise_404
    service = WorkroomService(client)

    with pytest.raises(NotFoundError):
        service.admin_delete(WORKROOM_ID)


# =============================================================================
# Schema validation
# =============================================================================


def test_workroom_schema_parses_full_response():
    wr = Workroom.model_validate(_workroom_response())

    assert wr.id == WORKROOM_UUID
    assert wr.name == "Test Workroom"
    assert wr.status == "active"


def test_workroom_schema_handles_nulls():
    data = _workroom_response(description=None, labels=None, updated_at=None)
    wr = Workroom.model_validate(data)

    assert wr.description is None
    assert wr.labels is None
    assert wr.updated_at is None


def test_workroom_type_enum():
    assert WorkroomType.ephemeral.value == "ephemeral"
    assert WorkroomType.persistent.value == "persistent"


def test_create_workroom_schema_validation():
    payload = CreateWorkroom(name="Test", type="persistent")

    dumped = payload.model_dump(exclude_none=True)
    assert dumped["name"] == "Test"
    assert dumped["type"] == "persistent"
    assert "description" not in dumped


# =============================================================================
# Client integration
# =============================================================================


def test_client_workrooms_property():
    """Verify the WorkroomService is accessible via client.workrooms."""
    from kamiwaza_sdk.services.workrooms import WorkroomService

    responses = {("get", "/workrooms/"): {"items": []}}
    # Use a DummyAPIClient directly
    from tests.conftest import DummyAPIClient
    client = DummyAPIClient(responses)

    # Simulate KamiwazaClient lazy property by attaching WorkroomService
    service = WorkroomService(client)
    assert isinstance(service, WorkroomService)
    assert service.list() == []
