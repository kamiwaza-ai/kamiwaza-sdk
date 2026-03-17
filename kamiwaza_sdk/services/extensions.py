# kamiwaza_sdk/services/extensions.py

"""Service for managing K8s-native extensions via the platform API."""

import logging
from typing import List

from ..exceptions import APIError, NotFoundError
from ..schemas.extensions import CreateExtension, Extension
from .base_service import BaseService


class ExtensionService(BaseService):
    """Service for managing K8s-native extensions (KamiwazaExtension CRs)."""

    def __init__(self, client):
        super().__init__(client)
        self.logger = logging.getLogger(__name__)

    def list_extensions(self) -> List[Extension]:
        """List extensions visible to the current user.

        Returns:
            List of Extension objects.
        """
        response = self.client.get("/extensions")
        return [Extension.model_validate(item) for item in response]

    def get_extension(self, name: str) -> Extension:
        """Get a single extension by name.

        Args:
            name: CR name of the extension.

        Returns:
            Extension object with full spec and status.

        Raises:
            NotFoundError: If extension does not exist.
        """
        try:
            response = self.client.get(f"/extensions/{name}")
            return Extension.model_validate(response)
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Extension '{name}' not found") from e
            raise

    def create_extension(self, request: CreateExtension) -> Extension:
        """Create a new KamiwazaExtension CR.

        Args:
            request: Extension specification.

        Returns:
            The newly created Extension.

        Raises:
            APIError: If creation fails.
        """
        response = self.client.post(
            "/extensions",
            json=request.model_dump(),
        )
        return Extension.model_validate(response)

    def delete_extension(self, name: str) -> bool:
        """Delete an extension by name.

        Args:
            name: CR name of the extension.

        Returns:
            True if successfully deleted.

        Raises:
            NotFoundError: If extension does not exist.
            APIError: If deletion fails.
        """
        try:
            self.client.delete(f"/extensions/{name}")
            return True
        except APIError as e:
            if e.status_code == 404:
                raise NotFoundError(f"Extension '{name}' not found") from e
            raise
