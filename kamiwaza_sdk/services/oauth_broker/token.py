"""Token minting and lease management mixin."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from ...schemas.oauth_broker import (
    LeaseStatusResponse,
    MintTokenRequest,
    MintTokenResponse,
)

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+\Z")


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
            >>> # Use token.access_token.get_secret_value() with provider API
        """
        # Use mode="json" so UUID/datetime fields on MintTokenRequest are
        # serialised to JSON-native types (strings) before requests.post()
        # hands the dict to json.dumps(); otherwise a raw UUID raises
        # TypeError at runtime.
        response = self.client.post(
            "/oauth-broker/tokens/mint",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        return MintTokenResponse.model_validate(response)

    def get_lease_status(self, lease_id: str) -> LeaseStatusResponse:
        """
        Get status of a token lease.

        Args:
            lease_id: Lease identifier from mint response

        Returns:
            Lease status including expiry and validity

        Raises:
            ValueError: If ``lease_id`` is empty or contains characters
                outside ``[a-zA-Z0-9._-]``.
        """
        if not lease_id or not _SAFE_ID_RE.match(lease_id) or lease_id == "..":
            raise ValueError(
                "lease_id contains characters that are not permitted "
                "(must match [a-zA-Z0-9._-]+)"
            )
        safe_lease_id = quote(lease_id, safe="")
        response = self.client.get(
            f"/oauth-broker/tokens/leases/{safe_lease_id}"
        )
        return LeaseStatusResponse.model_validate(response)

    def expire_lease(self, lease_id: str) -> None:
        """
        Expire a token lease early.

        Note: This invalidates the lease tracking but does NOT revoke
        the token with the provider. The provider token may remain valid
        until its own expiry.

        Args:
            lease_id: Lease identifier

        Raises:
            ValueError: If ``lease_id`` is empty or contains characters
                outside ``[a-zA-Z0-9._-]``.
        """
        if not lease_id or not _SAFE_ID_RE.match(lease_id) or lease_id == "..":
            raise ValueError(
                "lease_id contains characters that are not permitted "
                "(must match [a-zA-Z0-9._-]+)"
            )
        safe_lease_id = quote(lease_id, safe="")
        self.client.delete(f"/oauth-broker/tokens/leases/{safe_lease_id}")
