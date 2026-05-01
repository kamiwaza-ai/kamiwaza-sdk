"""Tests for DevLocalRunner.

Extension detection and compose file discovery tests moved to
test_extension_detector.py (shared ExtensionDetector module).
"""

from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.dev_local import (
    apply_port_remaps,
    build_compose_extra_hosts,
    build_env_overlay,
    detect_compose_command,
    find_available_port,
    is_port_available,
    parse_port_mapping,
    resolve_port_conflicts,
)
from kamiwaza_extensions_lib.local_dev import BridgeContext


@pytest.mark.unit
class TestEnvOverlay:
    def test_builds_correct_overlay(self):
        # Round-10: KAMIWAZA_PUBLIC_API_URL holds the RAW browser URL
        # (with ``/api`` intact) so ``session.create_session_router``
        # can build ``${base}/auth/login`` correctly. Previous round-2
        # behavior stripped ``/api`` here, breaking login redirects
        # under ``--auth`` (codex P2).
        conn = ConnectionInfo(
            name="test", url="https://example.com/api", active=True, created_at=0.0
        )
        overlay = build_env_overlay(conn, "my-app")
        assert overlay["KAMIWAZA_API_URL"] == "https://example.com/api"
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://example.com/api"
        assert overlay["KAMIWAZA_USE_AUTH"] == "false"
        assert overlay["KAMIWAZA_APP_NAME"] == "my-app"

    def test_overlay_without_api_in_url(self):
        conn = ConnectionInfo(
            name="test", url="https://example.com", active=True, created_at=0.0
        )
        overlay = build_env_overlay(conn, "my-app")
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://example.com"

    def test_overlay_preserves_raw_url_with_mid_path_api(self):
        """Round-10: KAMIWAZA_PUBLIC_API_URL holds the raw URL — no
        ``/api`` stripping. Round-2's ``str.replace('/api', '')``
        corruption bug is moot since we no longer strip at all here.
        Browser-display consumers (``url.public_base_url``) strip on
        demand and that helper has its own mid-path-safe regression
        coverage in ``test_url.py``."""
        conn = ConnectionInfo(
            name="test",
            url="https://example.com/api-gateway/api",
            active=True,
            created_at=0.0,
        )
        overlay = build_env_overlay(conn, "my-app")
        assert (
            overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://example.com/api-gateway/api"
        )

    def test_auth_false_preserves_current_behaviour(self):
        # TS-5 — auth=False is the existing path; no gate, no bearer, USE_AUTH=false
        conn = ConnectionInfo(
            name="test", url="https://example.com/api", active=True, created_at=0.0
        )
        overlay = build_env_overlay(conn, "my-app", auth=False)
        assert "KZ_EXT_DEV_LOCAL_AUTH" not in overlay
        assert "KAMIWAZA_BEARER_TOKEN" not in overlay
        assert overlay["KAMIWAZA_USE_AUTH"] == "false"

    def test_auth_true_with_bridge_injects_bearer_and_gate(self):
        # TS-6 — auth=True with bridge: gate, bearer, USE_AUTH=true
        conn = ConnectionInfo(
            name="test", url="https://example.com/api", active=True, created_at=0.0
        )
        bridge = BridgeContext(
            bearer_token="bearer-xyz",
            user_id="user-1",
            expires_at=None,
        )
        overlay = build_env_overlay(conn, "my-app", auth=True, bridge=bridge)
        assert overlay["KZ_EXT_DEV_LOCAL_AUTH"] == "1"
        assert overlay["KAMIWAZA_BEARER_TOKEN"] == "bearer-xyz"
        assert overlay["KAMIWAZA_USE_AUTH"] == "true"

    def test_auth_true_without_bridge_raises(self):
        # Defensive: caller must pass bridge when auth=True
        conn = ConnectionInfo(
            name="test", url="https://example.com/api", active=True, created_at=0.0
        )
        with pytest.raises(ValueError, match="bridge"):
            build_env_overlay(conn, "my-app", auth=True, bridge=None)

    def test_auth_true_rewrites_bare_loopback(self):
        # TS-7 — bare loopback URL gets rewritten to host.docker.internal
        # for the CONTAINER-side env vars only. Round-5 review Critical
        # #1: KAMIWAZA_PUBLIC_API_URL is consumed by the developer's
        # BROWSER (via /auth/login-url + /auth/logout redirects), so it
        # must keep the original loopback host or the auth flow breaks.
        conn = ConnectionInfo(
            name="local", url="http://localhost:8000/api", active=True, created_at=0.0
        )
        bridge = BridgeContext(
            bearer_token="t",
            user_id="user-1",
            expires_at=None,
        )
        overlay = build_env_overlay(conn, "my-app", auth=True, bridge=bridge)
        # Container-side: rewritten so backend can reach the host.
        assert overlay["KAMIWAZA_API_URL"] == "http://host.docker.internal:8000/api"
        assert overlay["KAMIWAZA_ENDPOINT"] == "http://host.docker.internal:8000/api/v1"
        # Browser-side: NEVER rewritten — host.docker.internal isn't
        # resolvable from the developer's browser. Round-10: keeps the
        # ``/api`` suffix so session.py's login/logout URLs are correct.
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "http://localhost:8000/api"

    def test_auth_true_preserves_named_hostname(self):
        # TS-8 — kamiwaza.test preserved (TLS cert binding)
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=False,
        )
        bridge = BridgeContext(
            bearer_token="t",
            user_id="user-1",
            expires_at=None,
        )
        overlay = build_env_overlay(conn, "my-app", auth=True, bridge=bridge)
        assert overlay["KAMIWAZA_API_URL"] == "https://kamiwaza.test/api"
        # Round-10: raw URL (with /api) so session.py auth redirects work.
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://kamiwaza.test/api"
        assert overlay["KAMIWAZA_VERIFY_SSL"] == "false"

    def test_auth_true_loopback_public_url_stays_on_original_host(self):
        # PR #87 round-5 review Critical #1 — pin KAMIWAZA_PUBLIC_API_URL
        # to the developer's original host across every loopback variant.
        # The container-side KAMIWAZA_API_URL gets rewritten to
        # host.docker.internal so the backend can reach the host; the
        # browser-side KAMIWAZA_PUBLIC_API_URL must keep the original
        # name or /auth/login-url + /auth/logout redirect to a host the
        # browser cannot resolve.
        bridge = BridgeContext(
            bearer_token="t",
            user_id="user-1",
            expires_at=None,
        )
        for original_url in [
            "http://localhost:8000/api",
            "http://127.0.0.1:8000/api",
            "https://127.0.0.2:8443/api",
            "http://[::1]:8000/api",
        ]:
            conn = ConnectionInfo(
                name="local",
                url=original_url,
                active=True,
                created_at=0.0,
            )
            overlay = build_env_overlay(
                conn,
                "my-app",
                auth=True,
                bridge=bridge,
            )
            # Container-side IS rewritten
            assert "host.docker.internal" in overlay["KAMIWAZA_API_URL"], (
                f"API URL not rewritten for {original_url!r}: "
                f"{overlay['KAMIWAZA_API_URL']}"
            )
            # Browser-side must keep the original loopback host
            assert "host.docker.internal" not in overlay["KAMIWAZA_PUBLIC_API_URL"], (
                f"PUBLIC_API_URL leaked container hostname for {original_url!r}: "
                f"{overlay['KAMIWAZA_PUBLIC_API_URL']}"
            )


