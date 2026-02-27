"""Token minting and lease management mixin."""

from __future__ import annotations

from typing import Any

from ...schemas.oauth_broker import (
    LeaseStatusResponse,
    MintTokenRequest,
    MintTokenResponse,
)


class TokenMixin:
    """Mixin for ephemeral token minting and lease management."""

    client: Any  # Provided by BaseService when mixed in

    def mint_ephemeral_token(
        self, request: MintTokenRequest
    ) -> MintTokenResponse:
        """
        Mint an ephemeral access token (Mode 2 - Advanced).

        **WARNING**: Only use in high-security environments with restricted network egress.
        For most use cases, use Proxy Mode (Mode 1) instead.

        Args:
            request: Token mint request

        Returns:
            Ephemeral access token with lease tracking

        Example:
            >>> request = MintTokenRequest(
            ...     app_installation_id=app_id,
            ...     tool_id="gmail-reader",
            ...     provider="google",
            ...     lease_duration=300
            ... )
            >>> token = client.oauth_broker.mint_ephemeral_token(request)
            >>> # Use token.access_token with provider API
        """
        response = self.client.post(
            "/oauth-broker/tokens/mint", json=request.model_dump(exclude_none=True)
        )
        return MintTokenResponse.model_validate(response)

    def get_lease_status(self, lease_id: str) -> LeaseStatusResponse:
        """
        Get status of a token lease.

        Args:
            lease_id: Lease identifier from mint response

        Returns:
            Lease status including expiry and validity
        """
        response = self.client.get(f"/oauth-broker/tokens/leases/{lease_id}")
        return LeaseStatusResponse.model_validate(response)

    def revoke_lease(self, lease_id: str) -> None:
        """
        Revoke a token lease early.

        Note: This invalidates the lease tracking but does NOT revoke
        the token with the provider.

        Args:
            lease_id: Lease identifier
        """
        self.client.delete(f"/oauth-broker/tokens/leases/{lease_id}")
