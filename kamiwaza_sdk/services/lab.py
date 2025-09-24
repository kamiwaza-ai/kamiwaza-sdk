# kamiwaza_sdk/services/lab.py

from typing import List, Optional, Dict
from uuid import UUID
from ..schemas.lab import CreateLabRequest, Lab, LabResponse
from .base_service import BaseService

class LabService(BaseService):
    def list_labs(self) -> List[Lab]:
        """List all labs."""
        response = self.client.get("/lab/labs")
        return [Lab.model_validate(item) for item in response]

    def create_lab(self, username: str, resources: Optional[Dict[str, str]] = None) -> Lab:
        """Create a new lab."""
        request = CreateLabRequest(username=username, resources=resources)
        response = self.client.post("/lab/labs", json=request.model_dump())
        return LabResponse.model_validate(response).lab

    def get_lab(self, lab_id: UUID) -> Lab:
        """Get a specific lab."""
        response = self.client.get(f"/lab/labs/{lab_id}")
        return LabResponse.model_validate(response).lab

    def delete_lab(self, lab_id: UUID) -> None:
        """Delete a specific lab."""
        self.client.delete(f"/lab/labs/{lab_id}")