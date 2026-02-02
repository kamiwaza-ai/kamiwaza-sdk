"""Integration tests for TS13 NEWS endpoints.

Tests cover:
- TS13.001: GET /news/latest - Get latest news content
- TS13.002: GET /news/quadrants - Get structured news content in quadrant format
"""
from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


class TestNewsEndpoints:
    """Tests for news endpoints."""

    def test_get_latest_news(self, live_kamiwaza_client) -> None:
        """TS13.001: GET /news/latest - Get latest news content.

        Returns the latest news content from the Kamiwaza news API.
        This is a legacy endpoint for backward compatibility.
        """
        try:
            response = live_kamiwaza_client.get("/news/latest")
            assert response is not None
            assert isinstance(response, dict)
            # NewsContent schema has: content, error, timestamp
            assert "timestamp" in response or "content" in response or "error" in response
        except APIError as exc:
            if exc.status_code == 500:
                # News service might fail if external news API is unreachable
                pytest.skip(f"News service unavailable: {exc}")
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for news endpoint")
            raise

    def test_get_news_quadrants(self, live_kamiwaza_client) -> None:
        """TS13.002: GET /news/quadrants - Get structured news in quadrant format.

        Returns structured news content organized in quadrant format.
        """
        try:
            response = live_kamiwaza_client.get("/news/quadrants")
            assert response is not None
            assert isinstance(response, dict)
            # NewsResponse should have quadrant-structured data
        except APIError as exc:
            if exc.status_code == 500:
                # News service might fail if external news API is unreachable
                pytest.skip(f"News service unavailable: {exc}")
            if exc.status_code in (403, 401):
                pytest.skip("Insufficient permissions for news endpoint")
            raise
