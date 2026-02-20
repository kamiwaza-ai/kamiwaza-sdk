# kamiwaza_sdk/services/workrooms.py

from typing import List, Optional, Union
from uuid import UUID
import logging

from .base_service import BaseService
from ..schemas.workrooms import (
    CreateWorkroom,
    DeleteWorkroomResponse,
    ExportManifest,
    IngestionSummary,
    Workroom,
)
from ..exceptions import APIError, NotFoundError


class WorkroomService(BaseService):
    """Service for managing workrooms in the Kamiwaza platform.

    Workrooms are isolated resource containers that scope datasets,
    app deployments, and other artifacts. Each workroom has a lifecycle
    (active -> archived -> deleted) and an owner.
    """

    def __init__(self, client):
        super().__init__(client)
        self.logger = logging.getLogger(__name__)

    # -------------------------------------------------------------------------
    # User CRUD
    # -------------------------------------------------------------------------

    def create(
        self,
        name: str,
        workroom_type: str,
        *,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        classification: Optional[str] = None,
        attributes: Optional[dict] = None,
        scg_references: Optional[List[str]] = None,
    ) -> Workroom:
        """Create a new workroom.

        Args:
            name: Workroom name (1-255 characters).
            workroom_type: "ephemeral" or "persistent".
            description: Optional description (max 1024 chars).
            labels: Optional list of string labels.
            classification: Optional classification label.
            attributes: Optional extensible key-value pairs.
            scg_references: Optional SCG identifiers.

        Returns:
            The created Workroom object.

        Raises:
            APIError: If creation fails (e.g. limit exceeded -> 409).
        """
        payload = CreateWorkroom(
            name=name,
            type=workroom_type,
            description=description,
            labels=labels,
            classification=classification,
            attributes=attributes,
            scg_references=scg_references,
        )
        response = self.client.post(
            "/workrooms/",
            json=payload.model_dump(exclude_none=True),
        )
        return Workroom.model_validate(response)

    def list(self, *, include_archived: bool = False) -> List[Workroom]:
        """List workrooms owned by the authenticated user.

        Args:
            include_archived: If True, include archived workrooms.

        Returns:
            List of Workroom objects.
        """
        params = {}
        if include_archived:
            params["include_archived"] = "true"
        response = self.client.get("/workrooms/", params=params)
        return [Workroom.model_validate(item) for item in response["items"]]

    def get(self, workroom_id: Union[str, UUID]) -> Workroom:
        """Get a workroom by ID.

        Args:
            workroom_id: UUID of the workroom.

        Returns:
            Workroom object.

        Raises:
            NotFoundError: If workroom not found or not owned by caller.
        """
        wid = self._ensure_uuid(workroom_id)
        try:
            response = self.client.get(f"/workrooms/{wid}")
            return Workroom.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Workroom {wid} not found")
            raise

    def update(
        self,
        workroom_id: Union[str, UUID],
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        classification: Optional[str] = None,
        attributes: Optional[dict] = None,
        scg_references: Optional[List[str]] = None,
    ) -> Workroom:
        """Partial update of workroom metadata.

        Args:
            workroom_id: UUID of the workroom.
            name: New name (optional).
            description: New description (optional).
            labels: New labels (optional).
            classification: New classification (optional).
            attributes: New attributes (optional).
            scg_references: New SCG references (optional).

        Returns:
            Updated Workroom object.

        Raises:
            NotFoundError: If workroom not found.
            APIError: If validation fails (400) or workroom is read-only (409).
        """
        wid = self._ensure_uuid(workroom_id)
        fields = {
            "name": name,
            "description": description,
            "labels": labels,
            "classification": classification,
            "attributes": attributes,
            "scg_references": scg_references,
        }
        payload = {k: v for k, v in fields.items() if v is not None}
        try:
            response = self.client.patch(
                f"/workrooms/{wid}",
                json=payload,
            )
            return Workroom.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Workroom {wid} not found")
            raise

    def delete(self, workroom_id: Union[str, UUID]) -> DeleteWorkroomResponse:
        """Delete a workroom and all associated resources.

        Args:
            workroom_id: UUID of the workroom.

        Returns:
            DeleteWorkroomResponse with status and message.

        Raises:
            NotFoundError: If workroom not found.
            APIError: If workroom is the Global Workroom (403).
        """
        wid = self._ensure_uuid(workroom_id)
        try:
            response = self.client.delete(f"/workrooms/{wid}")
            return DeleteWorkroomResponse.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Workroom {wid} not found")
            raise

    # -------------------------------------------------------------------------
    # Lifecycle operations
    # -------------------------------------------------------------------------

    def archive(self, workroom_id: Union[str, UUID]) -> Workroom:
        """Archive a workroom (makes it read-only).

        Args:
            workroom_id: UUID of the workroom.

        Returns:
            Updated Workroom object with status="archived".

        Raises:
            NotFoundError: If workroom not found.
            APIError: If workroom is already archived/deleted (409)
                      or is the Global Workroom (403).
        """
        wid = self._ensure_uuid(workroom_id)
        try:
            response = self.client.post(f"/workrooms/{wid}/archive")
            return Workroom.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Workroom {wid} not found")
            raise

    # -------------------------------------------------------------------------
    # Export & ingestion
    # -------------------------------------------------------------------------

    def get_export_manifest(
        self, workroom_id: Union[str, UUID]
    ) -> ExportManifest:
        """Get categorized list of workroom contents with export eligibility.

        Args:
            workroom_id: UUID of the workroom.

        Returns:
            ExportManifest with list of items and their exportability.

        Raises:
            NotFoundError: If workroom not found.
        """
        wid = self._ensure_uuid(workroom_id)
        try:
            response = self.client.get(f"/workrooms/{wid}/export/manifest")
            return ExportManifest.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Workroom {wid} not found")
            raise

    def export_bundle(self, workroom_id: Union[str, UUID]) -> bytes:
        """Download a ZIP bundle of exportable workroom contents.

        Args:
            workroom_id: UUID of the workroom.

        Returns:
            Raw bytes of the ZIP archive.

        Raises:
            NotFoundError: If workroom not found.
        """
        wid = self._ensure_uuid(workroom_id)
        try:
            response = self.client.post(
                f"/workrooms/{wid}/export",
                expect_json=False,
            )
            return response.content
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Workroom {wid} not found")
            raise

    def get_ingestion_summary(
        self, workroom_id: Union[str, UUID]
    ) -> IngestionSummary:
        """Get aggregated ingestion statistics for the workroom.

        Args:
            workroom_id: UUID of the workroom.

        Returns:
            IngestionSummary with source counts, date ranges, errors.

        Raises:
            NotFoundError: If workroom not found.
        """
        wid = self._ensure_uuid(workroom_id)
        try:
            response = self.client.get(
                f"/workrooms/{wid}/ingestion/summary"
            )
            return IngestionSummary.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Workroom {wid} not found")
            raise

    # -------------------------------------------------------------------------
    # Admin operations
    # -------------------------------------------------------------------------

    def admin_list(
        self,
        *,
        include_deleted: bool = False,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Workroom]:
        """List all workrooms across all users (admin only).

        Args:
            include_deleted: If True, include deleted/purging workrooms.
            skip: Number of records to skip (pagination offset).
            limit: Maximum records to return (1-1000).

        Returns:
            List of Workroom objects.
        """
        params: dict = {"skip": skip, "limit": limit}
        if include_deleted:
            params["include_deleted"] = "true"
        response = self.client.get("/admin/workrooms/", params=params)
        return [Workroom.model_validate(item) for item in response["items"]]

    def admin_delete(
        self, workroom_id: Union[str, UUID]
    ) -> DeleteWorkroomResponse:
        """Admin delete - purges any workroom regardless of owner.

        Args:
            workroom_id: UUID of the workroom.

        Returns:
            DeleteWorkroomResponse with status and message.

        Raises:
            NotFoundError: If workroom not found.
            APIError: If workroom is the Global Workroom (403).
        """
        wid = self._ensure_uuid(workroom_id)
        try:
            response = self.client.delete(f"/admin/workrooms/{wid}")
            return DeleteWorkroomResponse.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Workroom {wid} not found")
            raise

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _ensure_uuid(value: Union[str, UUID]) -> UUID:
        """Coerce string to UUID if needed."""
        if isinstance(value, str):
            return UUID(value)
        return value
