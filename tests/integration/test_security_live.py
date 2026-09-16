"""Live coverage for public consent endpoints.

The public configuration and consent-acceptance endpoints must work before
login.
"""
from __future__ import annotations

import pytest

from kamiwaza_sdk.exceptions import APIError

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


class TestSecurityPublicConfig:
    """Tests for security public configuration endpoint."""

    def test_get_public_config(self, live_kamiwaza_client) -> None:
        """GET /security/public/config returns consent configuration."""
        try:
            response = live_kamiwaza_client.get("/security/public/config")
            assert response is not None
            assert isinstance(response, dict)
            assert "consent_enabled" in response
        except APIError as exc:
            if exc.status_code == 404:
                pytest.skip("Security service not available")
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
