from __future__ import annotations

import io
import uuid

import pytest
from pydantic import ValidationError

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.workrooms import (
    CreateWorkroom,
    DeleteWorkroomResponse,
    ExportManifest,
    IngestionSummary,
    UpdateWorkroom,
    Workroom,
    WorkroomStatus,
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


def test_create_serializes_uuid_attributes(dummy_client):
    responses = {("post", "/workrooms/"): _workroom_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)
    mission_id = uuid.uuid4()

    service.create(
        "My WR",
        "persistent",
        attributes={"mission_id": mission_id},
    )

    assert client.calls[0][2]["json"]["attributes"]["mission_id"] == str(mission_id)


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


def test_list_missing_items_returns_empty(dummy_client):
    responses = {("get", "/workrooms/"): {}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    assert service.list() == []


def test_list_rejects_non_list_items(dummy_client):
    responses = {("get", "/workrooms/"): {"items": "oops"}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    with pytest.raises(APIError, match="expected 'items' list"):
        service.list()


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


def test_update_uses_schema_and_can_clear_fields(dummy_client):
    responses = {
        ("patch", f"/workrooms/{WORKROOM_UUID}"): _workroom_response(
            description=None,
            labels=[],
        )
    }
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.update(
        WORKROOM_ID,
        description=None,
        labels=[],
    )

    payload = client.calls[0][2]["json"]
    assert payload["description"] is None
    assert payload["labels"] == []
    assert result.description is None
    assert result.labels == []


def test_update_validates_payload_fields(dummy_client):
    responses = {("patch", f"/workrooms/{WORKROOM_UUID}"): _workroom_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)

    with pytest.raises(ValidationError):
        service.update(WORKROOM_ID, name="")


def test_update_serializes_uuid_attributes(dummy_client):
    responses = {("patch", f"/workrooms/{WORKROOM_UUID}"): _workroom_response()}
    client = dummy_client(responses)
    service = WorkroomService(client)
    template_id = uuid.uuid4()

    service.update(
        WORKROOM_ID,
        attributes={"template_id": template_id},
    )

    assert client.calls[0][2]["json"]["attributes"]["template_id"] == str(template_id)


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
    responses = {}
    client = dummy_client(responses)
    def _raise_403(path, **kwargs):
        raise APIError("Forbidden", status_code=403, response_text="Global Workroom")
    client.delete = _raise_403
    service = WorkroomService(client)

    with pytest.raises(APIError) as exc_info:
        service.delete(global_id)
    assert exc_info.value.status_code == 403


def test_delete_handles_no_content_response(dummy_client):
    client = dummy_client({})
    client.delete = lambda path, **kwargs: client.calls.append(("delete", path, kwargs)) or None  # noqa: E731
    service = WorkroomService(client)

    result = service.delete(WORKROOM_ID)

    assert result.workroom_id == WORKROOM_UUID
    assert result.status == "deleted"
    assert result.message == ""


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
# Session Binding
# =============================================================================


def test_enter_posts_to_enter_endpoint(dummy_client):
    responses = {
        ("post", f"/workrooms/{WORKROOM_UUID}/enter"): {
            "workroom_id": WORKROOM_ID,
            "access_token": "scoped-token",
            "expires_in": 3600,
            "message": "Workroom session bound",
        }
    }
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.enter(WORKROOM_ID)

    assert client.calls[0][1] == f"/workrooms/{WORKROOM_UUID}/enter"
    assert result.workroom_id == WORKROOM_UUID
    assert result.access_token == "scoped-token"
    assert result.expires_in == 3600


def test_leave_posts_to_leave_endpoint(dummy_client):
    responses = {
        ("post", "/workrooms/leave"): {
            "workroom_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "access_token": "global-token",
            "expires_in": 3600,
            "message": "Returned to Global Workroom",
        }
    }
    client = dummy_client(responses)
    service = WorkroomService(client)

    result = service.leave()

    assert client.calls[0][1] == "/workrooms/leave"
    assert str(result.workroom_id) == "ffffffff-ffff-ffff-ffff-ffffffffffff"
    assert result.access_token == "global-token"
    assert result.expires_in == 3600


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


def test_export_bundle_streams_to_output_path(dummy_client, tmp_path):
    responses = {("post", f"/workrooms/{WORKROOM_UUID}/export"): _workroom_response()}
    client = dummy_client(responses)
    chunks = [b"PK\x03\x04", b"zipdata"]

    class DummyResponse:
        def iter_content(self, chunk_size=0):
            assert chunk_size > 0
            yield from chunks

    client.post = lambda path, **kwargs: (  # noqa: E731
        client.calls.append(("post", path, kwargs)) or DummyResponse()
    )
    service = WorkroomService(client)
    output_path = tmp_path / "bundle.zip"

    result = service.export_bundle(WORKROOM_ID, output_path=output_path)

    assert result == output_path
    assert output_path.read_bytes() == b"".join(chunks)
    assert client.calls[0][2]["stream"] is True


def test_export_bundle_streams_to_file_object(dummy_client):
    responses = {("post", f"/workrooms/{WORKROOM_UUID}/export"): _workroom_response()}
    client = dummy_client(responses)

    class DummyResponse:
        def iter_content(self, chunk_size=0):
            assert chunk_size > 0
            yield b"chunk-1"
            yield b"chunk-2"

    client.post = lambda path, **kwargs: (  # noqa: E731
        client.calls.append(("post", path, kwargs)) or DummyResponse()
    )
    service = WorkroomService(client)
    buffer = io.BytesIO()

    result = service.export_bundle(WORKROOM_ID, file_obj=buffer)

    assert result is buffer
    assert buffer.getvalue() == b"chunk-1chunk-2"


def test_export_bundle_rejects_multiple_stream_targets(dummy_client):
    service = WorkroomService(dummy_client({}))

    with pytest.raises(ValueError, match="either output_path or file_obj"):
        service.export_bundle(WORKROOM_ID, output_path="bundle.zip", file_obj=io.BytesIO())


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


def test_admin_list_missing_items_returns_empty(dummy_client):
    responses = {("get", "/admin/workrooms/"): {}}
    client = dummy_client(responses)
    service = WorkroomService(client)

    assert service.admin_list() == []


def test_admin_list_validates_limit_range(dummy_client):
    service = WorkroomService(dummy_client({}))

    with pytest.raises(ValueError, match="between 1 and 1000"):
        service.admin_list(limit=0)


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


def test_admin_delete_handles_no_content_response(dummy_client):
    client = dummy_client({})
    client.delete = lambda path, **kwargs: client.calls.append(("delete", path, kwargs)) or None  # noqa: E731
    service = WorkroomService(client)

    result = service.admin_delete(WORKROOM_ID)

    assert result.workroom_id == WORKROOM_UUID
    assert result.status == "deleted"
    assert result.message == ""


# =============================================================================
# Schema validation
# =============================================================================


def test_workroom_schema_parses_full_response():
    wr = Workroom.model_validate(_workroom_response())

    assert wr.id == WORKROOM_UUID
    assert wr.name == "Test Workroom"
    assert wr.status == WorkroomStatus.active
    assert wr.type == WorkroomType.persistent


def test_workroom_schema_handles_nulls():
    data = _workroom_response(description=None, labels=None, updated_at=None)
    wr = Workroom.model_validate(data)

    assert wr.description is None
    assert wr.labels is None
    assert wr.updated_at is None


def test_response_models_allow_extra_fields():
    wr = Workroom.model_validate(_workroom_response(unexpected_field="ok"))
    delete_response = DeleteWorkroomResponse.model_validate(
        {**_delete_response(), "audit_id": "evt-1"}
    )
    manifest = ExportManifest.model_validate(
        {
            **_manifest_response(),
            "generated_at": "2025-01-01T00:00:00Z",
        }
    )
    summary = IngestionSummary.model_validate(
        {
            **_ingestion_response(),
            "extra": {"notes": 1},
        }
    )

    assert wr.id == WORKROOM_UUID
    assert delete_response.status == "deleted"
    assert len(manifest.items) == 3
    assert summary.total_sources == 5


def test_workroom_type_enum():
    assert WorkroomType.ephemeral.value == "ephemeral"
    assert WorkroomType.persistent.value == "persistent"


def test_create_workroom_schema_validation():
    payload = CreateWorkroom(name="Test", type="persistent")

    dumped = payload.model_dump(exclude_none=True)
    assert dumped["name"] == "Test"
    assert dumped["type"] == "persistent"
    assert "description" not in dumped


def test_update_workroom_schema_uses_exclude_unset():
    payload = UpdateWorkroom(description=None)

    dumped = payload.model_dump(exclude_unset=True)
    assert dumped == {"description": None}


# =============================================================================
# Client integration
# =============================================================================


def test_client_workrooms_property():
    """Verify the WorkroomService is accessible via client.workrooms."""
    from kamiwaza_sdk import KamiwazaClient
    from kamiwaza_sdk.services import WorkroomService

    client = KamiwazaClient("https://kamiwaza.test/api", api_key="test-token")

    service = client.workrooms

    assert isinstance(service, WorkroomService)
    assert client.workrooms is service


def test_ensure_uuid_raises_contextual_value_error(dummy_client):
    service = WorkroomService(dummy_client({}))

    with pytest.raises(ValueError, match="Invalid workroom UUID"):
        service.get("not-a-uuid")