@pytest.mark.unit
class TestBuildComposeExtraHosts:
    def test_named_loopback_returns_host_gateway(self):
        # TS-9
        conn = ConnectionInfo(
            name="dev", url="https://kamiwaza.test/api", active=True, created_at=0.0
        )
        # `kamiwaza.test` is detected as loopback by TLD heuristic, no DNS needed
        assert build_compose_extra_hosts(conn) == ["kamiwaza.test:host-gateway"]

    def test_bare_loopback_returns_empty_without_auth(self):
        # TS-11 — auth=False (default): named-loopback only behavior
        conn = ConnectionInfo(
            name="local", url="http://localhost:8000/api", active=True, created_at=0.0
        )
        assert build_compose_extra_hosts(conn) == []

    def test_non_loopback_returns_empty_without_auth(self, monkeypatch):
        # TS-10 — auth=False (default)
        import socket as _socket

        # Round-11 (codex GH High): resolver switched from gethostbyname
        # (IPv4-only) to getaddrinfo (dual-stack); patch the new target.
        monkeypatch.setattr(
            _socket,
            "getaddrinfo",
            lambda *a, **kw: [(0, 0, 0, "", ("1.2.3.4", 0))],
        )
        conn = ConnectionInfo(
            name="prod", url="https://api.kamiwaza.ai", active=True, created_at=0.0
        )
        assert build_compose_extra_hosts(conn) == []

    def test_auth_always_includes_host_docker_internal(self):
        # PR #87 review fix (High #2) — Linux Docker Engine doesn't
        # auto-resolve host.docker.internal; the alias must be in
        # extra_hosts. Always include it under --auth so the bare-loopback
        # URL rewrite to host.docker.internal works on Linux.
        conn = ConnectionInfo(
            name="local", url="http://localhost:8000/api", active=True, created_at=0.0
        )
        assert build_compose_extra_hosts(conn, auth=True) == [
            "host.docker.internal:host-gateway"
        ]

    def test_auth_with_named_loopback_includes_both(self):
        # Named loopback (kamiwaza.test) keeps its own alias, plus
        # host.docker.internal for Linux portability.
        conn = ConnectionInfo(
            name="dev", url="https://kamiwaza.test/api", active=True, created_at=0.0
        )
        assert build_compose_extra_hosts(conn, auth=True) == [
            "host.docker.internal:host-gateway",
            "kamiwaza.test:host-gateway",
        ]

    def test_auth_with_non_loopback_still_includes_host_docker_internal(
        self, monkeypatch
    ):
        # Even when the connection URL isn't a loopback, --auth injects
        # host.docker.internal:host-gateway. Harmless on non-loopback
        # connections and keeps the contract consistent.
        import socket as _socket

        # Round-11 (codex GH High): resolver switched from gethostbyname
        # (IPv4-only) to getaddrinfo (dual-stack); patch the new target.
        monkeypatch.setattr(
            _socket,
            "getaddrinfo",
            lambda *a, **kw: [(0, 0, 0, "", ("1.2.3.4", 0))],
        )
        conn = ConnectionInfo(
            name="prod", url="https://api.kamiwaza.ai", active=True, created_at=0.0
        )
        assert build_compose_extra_hosts(conn, auth=True) == [
            "host.docker.internal:host-gateway"
        ]


