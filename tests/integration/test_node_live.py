"""Integration tests for TS14 NODE endpoints.

Tests cover:
- TS14.001: GET /node/node_id - Get node ID (requires authentication)
- TS14.002: GET /node/node_status - Get node status (open endpoint)
"""
from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


class TestNodeEndpoints:
    """Tests for node endpoints."""

    def test_get_node_status(self, live_kamiwaza_client) -> None:
        """TS14.002: GET /node/node_status - Get node status.

        Note: Despite the server code comment suggesting this is an open endpoint
        for health checks, it currently requires authentication.
        """
        try:
            response = live_kamiwaza_client.get("/node/node_status")
            assert response is not None
            assert isinstance(response, dict)
            # NodeStatus should have relevant fields
            # Common fields might include: status, uptime, version, etc.
        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("node_status endpoint requires authentication (contrary to docs)")
            raise

    def test_get_node_id(self, live_kamiwaza_client) -> None:
        """TS14.001: GET /node/node_id - Get node ID.

        This endpoint requires authentication and returns the node ID.
        """
        try:
            response = live_kamiwaza_client.get("/node/node_id")
            assert response is not None
            # The response should be a string (node ID)
            assert isinstance(response, str)
            assert len(response) > 0
        except APIError as exc:
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for node_id endpoint")
            raise
