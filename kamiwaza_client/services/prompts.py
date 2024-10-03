# kamiwaza_client/services/prompts.py

from typing import Dict, List, Optional
from uuid import UUID
from .base_service import BaseService

class PromptsService(BaseService):
    def create_role(self, role_data: Dict) -> Dict:
        """Create a new role."""
        return self.client.post("/prompts/roles/", json=role_data)

    def list_roles(self, skip: int = 0, limit: int = 100) -> List[Dict]:
        """Retrieve a list of roles."""
        params = {"skip": skip, "limit": limit}
        return self.client.get("/prompts/roles/", params=params)

    def get_role(self, role_id: UUID) -> Dict:
        """Retrieve a role by its ID."""
        return self.client.get(f"/prompts/roles/{role_id}")

    def create_system(self, system_data: Dict) -> Dict:
        """Create a new system."""
        return self.client.post("/prompts/systems/", json=system_data)

    def list_systems(self, skip: int = 0, limit: int = 100) -> List[Dict]:
        """Retrieve a list of systems."""
        params = {"skip": skip, "limit": limit}
        return self.client.get("/prompts/systems/", params=params)

    def get_system(self, system_id: UUID) -> Dict:
        """Retrieve a system by its ID."""
        return self.client.get(f"/prompts/systems/{system_id}")

    def create_element(self, element_data: Dict) -> Dict:
        """Create a new element."""
        return self.client.post("/prompts/elements/", json=element_data)

    def list_elements(self, skip: int = 0, limit: int = 100) -> List[Dict]:
        """Retrieve a list of elements."""
        params = {"skip": skip, "limit": limit}
        return self.client.get("/prompts/elements/", params=params)

    def get_element(self, element_id: UUID) -> Dict:
        """Retrieve an element by its ID."""
        return self.client.get(f"/prompts/elements/{element_id}")

    def create_template(self, template_data: Dict) -> Dict:
        """Create a new template."""
        return self.client.post("/prompts/templates/", json=template_data)

    def list_templates(self, skip: int = 0, limit: int = 100) -> List[Dict]:
        """Retrieve a list of templates."""
        params = {"skip": skip, "limit": limit}
        return self.client.get("/prompts/templates/", params=params)

    def get_template(self, template_id: UUID) -> Dict:
        """Retrieve a template by its ID."""
        return self.client.get(f"/prompts/templates/{template_id}")