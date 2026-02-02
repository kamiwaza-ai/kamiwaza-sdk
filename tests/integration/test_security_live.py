"""Integration tests for TS18 SECURITY endpoints.

Tests cover:
- TS18.001: POST /security/consent/accept - Record consent acceptance
- TS18.002: GET /security/embed.js - Get embeddable JavaScript bundle
- TS18.003: GET /security/public/config - Get security configuration

Note: All security endpoints are public (no authentication required)
as they need to be accessible before login.
"""
from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.withoutresponses]


class TestSecurityPublicConfig:
    """Tests for security public configuration endpoint."""

    def test_get_public_config(self, live_kamiwaza_client) -> None:
        """TS18.003: GET /security/public/config - Get security configuration.

        Returns configuration needed by the frontend to render:
        - Pre-login consent gate (if enabled)
        - Classification banners (if enabled)
        """
        try:
            response = live_kamiwaza_client.get("/security/public/config")
            assert response is not None
            assert isinstance(response, dict)
            # SecurityConfigResponse schema includes:
            # - consent_enabled: bool
            # - consent_content: optional str
            # - banner_enabled: bool
            # - banner_text: optional str
            # - banner_color: optional str
            assert "consent_enabled" in response or "banner_enabled" in response
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Security service not available")
            raise


class TestSecurityEmbedScript:
    """Tests for security embed script endpoint."""

    def test_get_embed_script(self, live_kamiwaza_client) -> None:
        """TS18.002: GET /security/embed.js - Get embeddable JavaScript bundle.

        Returns a self-contained JavaScript file that apps can include to
        automatically display classification banners and enforce consent acceptance.
        """
        try:
            # This endpoint returns JavaScript, not JSON
            response = live_kamiwaza_client.get(
                "/security/embed.js",
                expect_json=False
            )
            assert response is not None
            # Check response is successful
            assert response.status_code == 200
            # Check content type is JavaScript
            content_type = response.headers.get("content-type", "")
            assert "javascript" in content_type.lower()
            # Check response has content
            assert len(response.text) > 0
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Security embed.js not available")
            raise


class TestSecurityConsentAccept:
    """Tests for security consent acceptance endpoint."""

    def test_accept_consent(self, live_kamiwaza_client) -> None:
        """TS18.001: POST /security/consent/accept - Record consent acceptance.

        Records that a user has accepted the consent terms.
        This is logged for audit purposes with client IP and user agent.
        """
        try:
            response = live_kamiwaza_client.post("/security/consent/accept")
            assert response is not None
            assert isinstance(response, dict)
            # ConsentAcceptResponse schema includes:
            # - accepted: bool
            # - message: str
            assert "accepted" in response
            assert response.get("accepted") is True
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Security consent endpoint not available")
            raise
