"""Live integration tests for the Skills Library SDK service."""

from __future__ import annotations

import io
import zipfile
from uuid import uuid4

import pytest

from kamiwaza_sdk.exceptions import APIError, NotFoundError
from kamiwaza_sdk.schemas.skills import SkillLibraryUpdateRequest
from kamiwaza_sdk.services.skills import SkillsService

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def _build_skill_package_bytes(
    *, name: str, display_name: str, description: str
) -> bytes:
    skill_markdown = f"""---
name: {name}
description: {description}
metadata:
  kamiwaza:
    category: export
    tags:
      - integration
      - pdf
---

# {display_name}

Use this skill in integration tests.
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{name}/SKILL.md", skill_markdown)
        archive.writestr(f"{name}/scripts/render.sh", "#!/bin/sh\necho render\n")
    return buffer.getvalue()


def _skills_service_available(service: SkillsService) -> bool:
    try:
        service.list_skills(page_size=1)
        return True
    except APIError as exc:
        if exc.status_code in {404, 503}:
            return False
        raise


def _skip_for_unavailable_mutation(exc: APIError) -> None:
    if exc.status_code == 403:
        pytest.skip("Skills mutation paths require operator/admin access")
    if exc.status_code == 503:
        pytest.skip("Skills Library storage is unavailable in this environment")
    raise exc


def test_skills_service_lifecycle(live_kamiwaza_client) -> None:
    service = live_kamiwaza_client.skills
    assert isinstance(service, SkillsService)

    if not _skills_service_available(service):
        pytest.skip("Skills Library endpoints are unavailable in this environment")

    unique_suffix = uuid4().hex[:8]
    skill_name = f"sdk-skill-{unique_suffix}"
    display_name = f"SDK Skill {unique_suffix}"
    description = "SDK live integration skill"

    created = None
    try:
        try:
            created = service.import_skill_package(
                filename=f"{skill_name}.zip",
                file_content=_build_skill_package_bytes(
                    name=skill_name,
                    display_name=display_name,
                    description=description,
                ),
            )
        except APIError as exc:
            _skip_for_unavailable_mutation(exc)

        assert created.name == skill_name
        assert created.status == "draft"
        assert created.package_summary.root_dir == skill_name

        detail = service.get_skill(created.id)
        assert detail.id == created.id
        assert detail.tags == ["integration", "pdf"]

        published = service.update_skill_metadata(
            created.id,
            SkillLibraryUpdateRequest(status="published"),
        )
        assert published.status == "published"

        listing = service.list_skills(
            q=skill_name,
            category="export",
            tag="integration",
            status="published",
            page_size=100,
        )
        matching_ids = {item.id for item in listing.items}
        assert created.id in matching_ids

        package_download = service.download_skill_package(created.id)
        assert package_download.filename == f"{skill_name}.zip"
        assert package_download.content_type == "application/zip"
        assert package_download.content

        exported = service.export_skill_package(created.id)
        assert exported.filename == f"{skill_name}.zip"
        assert exported.content_type == "application/zip"
        assert exported.content

        bundle = service.export_skills_bundle([created.id])
        assert bundle.filename == "skills-export.zip"
        assert bundle.content_type == "application/zip"
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            assert archive.namelist() == [f"{skill_name}.zip"]
            assert archive.read(f"{skill_name}.zip")

        assert service.delete_skill(created.id) is True
        created = None

    finally:
        if created is not None:
            try:
                service.delete_skill(created.id)
            except (APIError, NotFoundError):
                pass
