"""Tool policy management mixin."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ...schemas.oauth_broker import (
    ToolPolicyCreate,
    ToolPolicyListResponse,
    ToolPolicyResponse,
    ToolPolicyUpdate,
)


class PolicyMixin:
    """Mixin for tool policy CRUD operations."""

    client: Any  # Provided by BaseService when mixed in

    def create_tool_policy(self, policy: ToolPolicyCreate) -> ToolPolicyResponse:
        """
        Create a new tool policy.

        Args:
            policy: Tool policy details

        Returns:
            Created tool policy

        Example:
            >>> policy = ToolPolicyCreate(
            ...     app_installation_id=app_id,
            ...     tool_id="gmail-reader",
            ...     provider="google",
            ...     allowed_operations=["gmail.search", "gmail.getMessage"],
            ...     allowed_scope_subset=["https://www.googleapis.com/auth/gmail.readonly"]
            ... )
            >>> created = client.oauth_broker.create_tool_policy(policy)
        """
        response = self.client.post(
            "/oauth-broker/tool-policies", json=policy.model_dump(exclude_none=True)
        )
        return ToolPolicyResponse.model_validate(response)

    def list_tool_policies(
        self, app_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> ToolPolicyListResponse:
        """
        List tool policies.

        Args:
            app_id: Optional app installation ID to filter by
            limit: Maximum number of results
            offset: Pagination offset

        Returns:
            List of tool policies
        """
        params: dict[str, int | str] = {"limit": limit, "offset": offset}
        if app_id is not None:
            params["app_id"] = str(app_id)

        response = self.client.get("/oauth-broker/tool-policies", params=params)
        return ToolPolicyListResponse.model_validate(response)

    def get_tool_policy(self, policy_id: UUID) -> ToolPolicyResponse:
        """
        Get a specific tool policy.

        Args:
            policy_id: Tool policy ID

        Returns:
            Tool policy details
        """
        response = self.client.get(f"/oauth-broker/tool-policies/{policy_id}")
        return ToolPolicyResponse.model_validate(response)

    def update_tool_policy(
        self, policy_id: UUID, update: ToolPolicyUpdate
    ) -> ToolPolicyResponse:
        """
        Update a tool policy.

        Args:
            policy_id: Tool policy ID
            update: Fields to update

        Returns:
            Updated tool policy
        """
        response = self.client.patch(
            f"/oauth-broker/tool-policies/{policy_id}",
            json=update.model_dump(exclude_none=True),
        )
        return ToolPolicyResponse.model_validate(response)

    def delete_tool_policy(self, policy_id: UUID) -> None:
        """
        Delete a tool policy.

        Args:
            policy_id: Tool policy ID
        """
        self.client.delete(f"/oauth-broker/tool-policies/{policy_id}")
