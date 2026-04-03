"""Tests for DevLocalRunner helpers."""

import json
from unittest.mock import patch, MagicMock

import pytest

from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.dev_local import (
    build_compose_override,
    build_env_overlay,
    build_local_auth_env,
    detect_compose_command,
    resolve_local_auth_bridge,
)


@pytest.mark.unit
class TestEnvOverlay:
    def test_builds_correct_overlay(self):
        conn = ConnectionInfo(name="test", url="https://example.com/api", active=True, created_at=0.0)
        overlay = build_env_overlay(conn, "my-app")
        assert overlay["KAMIWAZA_API_URL"] == "https://example.com/api"
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://example.com"
        assert overlay["KAMIWAZA_USE_AUTH"] == "false"
        assert overlay["KAMIWAZA_APP_NAME"] == "my-app"

    def test_builds_auth_enabled_overlay(self):
        conn = ConnectionInfo(name="test", url="https://example.com/api", active=True, created_at=0.0)
        overlay = build_env_overlay(conn, "my-app", use_auth=True)

        assert overlay["KAMIWAZA_API_URL"] == "https://example.com/api"
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://example.com"
        assert overlay["KAMIWAZA_USE_AUTH"] == "true"
        assert overlay["KAMIWAZA_APP_NAME"] == "my-app"

    def test_overlay_without_api_in_url(self):
        conn = ConnectionInfo(name="test", url="https://example.com", active=True, created_at=0.0)
        overlay = build_env_overlay(conn, "my-app")
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://example.com"

    def test_overlay_does_not_corrupt_mid_url_api(self):
        """Regression: str.replace('/api', '') corrupted URLs with /api mid-path."""
        conn = ConnectionInfo(
            name="test", url="https://example.com/api-gateway/api", active=True, created_at=0.0
        )
        overlay = build_env_overlay(conn, "my-app")
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://example.com/api-gateway"

    def test_overlay_includes_api_key_when_provided(self):
        conn = ConnectionInfo(name="test", url="https://example.com/api", active=True, created_at=0.0)
        overlay = build_env_overlay(conn, "my-app", api_key="pat-123")
        assert overlay["KAMIWAZA_API_KEY"] == "pat-123"


@pytest.mark.unit
class TestLocalAuthBridge:
    def test_build_local_auth_env(self):
        env = build_local_auth_env(
            {
                "x-user-id": "usr-123",
                "x-user-roles": "user,editor",
            },
            "pat-123",
        )

        assert env["KAMIWAZA_API_KEY"] == "pat-123"
        assert env["KAMIWAZA_LOCAL_DEV_AUTH_BRIDGE"] == "true"
        headers = json.loads(env["KAMIWAZA_LOCAL_DEV_AUTH_HEADERS_JSON"])
        assert headers["authorization"] == "Bearer pat-123"
        assert headers["x-user-id"] == "usr-123"

    def test_build_compose_override_injects_env_into_each_service(self):
        override = build_compose_override(
            ["frontend", "backend"],
            {
                "KAMIWAZA_LOCAL_DEV_AUTH_BRIDGE": "true",
                "KAMIWAZA_API_KEY": "pat-123",
            },
        )

        assert override["services"]["frontend"]["environment"]["KAMIWAZA_API_KEY"] == "pat-123"
        assert override["services"]["backend"]["environment"]["KAMIWAZA_LOCAL_DEV_AUTH_BRIDGE"] == "true"

    def test_resolve_local_auth_bridge_uses_validate_headers(self):
        conn = ConnectionInfo(name="test", url="https://example.com/api", active=True, created_at=0.0)
        validate_response = MagicMock()
        validate_response.ok = True
        validate_response.headers = {
            "x-user-id": "usr-123",
            "x-user-name": "Alice",
            "x-user-roles": "admin,user",
            "x-auth-token": "jwt-abc",
        }

        with patch("kamiwaza_extensions.dev_local.requests.request", return_value=validate_response):
            bridge = resolve_local_auth_bridge(conn, "pat-123")

        assert bridge is not None
        assert bridge.subject == "Alice"
        assert bridge.roles == ["admin", "user"]
        assert bridge.headers["authorization"] == "Bearer pat-123"
        assert bridge.headers["x-auth-token"] == "jwt-abc"

    def test_resolve_local_auth_bridge_falls_back_to_users_me(self):
        conn = ConnectionInfo(name="test", url="https://example.com/api", active=True, created_at=0.0)
        validate_response = MagicMock()
        validate_response.ok = False

        me_response = MagicMock()
        me_response.ok = True
        me_response.json.return_value = {
            "sub": "usr-123",
            "username": "alice",
            "email": "alice@example.com",
            "roles": ["user", "editor"],
        }

        with patch(
            "kamiwaza_extensions.dev_local.requests.request",
            side_effect=[validate_response, me_response],
        ):
            bridge = resolve_local_auth_bridge(conn, "pat-123")

        assert bridge is not None
        assert bridge.subject == "alice"
        assert bridge.roles == ["user", "editor"]
        assert bridge.headers["x-user-id"] == "usr-123"
        assert bridge.headers["x-user-email"] == "alice@example.com"


@pytest.mark.unit
class TestComposeDetection:
    def test_detects_compose_v2(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = detect_compose_command()
            assert result == ["docker", "compose"]

    def test_falls_back_to_v1(self):
        def side_effect(cmd, **kwargs):
            if cmd == ["docker", "compose", "version"]:
                raise FileNotFoundError()
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=side_effect):
            result = detect_compose_command()
            assert result == ["docker-compose"]

    def test_errors_when_no_compose(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with pytest.raises(FileNotFoundError, match="Docker Compose not found"):
                detect_compose_command()



# Compose file detection tests are in test_extension_detector.py
