# kamiwaza_sdk/services/prompts.py

from typing import List
from uuid import UUID
from ..schemas.prompts import (
    PromptRoleCreate, PromptRole,
    PromptSystemCreate, PromptSystem,
    PromptElementCreate, PromptElement,
    PromptTemplateCreate, PromptTemplate
)
from .base_service import BaseService

class PromptsService(BaseService):
    def create_role(self, role: PromptRoleCreate) -> PromptRole:
        """Create a new role."""
        response = self.client.post("/prompts/roles/", json=role.model_dump())
        return PromptRole.model_validate(response)

    def list_roles(self, skip: int = 0, limit: int = 100) -> List[PromptRole]:
        """Retrieve a list of roles."""
        params = {"skip": skip, "limit": limit}
        response = self.client.get("/prompts/roles/", params=params)
        return [PromptRole.model_validate(item) for item in response]

    def get_role(self, role_id: UUID) -> PromptRole:
        """Retrieve a role by its ID."""
        response = self.client.get(f"/prompts/roles/{role_id}")
        return PromptRole.model_validate(response)

    def create_system(self, system: PromptSystemCreate) -> PromptSystem:
        """Create a new system."""
        response = self.client.post("/prompts/systems/", json=system.model_dump())
        return PromptSystem.model_validate(response)

    def list_systems(self, skip: int = 0, limit: int = 100) -> List[PromptSystem]:
        """Retrieve a list of systems."""
        params = {"skip": skip, "limit": limit}
        response = self.client.get("/prompts/systems/", params=params)
        return [PromptSystem.model_validate(item) for item in response]

    def get_system(self, system_id: UUID) -> PromptSystem:
        """Retrieve a system by its ID."""
        response = self.client.get(f"/prompts/systems/{system_id}")
        return PromptSystem.model_validate(response)

    def create_element(self, element: PromptElementCreate) -> PromptElement:
        """Create a new element."""
        response = self.client.post("/prompts/elements/", json=element.model_dump())
        return PromptElement.model_validate(response)

    def list_elements(self, skip: int = 0, limit: int = 100) -> List[PromptElement]:
        """Retrieve a list of elements."""
        params = {"skip": skip, "limit": limit}
        response = self.client.get("/prompts/elements/", params=params)
        return [PromptElement.model_validate(item) for item in response]

    def get_element(self, element_id: UUID) -> PromptElement:
        """Retrieve an element by its ID."""
        response = self.client.get(f"/prompts/elements/{element_id}")
        return PromptElement.model_validate(response)

    def create_template(self, template: PromptTemplateCreate) -> PromptTemplate:
        """Create a new template."""
        response = self.client.post("/prompts/templates/", json=template.model_dump())
        return PromptTemplate.model_validate(response)

    def list_templates(self, skip: int = 0, limit: int = 100) -> List[PromptTemplate]:
        """Retrieve a list of templates."""
        params = {"skip": skip, "limit": limit}
        response = self.client.get("/prompts/templates/", params=params)
        return [PromptTemplate.model_validate(item) for item in response]

    def get_template(self, template_id: UUID) -> PromptTemplate:
        """Retrieve a template by its ID."""
        response = self.client.get(f"/prompts/templates/{template_id}")
        return PromptTemplate.model_validate(response)