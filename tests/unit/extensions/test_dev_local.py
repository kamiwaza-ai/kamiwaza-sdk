"""Tests for DevLocalRunner.

Extension detection and compose file discovery tests moved to
test_extension_detector.py (shared ExtensionDetector module).
"""

from unittest.mock import patch, MagicMock

import pytest

from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.dev_local import (
    apply_port_remaps,
    build_env_overlay,
    detect_compose_command,
    find_available_port,
    is_port_available,
    parse_port_mapping,
    resolve_port_conflicts,
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



@pytest.mark.unit
class TestParsePortMapping:
    def test_host_and_container(self):
        assert parse_port_mapping("3000:3000") == (3000, 3000)

    def test_different_ports(self):
        assert parse_port_mapping("8080:3000") == (8080, 3000)

    def test_container_only(self):
        assert parse_port_mapping("3000") == (None, None)

    def test_with_protocol(self):
        assert parse_port_mapping("8000:8000/tcp") == (8000, 8000)

    def test_with_ip_binding(self):
        assert parse_port_mapping("127.0.0.1:3000:3000") == (3000, 3000)

    def test_integer_input(self):
        assert parse_port_mapping(3000) == (None, None)

    def test_empty_string(self):
        assert parse_port_mapping("") == (None, None)


@pytest.mark.unit
class TestIsPortAvailable:
    def test_available_port(self):
        # High ephemeral port should be available
        assert is_port_available(59123) is True

    def test_occupied_port_via_bind(self):
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("0.0.0.0", 59124))
        try:
            assert is_port_available(59124) is False
        finally:
            sock.close()

    def test_occupied_port_via_listen(self):
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 59125))
        sock.listen(1)
        try:
            assert is_port_available(59125) is False
        finally:
            sock.close()


@pytest.mark.unit
class TestFindAvailablePort:
    def test_returns_start_if_available(self):
        port = find_available_port(59200)
        assert port == 59200

    def test_skips_occupied(self):
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("0.0.0.0", 59201))
        try:
            port = find_available_port(59201)
            assert port > 59201
        finally:
            sock.close()

    def test_raises_when_exhausted(self):
        with patch(
            "kamiwaza_extensions.dev_local.is_port_available", return_value=False
        ):
            with pytest.raises(RuntimeError, match="No available port"):
                find_available_port(60000, max_tries=3)


@pytest.mark.unit
class TestResolvePortConflicts:
    def test_no_conflicts(self):
        compose = {
            "services": {
                "frontend": {"ports": ["59300:3000"]},
                "backend": {"ports": ["59301:8000"]},
            }
        }
        assert resolve_port_conflicts(compose) == {}

    def test_detects_conflict(self):
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("0.0.0.0", 59302))
        try:
            compose = {
                "services": {
                    "frontend": {"ports": ["59302:3000"]},
                }
            }
            remaps = resolve_port_conflicts(compose)
            assert "frontend" in remaps
            orig, new = remaps["frontend"]
            assert orig == 59302
            assert new > 59302
        finally:
            sock.close()

    def test_empty_services(self):
        assert resolve_port_conflicts({"services": {}}) == {}

    def test_container_only_ports_ignored(self):
        compose = {"services": {"web": {"ports": ["3000"]}}}
        assert resolve_port_conflicts(compose) == {}


@pytest.mark.unit
class TestApplyPortRemaps:
    def test_remaps_host_port(self):
        compose = {"services": {"frontend": {"ports": ["3000:3000"], "build": "."}}}
        remaps = {"frontend": (3000, 3001)}
        patched = apply_port_remaps(compose, remaps)
        assert patched["services"]["frontend"]["ports"] == ["3001:3000"]
        # Original compose unchanged
        assert compose["services"]["frontend"]["ports"] == ["3000:3000"]

    def test_preserves_other_services(self):
        compose = {
            "services": {
                "frontend": {"ports": ["3000:3000"]},
                "backend": {"ports": ["8000:8000"]},
            }
        }
        remaps = {"frontend": (3000, 3001)}
        patched = apply_port_remaps(compose, remaps)
        assert patched["services"]["frontend"]["ports"] == ["3001:3000"]
        assert patched["services"]["backend"]["ports"] == ["8000:8000"]

    def test_different_host_and_container(self):
        compose = {"services": {"web": {"ports": ["8080:3000"]}}}
        remaps = {"web": (8080, 8081)}
        patched = apply_port_remaps(compose, remaps)
        assert patched["services"]["web"]["ports"] == ["8081:3000"]

    def test_preserves_non_port_config(self):
        compose = {
            "services": {
                "backend": {
                    "ports": ["8000:8000"],
                    "build": {"context": "./backend"},
                    "environment": ["FOO=bar"],
                    "volumes": ["./app:/app"],
                }
            }
        }
        remaps = {"backend": (8000, 8001)}
        patched = apply_port_remaps(compose, remaps)
        assert patched["services"]["backend"]["ports"] == ["8001:8000"]
        assert patched["services"]["backend"]["build"] == {"context": "./backend"}
        assert patched["services"]["backend"]["environment"] == ["FOO=bar"]
        assert patched["services"]["backend"]["volumes"] == ["./app:/app"]


# Compose file detection tests are in test_extension_detector.py