@pytest.mark.unit
class TestPublicApiUrlConsistency:
    """Round-2 review High #8 — KAMIWAZA_PUBLIC_API_URL must not produce
    double slashes from trailing-slash inputs. Round-10: assertion
    updated to the raw-URL contract — ``session.py`` builds
    ``${base}/auth/login`` and needs the ``/api`` suffix preserved."""

    def test_trailing_api_slash_does_not_double_slash(self):
        # Prior bug: url='https://kamiwaza.test/api/' produced
        # KAMIWAZA_PUBLIC_API_URL='https://kamiwaza.test/' and then
        # KAMIWAZA_ENDPOINT='https://kamiwaza.test//v1'
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api/",
            active=True,
            created_at=0.0,
        )
        overlay = build_env_overlay(conn, "my-app")
        # Round-10: raw URL kept; trailing slash stripped (so no double-slash).
        assert overlay["KAMIWAZA_PUBLIC_API_URL"] == "https://kamiwaza.test/api"

    def test_public_api_url_produces_correct_session_login_url(self):
        """PR #87 round-10 codex P2 regression — the env overlay's
        ``KAMIWAZA_PUBLIC_API_URL`` is consumed by ``session.py``'s
        ``/auth/login-url`` endpoint, which builds
        ``f"{config.public_api_url}/auth/login"`` directly. Pin the
        cross-module contract: the platform serves auth at
        ``/api/auth/*``, so the env var MUST carry the ``/api`` suffix
        intact. Stripping ``/api`` here (round-2..round-9 behavior)
        produces a 404 redirect on every login under ``--auth``.
        """
        # Walk the canonical connection-URL shapes the runner sees
        # from ``kz-ext login`` and confirm each builds a correct
        # ``/api/auth/login`` URL when concatenated by session.py.
        cases = [
            "https://kamiwaza.test/api",
            "https://kamiwaza.test/api/",  # trailing slash
            "http://localhost:8000/api",
            "https://gateway.example.com/foo/api",
        ]
        for url in cases:
            conn = ConnectionInfo(
                name="t",
                url=url,
                active=True,
                created_at=0.0,
            )
            overlay = build_env_overlay(conn, "my-app")
            base = overlay["KAMIWAZA_PUBLIC_API_URL"]
            # Replicate session.py's URL construction inline so the
            # test fails loudly if either side drifts.
            login_url = f"{base}/auth/login"
            assert login_url.endswith("/api/auth/login"), (
                f"connection {url!r} produced {login_url!r} — session.py "
                f"would 404 because the platform serves auth at /api/auth/*"
            )


