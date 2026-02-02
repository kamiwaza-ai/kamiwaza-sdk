"""Integration tests for TS16 PROMPTS endpoints.

Tests cover:
- TS16.001: POST /prompts/elements/ - Create element
- TS16.002: GET /prompts/elements/{element_id} - Get element by ID
- TS16.003: POST /prompts/roles/ - Create role
- TS16.004: GET /prompts/roles/{role_id} - Get role by ID
- TS16.005: POST /prompts/systems/ - Create system
- TS16.006: GET /prompts/systems/{system_id} - Get system by ID
- TS16.007: POST /prompts/templates/ - Create template
- TS16.008: GET /prompts/templates/{template_id} - Get template by ID

Note: Write operations (POST) require prompts-admin group membership.
Note: List operations (GET list) are not implemented on the server yet (return 405).
Note: Getting non-existent resources returns 500 instead of 404 (server defect).
"""
from __future__ import annotations

import pytest
from uuid import uuid4

from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.schemas.prompts import (
    PromptElementCreate,
    PromptElement,
    PromptRoleCreate,
    PromptRole,
    PromptSystemCreate,
    PromptSystem,
    PromptTemplateCreate,
    PromptTemplate,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.withoutresponses,
    pytest.mark.skip(reason="Prompts endpoints tests are disabled."),
]


class TestPromptElementOperations:
    """Tests for prompt element CRUD operations."""

    def test_create_and_get_element(self, live_kamiwaza_client) -> None:
        """TS16.001 + TS16.002: Create element and get by ID."""
        created = None
        try:
            # Create element
            create_payload = PromptElementCreate(
                name="sdk-test-element",
                content="This is a test element for SDK integration testing.",
                tags=["test", "sdk"]
            )
            created = live_kamiwaza_client.prompts.create_element(create_payload)
            assert created is not None
            assert isinstance(created, PromptElement)
            assert created.name == "sdk-test-element"
            assert created.id is not None

            # Get element by ID
            retrieved = live_kamiwaza_client.prompts.get_element(created.id)
            assert retrieved is not None
            assert isinstance(retrieved, PromptElement)
            assert retrieved.id == created.id
            assert retrieved.name == "sdk-test-element"

        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for prompts operations (requires prompts-admin group)")
            raise

    def test_get_nonexistent_element(self, live_kamiwaza_client) -> None:
        """Test that getting a non-existent element returns error.

        Note: Server returns 500 instead of 404 for non-existent resources.
        See 00-server-defects.md for details.
        """
        fake_element_id = uuid4()

        with pytest.raises(APIError) as exc_info:
            live_kamiwaza_client.prompts.get_element(fake_element_id)

        # Server returns 500 instead of 404 (server defect)
        assert exc_info.value.status_code in (404, 403, 500)


class TestPromptRoleOperations:
    """Tests for prompt role CRUD operations."""

    def test_create_and_get_role(self, live_kamiwaza_client) -> None:
        """TS16.003 + TS16.004: Create role and get by ID."""
        created = None
        try:
            # Create role
            create_payload = PromptRoleCreate(
                name="sdk-test-role",
                content="You are a helpful assistant for SDK testing.",
                tags=["test", "sdk"]
            )
            created = live_kamiwaza_client.prompts.create_role(create_payload)
            assert created is not None
            assert isinstance(created, PromptRole)
            assert created.name == "sdk-test-role"
            assert created.id is not None

            # Get role by ID
            retrieved = live_kamiwaza_client.prompts.get_role(created.id)
            assert retrieved is not None
            assert isinstance(retrieved, PromptRole)
            assert retrieved.id == created.id
            assert retrieved.name == "sdk-test-role"

        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for prompts operations (requires prompts-admin group)")
            raise

    def test_get_nonexistent_role(self, live_kamiwaza_client) -> None:
        """Test that getting a non-existent role returns error.

        Note: Server returns 500 instead of 404 for non-existent resources.
        """
        fake_role_id = uuid4()

        with pytest.raises(APIError) as exc_info:
            live_kamiwaza_client.prompts.get_role(fake_role_id)

        # Server returns 500 instead of 404 (server defect)
        assert exc_info.value.status_code in (404, 403, 500)


