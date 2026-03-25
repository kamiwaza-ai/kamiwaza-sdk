from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from kamiwaza_sdk.client import KamiwazaClient
from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.skills import (
    SkillLibraryDetailResponse,
    SkillLibraryListResponse,
    SkillLibraryUpdateRequest,
    SkillPackageDownload,
)
from kamiwaza_sdk.services.skills import SkillsService

pytestmark = pytest.mark.unit


class _RaisingClient:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def _dispatch(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        response = self.responses[(method, path)]
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, path: str, **kwargs):
        return self._dispatch("get", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self._dispatch("post", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self._dispatch("put", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self._dispatch("delete", path, **kwargs)


def _detail_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "pdf-generator",
        "display_name": "PDF Generator",
        "description": "Generate PDFs from markdown.",
        "category": "export",
        "trigger": None,
        "inputs": [],
        "classification": None,
        "status": "draft",
        "tags": ["pdf", "report"],
        "content_checksum": "abc123",
        "metadata": {"tags": ["pdf", "report"]},
        "package_summary": {
            "root_dir": "pdf-generator",
            "entries": ["SKILL.md", "scripts/render.py"],
            "package_size_bytes": 1234,
            "has_scripts": True,
            "has_references": False,
            "has_assets": False,
        },
        "created_by": "operator-1",
        "created_at": "2026-03-25T00:00:00Z",
        "updated_at": "2026-03-26T00:00:00Z",
    }


def _list_payload() -> dict:
    detail = _detail_payload()
    return {
        "items": [
            {
                "id": detail["id"],
                "name": detail["name"],
                "display_name": detail["display_name"],
                "description": detail["description"],
                "category": detail["category"],
                "status": detail["status"],
                "classification": detail["classification"],
                "tags": detail["tags"],
                "content_checksum": detail["content_checksum"],
                "created_at": detail["created_at"],
                "updated_at": detail["updated_at"],
            }
        ],
        "total": 1,
        "page": 1,
        "page_size": 20,
    }


def _download_response(
    *,
    filename: str = "pdf-generator.zip",
    content_type: str = "application/zip",
    content: bytes = b"zip-bytes",
):
    return SimpleNamespace(
        headers={
            "content-disposition": f'attachment; filename="{filename}"',
            "content-type": content_type,
        },
        content=content,
    )


def test_client_exposes_skills_service(monkeypatch):
    monkeypatch.setenv("KAMIWAZA_BASE_URL", "https://example.test/api")
    client = KamiwazaClient()

    assert isinstance(client.skills, SkillsService)
    assert client.skills is client.skills


def test_list_skills_builds_expected_query_params(dummy_client):
    client = dummy_client({("get", "/skills"): _list_payload()})
    service = SkillsService(client)

    result = service.list_skills(
        q="pdf",
        category="export",
        tag="report",
        status="published",
        page=2,
        page_size=50,
    )

    assert isinstance(result, SkillLibraryListResponse)
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("get", "/skills")
    assert kwargs["params"] == {
        "q": "pdf",
        "category": "export",
        "tag": "report",
        "status": "published",
        "page": 2,
        "page_size": 50,
    }


def test_get_skill_returns_typed_detail(dummy_client):
    payload = _detail_payload()
    client = dummy_client({("get", f"/skills/{payload['id']}"): payload})
    service = SkillsService(client)

    result = service.get_skill(payload["id"])

    assert isinstance(result, SkillLibraryDetailResponse)
    assert result.name == "pdf-generator"


def test_get_skill_maps_404(dummy_client):
    skill_id = str(uuid4())
    client = _RaisingClient(
        {
            ("get", f"/skills/{skill_id}"): APIError(
                "not found",
                status_code=404,
            )
        }
    )
    service = SkillsService(client)

    with pytest.raises(NotFoundError, match="not found"):
        service.get_skill(skill_id)


def test_import_skill_package_sends_multipart_upload(dummy_client):
    client = dummy_client({("post", "/skills/import"): _detail_payload()})
    service = SkillsService(client)

    result = service.import_skill_package(
        filename="skill.zip",
        file_content=b"zip-bytes",
    )

    assert isinstance(result, SkillLibraryDetailResponse)
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/skills/import")
    assert kwargs["files"] == {"file": ("skill.zip", b"zip-bytes", "application/zip")}


def test_download_skill_package_returns_typed_download(dummy_client):
    skill_id = str(uuid4())
    client = dummy_client(
        {
            ("get", f"/skills/{skill_id}/package"): _download_response(),
        }
    )
    service = SkillsService(client)

    result = service.download_skill_package(skill_id)

    assert isinstance(result, SkillPackageDownload)
    assert result.filename == "pdf-generator.zip"
    assert result.content_type == "application/zip"
    assert result.content == b"zip-bytes"
    method, path, kwargs = client.calls[0]
    assert kwargs["expect_json"] is False
    assert (method, path) == ("get", f"/skills/{skill_id}/package")


def test_download_skill_package_maps_404(dummy_client):
    skill_id = str(uuid4())
    client = _RaisingClient(
        {
            ("get", f"/skills/{skill_id}/package"): APIError(
                "missing",
                status_code=404,
            )
        }
    )
    service = SkillsService(client)

    with pytest.raises(NotFoundError, match="not found"):
        service.download_skill_package(skill_id)


def test_export_skill_package_returns_typed_download(dummy_client):
    skill_id = str(uuid4())
    client = dummy_client(
        {
            ("get", f"/skills/{skill_id}/export"): _download_response(),
        }
    )
    service = SkillsService(client)

    result = service.export_skill_package(skill_id)

    assert result.filename == "pdf-generator.zip"
    assert result.content == b"zip-bytes"
    method, path, kwargs = client.calls[0]
    assert kwargs["expect_json"] is False
    assert (method, path) == ("get", f"/skills/{skill_id}/export")


def test_export_skill_package_maps_404(dummy_client):
    skill_id = str(uuid4())
    client = _RaisingClient(
        {
            ("get", f"/skills/{skill_id}/export"): APIError(
                "missing",
                status_code=404,
            )
        }
    )
    service = SkillsService(client)

    with pytest.raises(NotFoundError, match="not found"):
        service.export_skill_package(skill_id)


def test_export_skills_bundle_posts_json_and_parses_download(dummy_client):
    first_id = uuid4()
    second_id = uuid4()
    client = dummy_client(
        {
            ("post", "/skills/export"): _download_response(
                filename="skills-export.zip",
                content=b"bundle-bytes",
            ),
        }
    )
    service = SkillsService(client)

    result = service.export_skills_bundle([str(first_id), second_id])

    assert result.filename == "skills-export.zip"
    assert result.content == b"bundle-bytes"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("post", "/skills/export")
    assert kwargs["expect_json"] is False
    assert kwargs["json"] == {"skill_ids": [str(first_id), str(second_id)]}


def test_update_skill_metadata_puts_json_payload(dummy_client):
    payload = _detail_payload()
    client = dummy_client({("put", f"/skills/{payload['id']}"): payload})
    service = SkillsService(client)
    update = SkillLibraryUpdateRequest(
        display_name="PDF Generator v2",
        metadata={"tags": ["pdf"]},
    )

    result = service.update_skill_metadata(payload["id"], update)

    assert result.display_name == "PDF Generator"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("put", f"/skills/{payload['id']}")
    assert kwargs["json"] == {
        "display_name": "PDF Generator v2",
        "metadata": {"tags": ["pdf"]},
    }


def test_update_skill_metadata_maps_404(dummy_client):
    skill_id = str(uuid4())
    client = _RaisingClient(
        {
            ("put", f"/skills/{skill_id}"): APIError(
                "missing",
                status_code=404,
            )
        }
    )
    service = SkillsService(client)

    with pytest.raises(NotFoundError, match="not found"):
        service.update_skill_metadata(
            skill_id,
            SkillLibraryUpdateRequest(status="published"),
        )


def test_delete_skill_returns_true(dummy_client):
    skill_id = str(uuid4())
    client = dummy_client({("delete", f"/skills/{skill_id}"): None})
    service = SkillsService(client)

    assert service.delete_skill(skill_id) is True
    assert client.calls[0] == ("delete", f"/skills/{skill_id}", {})


def test_delete_skill_maps_404(dummy_client):
    skill_id = str(uuid4())
    client = _RaisingClient(
        {
            ("delete", f"/skills/{skill_id}"): APIError(
                "missing",
                status_code=404,
            )
        }
    )
    service = SkillsService(client)

    with pytest.raises(NotFoundError, match="not found"):
        service.delete_skill(skill_id)


def test_extract_filename_prefers_rfc5987_value():
    filename = SkillsService._extract_filename(
        "attachment; filename=ignored.zip; filename*=UTF-8''skills-export.zip",
        default="download.zip",
    )

    assert filename == "skills-export.zip"


def test_skill_detail_schema_accepts_extra_fields():
    payload = _detail_payload()
    payload["extra_field"] = "extra-value"
    payload["package_summary"]["checksum_hint"] = "hint"

    detail = SkillLibraryDetailResponse.model_validate(payload)

    assert detail.model_extra == {"extra_field": "extra-value"}
    assert detail.package_summary.model_extra == {"checksum_hint": "hint"}


def test_skill_package_download_schema_accepts_extra_fields():
    download = SkillPackageDownload.model_validate(
        {
            "filename": "skill.zip",
            "content_type": "application/zip",
            "content": b"zip",
            "etag": "abc123",
        }
    )

    assert download.model_extra == {"etag": "abc123"}


def test_update_request_requires_at_least_one_field():
    with pytest.raises(ValidationError, match="At least one field must be provided"):
        SkillLibraryUpdateRequest()
