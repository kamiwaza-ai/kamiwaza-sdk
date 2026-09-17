"""Service for interacting with Skills Library endpoints."""

from __future__ import annotations

import json
import os
import re
from enum import Enum
from typing import Sequence
from urllib.parse import unquote
from uuid import UUID

import requests

from ..exceptions import APIError, NotFoundError
from ..schemas.markings import Marking
from ..schemas.skills import (
    SkillLibraryDetailResponse,
    SkillLibraryExportRequest,
    SkillLibraryListResponse,
    SkillLibraryUpdateRequest,
    SkillPackageContent,
    SkillPackageDownload,
)
from .base_service import BaseService

_FILENAME_STAR_RE = re.compile(r"filename\*=([^;]+)", re.IGNORECASE)
_FILENAME_RE = re.compile(r'filename="([^"]+)"|filename=([^;]+)', re.IGNORECASE)


class _OmittedMarking(Enum):
    VALUE = "omitted"


class SkillsService(BaseService):
    """Typed wrapper around the Skills Library API."""

    def list_skills(
        self,
        *,
        q: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SkillLibraryListResponse:
        """List skills visible to the current caller."""
        params = {"page": page, "page_size": page_size}
        if q is not None:
            params["q"] = q
        if category is not None:
            params["category"] = category
        if tag is not None:
            params["tag"] = tag
        if status is not None:
            params["status"] = status
        response = self.client.get("/skills", params=params)
        return SkillLibraryListResponse.model_validate(response)

    def get_skill(self, skill_id: UUID | str) -> SkillLibraryDetailResponse:
        """Get a single skill by ID."""
        try:
            response = self.client.get(f"/skills/{skill_id}")
        except APIError as exc:
            if exc.status_code == 404:
                raise NotFoundError(f"Skill '{skill_id}' not found") from exc
            raise
        return SkillLibraryDetailResponse.model_validate(response)

    def import_skill_package(
        self,
        *,
        filename: str,
        file_content: SkillPackageContent,
        content_type: str = "application/zip",
        marking: Marking | dict | None | _OmittedMarking = _OmittedMarking.VALUE,
    ) -> SkillLibraryDetailResponse:
        """Import a raw package or one-entry version-2 snapshot as a draft.

        ``marking`` is the full final envelope, applied before the first write.
        Omit it to preserve the package/snapshot marking. Explicit ``None`` sends
        null; the server refuses removal of existing package protection. A
        snapshot already carries its current marking and needs no override.
        """
        upload_filename = self._sanitize_filename(filename, default="skill.zip")
        form = {}
        if marking is not _OmittedMarking.VALUE:
            normalized = None if marking is None else Marking.model_validate(marking).model_dump(mode="json")
            form["data"] = {"marking": json.dumps(normalized)}
        response = self.client.post(
            "/skills/import",
            files={"file": (upload_filename, file_content, content_type)},
            **form,
        )
        return SkillLibraryDetailResponse.model_validate(response)

    def download_skill_package(self, skill_id: UUID | str) -> SkillPackageDownload:
        """Download the published package for a skill."""
        try:
            response = self.client.get(
                f"/skills/{skill_id}/package",
                expect_json=False,
            )
        except APIError as exc:
            if exc.status_code == 404:
                raise NotFoundError(f"Skill '{skill_id}' not found") from exc
            raise
        return self._parse_download_response(
            response,
            default_filename=f"{skill_id}.zip",
        )

    def export_skill_package(self, skill_id: UUID | str) -> SkillPackageDownload:
        """Export a version-2 snapshot with current metadata and unchanged source ZIP."""
        try:
            response = self.client.get(
                f"/skills/{skill_id}/export",
                expect_json=False,
            )
        except APIError as exc:
            if exc.status_code == 404:
                raise NotFoundError(f"Skill '{skill_id}' not found") from exc
            raise
        return self._parse_download_response(
            response,
            default_filename=f"{skill_id}.zip",
        )

    def export_skills_bundle(
        self,
        skill_ids: Sequence[UUID | str],
    ) -> SkillPackageDownload:
        """Export version-2 snapshots; multi-entry imports are performed individually."""
        payload = SkillLibraryExportRequest.model_validate(
            {"skill_ids": list(skill_ids)}
        )
        try:
            response = self.client.post(
                "/skills/export",
                json=payload.model_dump(mode="json"),
                expect_json=False,
            )
        except APIError as exc:
            if exc.status_code == 404:
                raise NotFoundError("One or more skills were not found") from exc
            raise
        return self._parse_download_response(
            response,
            default_filename="skills-export.zip",
        )

    def update_skill_metadata(
        self,
        skill_id: UUID | str,
        update: SkillLibraryUpdateRequest,
    ) -> SkillLibraryDetailResponse:
        """Update mutable metadata for a skill."""
        try:
            response = self.client.put(
                f"/skills/{skill_id}",
                json=update.model_dump(exclude_unset=True),
            )
        except APIError as exc:
            if exc.status_code == 404:
                raise NotFoundError(f"Skill '{skill_id}' not found") from exc
            raise
        return SkillLibraryDetailResponse.model_validate(response)

    def delete_skill(self, skill_id: UUID | str) -> bool:
        """Soft-delete a skill."""
        try:
            self.client.delete(f"/skills/{skill_id}")
        except APIError as exc:
            if exc.status_code == 404:
                raise NotFoundError(f"Skill '{skill_id}' not found") from exc
            raise
        return True

    @staticmethod
    def _parse_download_response(
        response: requests.Response,
        *,
        default_filename: str,
    ) -> SkillPackageDownload:
        content_type = response.headers.get("content-type", "application/octet-stream")
        content_disposition = response.headers.get("content-disposition", "")
        return SkillPackageDownload(
            filename=SkillsService._extract_filename(
                content_disposition,
                default=default_filename,
            ),
            content_type=content_type,
            content=response.content,
        )

    @staticmethod
    def _extract_filename(content_disposition: str, *, default: str) -> str:
        if not content_disposition:
            return default

        star_match = _FILENAME_STAR_RE.search(content_disposition)
        if star_match:
            encoded = star_match.group(1).strip().strip('"')
            parts = encoded.split("'", 2)
            if len(parts) == 3:
                return SkillsService._sanitize_filename(
                    unquote(parts[2]),
                    default=default,
                )
            return SkillsService._sanitize_filename(unquote(encoded), default=default)

        filename_match = _FILENAME_RE.search(content_disposition)
        if not filename_match:
            return default

        filename = filename_match.group(1) or filename_match.group(2) or default
        return SkillsService._sanitize_filename(
            filename.strip().strip('"'),
            default=default,
        )

    @staticmethod
    def _sanitize_filename(filename: str, *, default: str) -> str:
        sanitized = os.path.basename(filename.replace("\\", "/")).strip()
        if sanitized in {"", ".", ".."}:
            return default
        return sanitized
