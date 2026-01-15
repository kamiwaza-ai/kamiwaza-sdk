"""Integration tests for TS15 PING endpoint.

Tests cover:
- TS15.001: GET /ping - Simple liveness check
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


class TestPingEndpoint:
    """Tests for the ping endpoint."""

    def test_ping(self, live_kamiwaza_client) -> None:
        """TS15.001: GET /ping - Simple liveness check.

        Note: This endpoint does not require authentication and returns
        a simple status response for liveness testing.
        """
        response = live_kamiwaza_client.get("/ping")
        assert response is not None
        assert isinstance(response, dict)
        assert response.get("status") == "pong"
        assert "timestamp" in response
