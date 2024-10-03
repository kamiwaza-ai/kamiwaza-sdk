# kamiwaza_client/services/lab.py

from typing import Dict, Optional, List
from uuid import UUID
from .base_service import BaseService

class LabService(BaseService):
    def list_labs(self) -> List[Dict]:
        """List all labs."""
        return self.client.get("/lab/labs")

    def create_lab(self, username: str, resources: Optional[Dict[str, str]] = None) -> Dict:
        """Create a new lab."""
        data = {"username": username, "resources": resources}
        return self.client.post("/lab/labs", json=data)

    def get_lab(self, lab_id: UUID) -> Dict:
        """Get a specific lab."""
        return self.client.get(f"/lab/labs/{lab_id}")

    def delete_lab(self, lab_id: UUID) -> None:
        """Delete a specific lab."""
        return self.client.delete(f"/lab/labs/{lab_id}")