@pytest.mark.unit
class TestRunnerEnvPassthroughOverlay:
    """Round-2 review Critical #1 — bridge env vars must reach the
    container, not just the compose-CLI parent process. Verifies that
    DevLocalRunner, when --auth is set, generates a compose overlay
    listing the bridge vars under every service's environment."""

    def test_runner_writes_auth_env_overlay_for_every_service(
        self, tmp_path, monkeypatch
    ):
        import yaml as _yaml

        from kamiwaza_extensions import dev_local as dev_local_mod
        from kamiwaza_extensions.dev_local import DevLocalRunner
        from kamiwaza_extensions_lib.local_dev import BridgeContext

        # Synthesize a minimal extension info object for the runner
        compose_data = {
            "services": {
                "frontend": {"build": "./frontend", "ports": ["3000"]},
                "backend": {"build": "./backend", "ports": ["8000"]},
            }
        }
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text(_yaml.dump(compose_data))

        info = MagicMock()
        info.name = "my-app"
        info.path = tmp_path
        info.compose_path = compose_path
        info.compose_data = compose_data
        info.metadata = {"type": "app"}

        # Track every overlay tempfile written by _write_compose_overlay so
        # we can read them after run() returns and the cleanup removes them.
        captured_overlays: dict[str, dict] = {}
        real_write_overlay = dev_local_mod._write_compose_overlay

        def capturing_write_overlay(*, prefix, services, per_service):
            path = real_write_overlay(
                prefix=prefix, services=services, per_service=per_service
            )
            with open(path) as fh:
                captured_overlays[prefix] = _yaml.safe_load(fh)
            return path

        monkeypatch.setattr(
            dev_local_mod, "_write_compose_overlay", capturing_write_overlay
        )

        # Stub out the parts of run() that hit the real environment
        runner = DevLocalRunner()
        runner._detector = MagicMock()
        runner._detector.detect.return_value = info
        runner._conn_mgr = MagicMock()
        runner._conn_mgr.get_active_connection.return_value = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
        )

        from kamiwaza_extensions import sdk_override as sdk_override_mod

        monkeypatch.setattr(
            dev_local_mod,
            "detect_compose_command",
            lambda: ["docker", "compose"],
        )
        monkeypatch.setattr(
            sdk_override_mod, "resolve_sdk_override", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            dev_local_mod, "resolve_port_conflicts", lambda *a, **kw: {}
        )
        monkeypatch.setattr(
            dev_local_mod,
            "prepare_bridge_context",
            lambda connection_manager: BridgeContext(
                bearer_token="bearer-xyz",
                user_id="user-42",
                expires_at=None,
            ),
        )
        # Capture the compose argv so we can assert the overlay file is
        # actually wired into `compose up` — generating the overlay but
        # not referencing it would be the same class of "silent no-op"
        # bug as round-2 Critical #1 (PR #87 round-3 review High #8).
        captured_subprocess: dict = {}

        def capture_subprocess(cmd, *, env, cwd):
            captured_subprocess["cmd"] = cmd
            captured_subprocess["env"] = env
            return 0

        runner._run_subprocess = capture_subprocess
        runner._print_urls = MagicMock()

        rc = runner.run(detach=False, auth=True)
        assert rc == 0

        # The auth-env overlay must declare bridge vars on EVERY service.
        # If this assertion ever flips, the bridge silently no-ops on
        # whatever service was missed (round-2 review Critical #1).
        env_overlay = captured_overlays.get("kz-auth-env-")
        assert env_overlay is not None, "auth-env overlay was not generated"
        services = env_overlay["services"]
        assert set(services.keys()) == {"frontend", "backend"}
        # Round-10: overlay uses mapping form ``{KEY: value}`` (not the
        # legacy list-of-strings ``["KEY=value"]``) so Docker Compose
        # skips ``$`` interpolation on bearer values that legally
        # contain ``$`` after base64url decode.
        for svc, cfg in services.items():
            env_map = cfg["environment"]
            assert isinstance(env_map, dict), (
                f"service {svc!r} environment must be a mapping (round-10 "
                f"review Comprehensive H — list form interpolates `$`)"
            )
            assert (
                env_map.get("KZ_EXT_DEV_LOCAL_AUTH") == "1"
            ), f"service {svc!r} missing KZ_EXT_DEV_LOCAL_AUTH"
            assert (
                env_map.get("KAMIWAZA_BEARER_TOKEN") == "bearer-xyz"
            ), f"service {svc!r} missing KAMIWAZA_BEARER_TOKEN"

        # Round-3 review High #8 — assert the overlay path is present in
        # the compose argv. Generating the overlay but not referencing
        # it via `-f` would silently no-op the bridge.
        cmd = captured_subprocess["cmd"]
        # Find the kz-auth-env-* tempfile that capturing_write_overlay
        # tracked and confirm it's wired in.
        auth_env_paths = [
            arg for arg in cmd if isinstance(arg, str) and "kz-auth-env-" in arg
        ]
        assert (
            auth_env_paths
        ), f"auth-env overlay not wired into compose argv. cmd={cmd!r}"
        # And the preceding `-f` flag is what makes compose actually pick
        # the overlay up — assert the flag-arg pairing is correct.
        idx = cmd.index(auth_env_paths[0])
        assert cmd[idx - 1] == "-f", (
            f"auth-env overlay path {auth_env_paths[0]!r} is in cmd but "
            f"not preceded by `-f` — compose will ignore it. cmd={cmd!r}"
        )

    def test_runner_does_not_write_auth_env_overlay_without_auth_flag(
        self, tmp_path, monkeypatch
    ):
        import yaml as _yaml

        from kamiwaza_extensions import dev_local as dev_local_mod
        from kamiwaza_extensions.dev_local import DevLocalRunner

        compose_data = {"services": {"frontend": {"build": "./frontend"}}}
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text(_yaml.dump(compose_data))

        info = MagicMock()
        info.name = "my-app"
        info.path = tmp_path
        info.compose_path = compose_path
        info.compose_data = compose_data
        info.metadata = {"type": "app"}

        captured_overlays: dict[str, dict] = {}
        real_write_overlay = dev_local_mod._write_compose_overlay

        def capturing_write_overlay(*, prefix, services, per_service):
            path = real_write_overlay(
                prefix=prefix, services=services, per_service=per_service
            )
            with open(path) as fh:
                captured_overlays[prefix] = _yaml.safe_load(fh)
            return path

        monkeypatch.setattr(
            dev_local_mod, "_write_compose_overlay", capturing_write_overlay
        )

        runner = DevLocalRunner()
        runner._detector = MagicMock()
        runner._detector.detect.return_value = info
        runner._conn_mgr = MagicMock()
        runner._conn_mgr.get_active_connection.return_value = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
        )

        from kamiwaza_extensions import sdk_override as sdk_override_mod

        monkeypatch.setattr(
            dev_local_mod,
            "detect_compose_command",
            lambda: ["docker", "compose"],
        )
        monkeypatch.setattr(
            sdk_override_mod, "resolve_sdk_override", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            dev_local_mod, "resolve_port_conflicts", lambda *a, **kw: {}
        )

        runner._run_subprocess = MagicMock(return_value=0)
        runner._print_urls = MagicMock()

        rc = runner.run(detach=False, auth=False)
        assert rc == 0

        # No bridge-env overlay should be generated under auth=False
        assert "kz-auth-env-" not in captured_overlays

    def test_runner_strips_pre_existing_bridge_env_when_auth_false(
        self, tmp_path, monkeypatch
    ):
        """High #3 — defense in depth: when --auth is NOT set, the runner
        scrubs any pre-existing bridge env vars from os.environ.copy() so
        a stale shell export can't leak into the container."""
        import yaml as _yaml

        from kamiwaza_extensions import dev_local as dev_local_mod
        from kamiwaza_extensions.dev_local import DevLocalRunner

        # Simulate the developer having a stale bridge env in their shell
        monkeypatch.setenv("KZ_EXT_DEV_LOCAL_AUTH", "1")
        monkeypatch.setenv("KAMIWAZA_BEARER_TOKEN", "stale-token-from-shell")
        monkeypatch.setenv("KAMIWAZA_DEV_WORKROOM_ID", "stale-workroom")

        compose_data = {"services": {"frontend": {"build": "./frontend"}}}
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text(_yaml.dump(compose_data))

        info = MagicMock()
        info.name = "my-app"
        info.path = tmp_path
        info.compose_path = compose_path
        info.compose_data = compose_data
        info.metadata = {"type": "app"}

        runner = DevLocalRunner()
        runner._detector = MagicMock()
        runner._detector.detect.return_value = info
        runner._conn_mgr = MagicMock()
        runner._conn_mgr.get_active_connection.return_value = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
        )

        from kamiwaza_extensions import sdk_override as sdk_override_mod

        monkeypatch.setattr(
            dev_local_mod,
            "detect_compose_command",
            lambda: ["docker", "compose"],
        )
        monkeypatch.setattr(
            sdk_override_mod, "resolve_sdk_override", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            dev_local_mod, "resolve_port_conflicts", lambda *a, **kw: {}
        )

        captured_env: dict = {}

        def capture_subprocess(cmd, *, env, cwd):
            captured_env.update(env)
            return 0

        runner._run_subprocess = capture_subprocess
        runner._print_urls = MagicMock()

        rc = runner.run(detach=False, auth=False)
        assert rc == 0

        # Bridge env vars must NOT appear in the env passed to compose.
        assert "KZ_EXT_DEV_LOCAL_AUTH" not in captured_env
        assert "KAMIWAZA_BEARER_TOKEN" not in captured_env
        assert "KAMIWAZA_DEV_WORKROOM_ID" not in captured_env