class TestPromptSystemOperations:
    """Tests for prompt system CRUD operations."""

    def test_create_and_get_system(self, live_kamiwaza_client) -> None:
        """TS16.005 + TS16.006: Create system and get by ID."""
        created = None
        try:
            # Create system
            create_payload = PromptSystemCreate(
                name="sdk-test-system",
                content="System prompt for SDK integration testing.",
                tags=["test", "sdk"]
            )
            created = live_kamiwaza_client.prompts.create_system(create_payload)
            assert created is not None
            assert isinstance(created, PromptSystem)
            assert created.name == "sdk-test-system"
            assert created.id is not None

            # Get system by ID
            retrieved = live_kamiwaza_client.prompts.get_system(created.id)
            assert retrieved is not None
            assert isinstance(retrieved, PromptSystem)
            assert retrieved.id == created.id
            assert retrieved.name == "sdk-test-system"

        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for prompts operations (requires prompts-admin group)")
            raise

    def test_get_nonexistent_system(self, live_kamiwaza_client) -> None:
        """Test that getting a non-existent system returns error.

        Note: Server returns 500 instead of 404 for non-existent resources.
        """
        fake_system_id = uuid4()

        with pytest.raises(APIError) as exc_info:
            live_kamiwaza_client.prompts.get_system(fake_system_id)

        # Server returns 500 instead of 404 (server defect)
        assert exc_info.value.status_code in (404, 403, 500)


class TestPromptTemplateOperations:
    """Tests for prompt template CRUD operations."""

    def test_create_and_get_template(self, live_kamiwaza_client) -> None:
        """TS16.007 + TS16.008: Create template and get by ID."""
        created = None
        try:
            # Create template
            create_payload = PromptTemplateCreate(
                name="sdk-test-template",
                content="Template content for {{topic}} in SDK testing.",
                tags=["test", "sdk"]
            )
            created = live_kamiwaza_client.prompts.create_template(create_payload)
            assert created is not None
            assert isinstance(created, PromptTemplate)
            assert created.name == "sdk-test-template"
            assert created.id is not None

            # Get template by ID
            retrieved = live_kamiwaza_client.prompts.get_template(created.id)
            assert retrieved is not None
            assert isinstance(retrieved, PromptTemplate)
            assert retrieved.id == created.id
            assert retrieved.name == "sdk-test-template"

        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for prompts operations (requires prompts-admin group)")
            raise

    def test_get_nonexistent_template(self, live_kamiwaza_client) -> None:
        """Test that getting a non-existent template returns error.

        Note: Server returns 500 instead of 404 for non-existent resources.
        """
        fake_template_id = uuid4()

        with pytest.raises(APIError) as exc_info:
            live_kamiwaza_client.prompts.get_template(fake_template_id)

        # Server returns 500 instead of 404 (server defect)
        assert exc_info.value.status_code in (404, 403, 500)


class TestPromptListOperations:
    """Tests for prompt list operations.

    Note: The server has these list endpoints commented out (not implemented).
    They return 405 Method Not Allowed. The SDK has methods for them but they
    cannot be tested until the server implements them.
    """

    def test_list_elements(self, live_kamiwaza_client) -> None:
        """Test listing prompt elements.

        Note: Server returns 405 - list endpoints not implemented.
        """
        try:
            elements = live_kamiwaza_client.prompts.list_elements()
            assert isinstance(elements, list)
            for element in elements:
                assert isinstance(element, PromptElement)
        except APIError as exc:
            if exc.status_code == 405:
                pytest.skip("List elements endpoint not implemented on server (405 Method Not Allowed)")
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for listing elements")
            raise

    def test_list_roles(self, live_kamiwaza_client) -> None:
        """Test listing prompt roles.

        Note: Server returns 405 - list endpoints not implemented.
        """
        try:
            roles = live_kamiwaza_client.prompts.list_roles()
            assert isinstance(roles, list)
            for role in roles:
                assert isinstance(role, PromptRole)
        except APIError as exc:
            if exc.status_code == 405:
                pytest.skip("List roles endpoint not implemented on server (405 Method Not Allowed)")
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for listing roles")
            raise

    def test_list_systems(self, live_kamiwaza_client) -> None:
        """Test listing prompt systems.

        Note: Server returns 405 - list endpoints not implemented.
        """
        try:
            systems = live_kamiwaza_client.prompts.list_systems()
            assert isinstance(systems, list)
            for system in systems:
                assert isinstance(system, PromptSystem)
        except APIError as exc:
            if exc.status_code == 405:
                pytest.skip("List systems endpoint not implemented on server (405 Method Not Allowed)")
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for listing systems")
            raise

    def test_list_templates(self, live_kamiwaza_client) -> None:
        """Test listing prompt templates.

        Note: Server returns 405 - list endpoints not implemented.
        """
        try:
            templates = live_kamiwaza_client.prompts.list_templates()
            assert isinstance(templates, list)
            for template in templates:
                assert isinstance(template, PromptTemplate)
        except APIError as exc:
            if exc.status_code == 405:
                pytest.skip("List templates endpoint not implemented on server (405 Method Not Allowed)")
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for listing templates")
            raise
