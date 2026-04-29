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
        # Bare container-port spec — host port is auto-assigned by Docker.
        # (ENG-3889 P2: scaffolded compose now uses bare specs to avoid
        # host-port collisions with the kind-cluster control plane.)
        assert parse_port_mapping("3000") == (None, 3000)

    def test_with_protocol(self):
        assert parse_port_mapping("8000:8000/tcp") == (8000, 8000)

    def test_with_ip_binding(self):
        assert parse_port_mapping("127.0.0.1:3000:3000") == (3000, 3000)

    def test_integer_input(self):
        assert parse_port_mapping(3000) == (None, 3000)

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


@pytest.mark.unit
class TestPrintUrlsBarePort:
    """ENG-3889 P2 + review PR #84 Critical #3 — bare-port URL discovery
    must work in both pre-up and post-up modes. The pre-up mode runs
    *before* `compose up` is launched (Docker hasn't assigned a host port
    yet) so it must emit a hint. The post-up mode runs after `compose up
    -d` returns and queries Docker for the actual port."""

    def _runner(self):
        from kamiwaza_extensions.dev_local import DevLocalRunner

        return DevLocalRunner.__new__(DevLocalRunner)  # bypass __init__

    def _capture_stderr(self, monkeypatch):
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        captured = Console(file=buf, force_terminal=False, no_color=True, width=120)
        # The module-level `console` writes to stderr — replace it.
        monkeypatch.setattr("kamiwaza_extensions.dev_local.console", captured)
        return buf

    def test_pre_up_bare_port_emits_hint_not_localhost_url(self, monkeypatch):
        runner = self._runner()
        compose = {"services": {"frontend": {"ports": ["3000"]}}}
        buf = self._capture_stderr(monkeypatch)

        # `_docker_compose_port` MUST NOT be called pre-up — Docker hasn't
        # assigned a host port yet, so a query would either return nothing
        # or hang.
        called = {"docker_compose_port": False}
        monkeypatch.setattr(
            "kamiwaza_extensions.dev_local.DevLocalRunner._docker_compose_port",
            staticmethod(lambda svc, port: called.__setitem__("docker_compose_port", True) or None),
        )

        runner._print_urls(compose, {}, post_up=False)

        out = buf.getvalue()
        assert called["docker_compose_port"] is False
        assert "container port 3000" in out
        assert "host port assigned by Docker" in out
        # No localhost URL printed — we don't know the port yet.
        assert "http://localhost" not in out

    def test_post_up_bare_port_queries_docker_and_prints_url(self, monkeypatch):
        runner = self._runner()
        compose = {"services": {"frontend": {"ports": ["3000"]}}}
        buf = self._capture_stderr(monkeypatch)

        # Simulate Docker having assigned host port 49152 to container port 3000.
        monkeypatch.setattr(
            "kamiwaza_extensions.dev_local.DevLocalRunner._docker_compose_port",
            staticmethod(lambda svc, port: 49152 if svc == "frontend" and port == 3000 else None),
        )

        runner._print_urls(compose, {}, post_up=True)

        out = buf.getvalue()
        assert "http://localhost:49152" in out

    def test_pre_up_mapped_port_still_prints_localhost_url(self, monkeypatch):
        # Mapped specs (`"3000:3000"`) work pre-up — host port is fixed in
        # the compose file. This is the original v1 behaviour and must not
        # regress under the pre/post-up split.
        runner = self._runner()
        compose = {"services": {"frontend": {"ports": ["3000:3000"]}}}
        buf = self._capture_stderr(monkeypatch)

        runner._print_urls(compose, {}, post_up=False)

        out = buf.getvalue()
        assert "http://localhost:3000" in out

    def test_post_up_bare_port_falls_silent_when_docker_unavailable(self, monkeypatch):
        # If `docker compose port` returns nothing (e.g. compose still
        # starting, docker daemon hiccup), don't crash — just print nothing
        # for that service.
        runner = self._runner()
        compose = {"services": {"frontend": {"ports": ["3000"]}}}
        buf = self._capture_stderr(monkeypatch)

        monkeypatch.setattr(
            "kamiwaza_extensions.dev_local.DevLocalRunner._docker_compose_port",
            staticmethod(lambda svc, port: None),
        )

        runner._print_urls(compose, {}, post_up=True)

        out = buf.getvalue()
        assert "http://localhost" not in out