@pytest.mark.unit
class TestRunnerAuthExtensionTypeGate:
    """PR #87 round-5 review High #2 — `--auth` is only meaningful for
    `app`-type extensions (the bridge mechanism is the Next.js
    middleware shipped with the app template). For `service` and `tool`
    extensions there's no equivalent bridge, so the runner must refuse
    rather than set USE_AUTH=true and silently 401 every request."""

    @pytest.mark.parametrize(
        "metadata,expected_type_in_message",
        [
            ({"type": "service"}, "service"),
            ({"type": "tool"}, "tool"),
            # PR #87 round-6 review (claude) — legacy extensions use
            # `template_type` instead of `type`. The shared
            # ``infer_extension_type`` helper honours the legacy field
            # so non-app extensions don't silently slip past this gate.
            ({"template_type": "service"}, "service"),
            ({"template_type": "tool"}, "tool"),
            # Name-prefix heuristic catches extensions whose metadata
            # has neither field set (also via infer_extension_type).
            ({"name": "tool-icism-parser"}, "tool"),
            ({"name": "service-milvus"}, "service"),
        ],
    )
    def test_runner_rejects_auth_for_non_app_extension_types(
        self, tmp_path, monkeypatch, metadata, expected_type_in_message
    ):
        import yaml as _yaml

        from kamiwaza_extensions import dev_local as dev_local_mod
        from kamiwaza_extensions.dev_local import DevLocalRunner
        from kamiwaza_extensions_lib.local_dev import LocalDevAuthError

        compose_data = {"services": {"server": {"build": "."}}}
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text(_yaml.dump(compose_data))

        info = MagicMock()
        info.name = metadata.get("name", f"my-{expected_type_in_message}")
        info.path = tmp_path
        info.compose_path = compose_path
        info.compose_data = compose_data
        info.metadata = metadata

        runner = DevLocalRunner()
        runner._detector = MagicMock()
        runner._detector.detect.return_value = info
        runner._conn_mgr = MagicMock()

        monkeypatch.setattr(
            dev_local_mod,
            "detect_compose_command",
            lambda: ["docker", "compose"],
        )

        # prepare_bridge_context should NEVER get called — the type gate
        # is supposed to fire first.
        prepare_called = []

        def fail_if_called(*args, **kwargs):
            prepare_called.append(True)
            raise AssertionError("prepare_bridge_context should not run")

        monkeypatch.setattr(dev_local_mod, "prepare_bridge_context", fail_if_called)

        with pytest.raises(LocalDevAuthError, match="only supported for `app`"):
            runner.run(detach=False, auth=True)
        assert not prepare_called

    def test_runner_accepts_auth_for_app_extension_type(self, tmp_path, monkeypatch):
        """Sanity check: app-type passes the gate (bridge prep is then
        invoked normally)."""
        import yaml as _yaml

        from kamiwaza_extensions import dev_local as dev_local_mod
        from kamiwaza_extensions.dev_local import DevLocalRunner
        from kamiwaza_extensions_lib.local_dev import BridgeContext

        compose_data = {"services": {"frontend": {"build": "./frontend"}}}
        compose_path = tmp_path / "docker-compose.yml"
        compose_path.write_text(_yaml.dump(compose_data))

        info = MagicMock()
        info.name = "my-app"
        info.path = tmp_path
        info.compose_path = compose_path
        info.compose_data = compose_data
        info.metadata = {"type": "app"}

        runner = DevLocalRunner()
        runner._detector = MagicMock()
        runner._detector.detect.return_value = info
        runner._conn_mgr = MagicMock()
        runner._conn_mgr.get_active_connection.return_value = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
        )

        from kamiwaza_extensions import sdk_override as sdk_override_mod

        monkeypatch.setattr(
            dev_local_mod,
            "detect_compose_command",
            lambda: ["docker", "compose"],
        )
        monkeypatch.setattr(
            sdk_override_mod, "resolve_sdk_override", lambda *a, **kw: None
        )
        monkeypatch.setattr(
            dev_local_mod, "resolve_port_conflicts", lambda *a, **kw: {}
        )
        monkeypatch.setattr(
            dev_local_mod,
            "prepare_bridge_context",
            lambda connection_manager: BridgeContext(
                bearer_token="b",
                user_id="u",
                expires_at=None,
            ),
        )
        runner._run_subprocess = MagicMock(return_value=0)
        runner._print_urls = MagicMock()

        rc = runner.run(detach=False, auth=True)
        assert rc == 0


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

    def test_occupied_port_via_ipv6_listener(self):
        """PR #87 round-10 H6 + round-11 review (Comprehensive H + Claude H)
        — an IPv6-only listener bound to ``::1`` (e.g. a local proxy on a
        Linux ``net.ipv6.bindv6only=1`` host, or a kubernetes-style
        sidecar) must be detected so ``compose up`` doesn't bind-fail.
        Round-10 added the connect probe; this test pins it.
        """
        import socket

        if not socket.has_ipv6:
            pytest.skip("IPv6 not available on this host")

        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            # IPV6_V6ONLY=1 simulates the bindv6only kernel default that
            # makes the round-10 fix actually matter — without it the
            # IPv4 probe would catch the listener via the v4-mapped path.
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except (AttributeError, OSError):
            pass
        try:
            sock.bind(("::1", 59126))
            sock.listen(1)
            assert is_port_available(59126) is False
        finally:
            sock.close()

    def test_v4_free_but_v6_bound_caught_by_bind_check(self):
        """PR #87 round-11 review (Comprehensive M2) — the round-10
        connect probe checked both stacks but the bind check was IPv4
        only, so a port that's v4-free but v6-occupied could pass
        ``is_port_available`` and still fail at ``compose up`` bind
        time. Round-11 added a v6 bind probe to close the asymmetry.

        Bind a v6 socket without ``listen()``: the connect probe sees
        ECONNREFUSED (no listener) and falls through to the bind check,
        where v4 succeeds and v6 raises EADDRINUSE — which is the
        exact "v4-free but v6-bound" race we're locking in.
        """
        import socket

        if not socket.has_ipv6:
            pytest.skip("IPv6 not available on this host")

        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except (AttributeError, OSError):
            pass
        try:
            sock.bind(("::1", 59127))
            # No listen() — connect probe will get ECONNREFUSED.
            assert is_port_available(59127) is False
        finally:
            sock.close()

    def test_v6_disabled_runtime_does_not_falsely_report_port_taken(self, monkeypatch):
        """PR #87 round-12 review (codex M1) — ``socket.has_ipv6`` is
        a build-time flag, not a runtime capability. On Linux hosts
        with ``net.ipv6.conf.all.disable_ipv6=1`` (hardened servers,
        some CI runners), v6 socket creation raises ``EAFNOSUPPORT``.
        Round-12 added the errno guard so the v4 bind verdict is
        authoritative when v6 is unusable; this test pins the fix by
        making any AF_INET6 socket creation raise.
        """
        import errno as _errno
        import socket as _socket

        original_socket = _socket.socket

        def fake_socket(family, *args, **kwargs):
            if family == _socket.AF_INET6:
                # Same outcome as ``net.ipv6.conf.all.disable_ipv6=1``:
                # the kernel refuses to create a v6 socket at all. The
                # function's connect/bind probes both raise OSError on
                # socket creation and fall through.
                raise OSError(_errno.EAFNOSUPPORT, "v6 disabled")
            return original_socket(family, *args, **kwargs)

        monkeypatch.setattr(_socket, "socket", fake_socket)
        # Port should still report available — v4 bind succeeded and
        # the v6 ``socket()`` call raised EAFNOSUPPORT.
        assert is_port_available(59128) is True


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
class TestHasBarePorts:
    """Foreground-mode URL polling (ENG-3901 / F-008) only fires when
    there's at least one bare-port spec to resolve. Mapped specs already
    have a known host port, and services without ports don't need URLs."""

    def _runner(self):
        from kamiwaza_extensions.dev_local import DevLocalRunner

        return DevLocalRunner.__new__(DevLocalRunner)

    def test_returns_true_when_a_service_has_a_bare_port(self):
        runner = self._runner()
        compose = {"services": {"frontend": {"ports": ["3000"]}}}
        assert runner._has_bare_ports(compose) is True

    def test_returns_false_when_all_ports_are_mapped(self):
        runner = self._runner()
        compose = {"services": {"frontend": {"ports": ["3000:3000"]}}}
        assert runner._has_bare_ports(compose) is False

    def test_returns_false_when_no_services_have_ports(self):
        runner = self._runner()
        compose = {"services": {"backend": {"image": "redis:7"}}}
        assert runner._has_bare_ports(compose) is False

    def test_returns_false_for_empty_compose(self):
        runner = self._runner()
        assert runner._has_bare_ports(None) is False
        assert runner._has_bare_ports({}) is False
        assert runner._has_bare_ports({"services": {}}) is False

    def test_returns_true_when_one_service_is_mapped_and_another_is_bare(self):
        runner = self._runner()
        compose = {
            "services": {
                "backend": {"ports": ["8000:8000"]},
                "frontend": {"ports": ["3000"]},
            }
        }
        assert runner._has_bare_ports(compose) is True


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
            staticmethod(
                lambda svc, port, compose_cmd=None, cwd=None: called.__setitem__(
                    "docker_compose_port", True
                )
                or None
            ),
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
            staticmethod(
                lambda svc, port, compose_cmd=None, cwd=None: (
                    49152 if svc == "frontend" and port == 3000 else None
                )
            ),
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
            staticmethod(lambda svc, port, compose_cmd=None, cwd=None: None),
        )

        runner._print_urls(compose, {}, post_up=True)

        out = buf.getvalue()
        assert "http://localhost" not in out


@pytest.mark.unit
class TestDockerComposePortV1Compat:
    """Review re-review PR #84 M1: the bare-port lookup must use the same
    compose binary that ``detect_compose_command()`` returned (`docker
    compose` v2 or `docker-compose` v1) — hard-coding v2 silently broke
    URL discovery on v1-only hosts."""

    def test_uses_supplied_compose_cmd_v1(self, monkeypatch):
        from kamiwaza_extensions.dev_local import DevLocalRunner

        captured: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            captured.append(list(cmd))
            return MagicMock(returncode=0, stdout="0.0.0.0:49152\n", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)
        host = DevLocalRunner._docker_compose_port(
            "frontend",
            3000,
            compose_cmd=["docker-compose"],
        )
        assert host == 49152
        assert captured == [["docker-compose", "port", "frontend", "3000"]]

    def test_uses_supplied_compose_cmd_v2(self, monkeypatch):
        from kamiwaza_extensions.dev_local import DevLocalRunner

        captured: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            captured.append(list(cmd))
            return MagicMock(returncode=0, stdout="0.0.0.0:49152\n", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)
        host = DevLocalRunner._docker_compose_port(
            "frontend",
            3000,
            compose_cmd=["docker", "compose"],
        )
        assert host == 49152
        assert captured == [["docker", "compose", "port", "frontend", "3000"]]

    def test_falls_back_to_detect_when_compose_cmd_omitted(self, monkeypatch):
        # Backwards-compatible default: detect at lookup time.
        from kamiwaza_extensions.dev_local import DevLocalRunner

        captured: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            captured.append(list(cmd))
            # First call is the detection probe; second is the port query.
            if cmd[:3] == ["docker", "compose", "version"]:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="0.0.0.0:49152\n", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)
        host = DevLocalRunner._docker_compose_port("frontend", 3000)
        assert host == 49152
        # The actual port query used the detected v2 binary.
        port_calls = [c for c in captured if "port" in c]
        assert port_calls and port_calls[0][:2] == ["docker", "compose"]

    def test_passes_through_project_args_and_cwd(self, monkeypatch):
        # Review re-review PR #84 M1: when the user invokes from a
        # parent directory or with override files, the post-up port
        # query must target the SAME compose project that `compose up`
        # started — same `-f`/`--project-directory` args and same cwd.
        from kamiwaza_extensions.dev_local import DevLocalRunner

        captured: dict[str, object] = {}

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = list(cmd)
            captured["cwd"] = kwargs.get("cwd")
            return MagicMock(returncode=0, stdout="0.0.0.0:49152\n", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)

        compose_prefix = [
            "docker",
            "compose",
            "-f",
            "/tmp/kz-ports-abc.yml",
            "-f",
            "/tmp/kz-sdk-xyz.yml",
            "--project-directory",
            "/Users/dev/my-app",
        ]
        host = DevLocalRunner._docker_compose_port(
            "frontend",
            3000,
            compose_cmd=compose_prefix,
            cwd="/Users/dev/my-app",
        )
        assert host == 49152
        # Project-identifier args + `port` + service + container port —
        # matches the same project that `compose up` was invoked against.
        assert captured["cmd"] == [
            *compose_prefix,
            "port",
            "frontend",
            "3000",
        ]
        assert captured["cwd"] == "/Users/dev/my-app"
