"""Tests for remote-dev registry resolution."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.registry_resolution import (
    _reset_docker_info_cache,
    _reset_docker_registry_config_cache,
    build_push_ref_map,
    docker_accepts_insecure_push_to,
    insecure_registry_daemon_json_fix,
    replace_registry_prefix,
    resolve_dev_registries,
    select_push_engine,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_docker_info_cache():
    """``_docker_info`` and ``_docker_registry_config`` are both memoized
    per process. Reset between cases so a prior test's mocked subprocess
    outcome doesn't leak into the next."""

    _reset_docker_info_cache()
    _reset_docker_registry_config_cache()
    yield
    _reset_docker_info_cache()
    _reset_docker_registry_config_cache()


def _conn(url: str = "https://kamiwaza.test/api"):
    return SimpleNamespace(url=url)


class TestImageRegistryResolution:
    @patch("kamiwaza_extensions.registry_resolution.detect_core_config_registry")
    def test_env_registry_wins(self, mock_core, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_REGISTRY", "env-registry:5000")

        resolution = resolve_dev_registries(
            _conn(),
            kind_registry_detector=lambda: "kind-registry:5001",
        )

        assert resolution.image_registry == "env-registry:5000"
        assert resolution.image_registry_source == "KAMIWAZA_REGISTRY"
        mock_core.assert_not_called()

    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value="127.0.0.1:30010",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=False,
    )
    def test_core_config_precedes_kind(self, _mock_vm, _mock_core):
        resolution = resolve_dev_registries(
            _conn(),
            kind_registry_detector=lambda: "localhost:5001",
        )

        assert resolution.image_registry == "127.0.0.1:30010"
        assert resolution.image_registry_source == "kamiwaza/core-config"

    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value=None,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=False,
    )
    def test_falls_back_to_kind_then_convention(self, _mock_vm, _mock_core):
        resolution = resolve_dev_registries(
            _conn(),
            kind_registry_detector=lambda: "localhost:5001",
        )
        assert resolution.image_registry == "localhost:5001"

        resolution = resolve_dev_registries(
            _conn("https://cluster.example/api"),
            kind_registry_detector=lambda: None,
        )
        assert resolution.image_registry == "registry.cluster.example"

    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value="should-not-leak:30010",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=False,
    )
    def test_core_config_skipped_for_remote_connection(self, _mock_vm, mock_core):
        # ENG-5719: for a non-local connection neither kubectl-derived local
        # lookup may be trusted -- core-config AND the Kind detector both read
        # the developer's current kube context, which may point at an unrelated
        # local cluster. Both must be skipped and the connection-derived
        # registry used instead. The Kind detector returns a value here to prove
        # it is gated (not merely returning None).
        kind_detector = MagicMock(return_value="localhost:5001")
        resolution = resolve_dev_registries(
            _conn("https://api.example.com/api"),
            kind_registry_detector=kind_detector,
        )
        assert resolution.image_registry == "registry.api.example.com"
        mock_core.assert_not_called()
        kind_detector.assert_not_called()

    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value="should-not-leak:30010",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=False,
    )
    def test_core_config_skipped_for_lan_ip_connection(self, _mock_vm, mock_core):
        # Raw IPs still disable TLS verification, but registry discovery must
        # not trust the current kube context for non-loopback LAN IPs. That
        # context may point at an unrelated local cluster.
        kind_detector = MagicMock(return_value="localhost:5001")
        resolution = resolve_dev_registries(
            _conn("https://192.168.1.50/api"),
            kind_registry_detector=kind_detector,
        )
        assert resolution.image_registry == "registry.192.168.1.50"
        mock_core.assert_not_called()
        kind_detector.assert_not_called()

    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value="127.0.0.1:30010",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=False,
    )
    def test_core_config_used_for_local_connection(self, _mock_vm, mock_core):
        # Local/dev connection (default kamiwaza.test): the core-config lookup
        # is trusted, since kubectl plausibly targets the same local cluster.
        resolution = resolve_dev_registries(
            _conn("https://kamiwaza.test/api"),
            kind_registry_detector=lambda: None,
        )
        assert resolution.image_registry == "127.0.0.1:30010"
        mock_core.assert_called_once()


class TestPushRegistryResolution:
    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value="127.0.0.1:30010",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=True,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution._docker_is_working",
        return_value=True,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value="podman-machine-default",
    )
    def test_loopback_registry_uses_docker_alias_when_docker_engine_works(
        self, _mock_podman, _mock_docker, _mock_vm, _mock_core
    ):
        # Docker is the active push engine and the daemon is working →
        # emit host.docker.internal so the daemon-in-VM resolves it to
        # the macOS/Windows host loopback. (R6 refines iter-1: alias
        # selection now keys on the actual engine that will push, not
        # just on which binaries are available.)
        resolution = resolve_dev_registries(
            _conn(), kind_registry_detector=lambda: None, push_engine="docker"
        )

        assert resolution.image_registry == "127.0.0.1:30010"
        assert resolution.push_registry == "host.docker.internal:30010"
        assert resolution.push_split is True

    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value="127.0.0.1:30010",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=True,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value="podman-machine-default",
    )
    def test_loopback_registry_uses_podman_alias_when_podman_engine_with_machine(
        self, _mock_podman, _mock_vm, _mock_core
    ):
        # R6: alias selection is engine-aware. When the engine is podman
        # AND a podman machine is running, the podman alias is what the
        # host's resolver can reach (the machine sets up /etc/hosts).
        resolution = resolve_dev_registries(
            _conn(), kind_registry_detector=lambda: None, push_engine="podman"
        )

        assert resolution.push_registry == "host.containers.internal:30010"

    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value="127.0.0.1:30010",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=True,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value=None,
    )
    def test_loopback_registry_skips_remap_for_podman_without_machine(
        self, _mock_podman, _mock_vm, _mock_core
    ):
        # R6 regression: with podman as the engine but NO podman machine
        # running, the only resolver podman has access to is the host's.
        # Neither host.docker.internal nor host.containers.internal
        # resolves there, but the original loopback (127.0.0.1) does
        # via whatever port-forwarder bound it (Docker Desktop, Lima).
        # So leave the registry alone.
        resolution = resolve_dev_registries(
            _conn(), kind_registry_detector=lambda: None, push_engine="podman"
        )

        assert resolution.push_registry == "127.0.0.1:30010"
        assert resolution.push_registry_source == "image registry"

    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value="127.0.0.1:30010",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=True,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution._docker_is_working",
        return_value=False,
    )
    def test_docker_engine_skips_remap_when_daemon_down(
        self, _mock_docker_works, _mock_vm, _mock_core
    ):
        """R6 follow-up to claude iter-3 Important: docker engine but
        daemon down → docker can't actually push regardless of alias, so
        no remap. The original loopback at least surfaces a connection
        failure rather than a misleading DNS error."""

        resolution = resolve_dev_registries(
            _conn(), kind_registry_detector=lambda: None, push_engine="docker"
        )

        assert resolution.push_registry == "127.0.0.1:30010"
        assert resolution.push_registry_source == "image registry"

    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value=None,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=True,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value=None,
    )
    def test_kind_plus_docker_desktop_plus_podman_uses_host_localhost(
        self, _mock_podman, _mock_vm, _mock_core
    ):
        """R6 regression case (user-reported, kamiwaza v0.13.1):
        kind exposes ``localhost:5001`` on the Mac host via Docker
        Desktop's port-forwarder. User has Docker Desktop running and
        podman also installed (via brew). insecure=True selects podman
        as the push engine. Previously the resolver chose
        ``host.docker.internal:5001`` (because docker daemon was
        working), but podman from host CLI cannot resolve that alias,
        so the push died at the auth-ping step with
        ``dial tcp: lookup host.docker.internal: no such host``.

        With R6, the resolver checks which engine will *actually* push
        and only remaps when the chosen engine can resolve the alias.
        Podman with no machine → no remap, leaving ``localhost:5001``
        intact (reachable from host CLI via Docker Desktop's port-
        forwarder)."""

        resolution = resolve_dev_registries(
            _conn(),
            kind_registry_detector=lambda: "localhost:5001",
            push_engine="podman",
        )

        assert resolution.image_registry == "localhost:5001"
        assert resolution.push_registry == "localhost:5001"
        assert resolution.push_split is False

    @patch(
        "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
        return_value="127.0.0.1:30010",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
        return_value=True,
    )
    def test_push_override_wins(self, _mock_vm, _mock_core, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_PUSH_REGISTRY", "push.example:5000")

        resolution = resolve_dev_registries(
            _conn(), kind_registry_detector=lambda: None
        )

        assert resolution.push_registry == "push.example:5000"
        assert resolution.push_registry_source == "KAMIWAZA_PUSH_REGISTRY"


class TestEnvRegistryNormalization:
    """``normalize_registry_env`` defends ``compose_transformer`` from
    user-supplied URLs that would otherwise produce broken image refs."""

    def test_strips_scheme_and_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_REGISTRY", "https://reg.example:5000/")
        resolution = resolve_dev_registries(
            _conn(), kind_registry_detector=lambda: None
        )
        assert resolution.image_registry == "reg.example:5000"

    def test_rejects_registry_with_path(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_REGISTRY", "reg.example:5000/extra/path")
        with pytest.raises(ValueError, match="KAMIWAZA_REGISTRY"):
            resolve_dev_registries(_conn(), kind_registry_detector=lambda: None)

    def test_rejects_empty_value_after_normalization(self, monkeypatch):
        monkeypatch.setenv("KAMIWAZA_PUSH_REGISTRY", "https:///")
        with pytest.raises(ValueError, match="KAMIWAZA_PUSH_REGISTRY"):
            resolve_dev_registries(_conn(), kind_registry_detector=lambda: None)

    @pytest.mark.parametrize(
        "bad_value,reason",
        [
            # Non-numeric port crashes urlparse(...).port deep in the stack
            # unless we validate up front (iteration 2 CL Important #1).
            ("127.0.0.1:not-a-port", "port"),
            ("reg.example:0xff", "port"),
            # Userinfo, query, fragment all silently mangle image refs
            # downstream (iteration 2 CL Important #2).
            ("user:pass@reg.example:5000", "'@'"),
            ("reg.example:5000?foo=bar", "'\\?'"),
            ("reg.example:5000#frag", "'#'"),
            # Embedded whitespace beyond what str.strip removes.
            ("reg.example\n:5000", "whitespace"),
        ],
    )
    def test_rejects_malformed_values(self, monkeypatch, bad_value, reason):
        monkeypatch.setenv("KAMIWAZA_REGISTRY", bad_value)
        with pytest.raises(ValueError, match=reason):
            resolve_dev_registries(_conn(), kind_registry_detector=lambda: None)


class TestLoopbackDetection:
    """Cover the ``ipaddress``-based detection so non-127.0.0.1 loopback
    forms route through the VM alias instead of bypassing it silently."""

    @pytest.mark.parametrize(
        "registry,expected",
        [
            ("127.0.0.1:5000", True),
            ("127.0.0.2:5000", True),  # any 127.0.0.0/8 is loopback
            ("localhost:5001", True),
            ("[::1]:5000", True),
            ("0.0.0.0:5000", False),  # routable bind-all, not loopback
            ("registry.example.com:5000", False),
            ("10.0.0.5:5000", False),
        ],
    )
    def test_is_loopback_registry(self, registry, expected):
        from kamiwaza_extensions.registry_resolution import is_loopback_registry

        assert is_loopback_registry(registry) is expected


class TestReplaceRegistryHost:
    def test_preserves_port_when_present(self):
        from kamiwaza_extensions.registry_resolution import replace_registry_host

        assert (
            replace_registry_host("127.0.0.1:30010", "host.docker.internal")
            == "host.docker.internal:30010"
        )

    def test_returns_original_when_no_explicit_port(self):
        """Without a port we don't know what to substitute — returning the
        host alone would silently flip the push from registry-port to
        443/80. Leaving the input unchanged is the safer no-op."""

        from kamiwaza_extensions.registry_resolution import replace_registry_host

        assert replace_registry_host("127.0.0.1", "host.docker.internal") == "127.0.0.1"


class TestBuildEngineVmPlatformGate:
    @patch(
        "kamiwaza_extensions.registry_resolution.platform.system",
        return_value="Linux",
    )
    @patch("kamiwaza_extensions.registry_resolution.subprocess.run")
    def test_build_engine_vm_is_false_on_native_linux(self, mock_run, _mock_system):
        from kamiwaza_extensions.registry_resolution import build_engine_runs_in_vm

        assert build_engine_runs_in_vm() is False
        # Short-circuits before invoking ``docker info``.
        mock_run.assert_not_called()

    @patch(
        "kamiwaza_extensions.registry_resolution.platform.system",
        return_value="Windows",
    )
    @patch("kamiwaza_extensions.registry_resolution.subprocess.run")
    def test_build_engine_vm_detects_windows_docker_desktop(
        self, mock_run, _mock_system
    ):
        from kamiwaza_extensions.registry_resolution import build_engine_runs_in_vm

        mock_run.return_value = MagicMock(returncode=0, stdout="linux|docker-desktop")
        assert build_engine_runs_in_vm() is True

    @patch(
        "kamiwaza_extensions.registry_resolution.platform.system",
        return_value="Windows",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.subprocess.run",
        side_effect=FileNotFoundError(),
    )
    def test_build_engine_vm_trusts_windows_even_without_docker_info(
        self, _mock_run, _mock_system
    ):
        from kamiwaza_extensions.registry_resolution import build_engine_runs_in_vm

        # Docker Desktop on Windows always virtualizes Linux even when
        # ``docker info`` errors out (context not selected yet).
        assert build_engine_runs_in_vm() is True

    @patch(
        "kamiwaza_extensions.registry_resolution.platform.system",
        return_value="Darwin",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value="podman-machine-default",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.subprocess.run",
        side_effect=FileNotFoundError(),
    )
    def test_build_engine_vm_detects_podman_only_macos(
        self, _mock_run, _mock_machine, _mock_system
    ):
        """Codex iter-2 P2: on a Podman-only macOS host (no Docker CLI),
        ``docker info`` raises FileNotFoundError but the running Podman
        machine is still a VM topology that needs the loopback remap."""

        from kamiwaza_extensions.registry_resolution import build_engine_runs_in_vm

        assert build_engine_runs_in_vm() is True

    @patch(
        "kamiwaza_extensions.registry_resolution.platform.system",
        return_value="Darwin",
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value=None,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.subprocess.run",
        side_effect=FileNotFoundError(),
    )
    def test_build_engine_vm_false_on_macos_without_engine(
        self, _mock_run, _mock_machine, _mock_system
    ):
        """Darwin without Docker AND without a running Podman machine is
        not a VM topology — no remap should occur."""

        from kamiwaza_extensions.registry_resolution import build_engine_runs_in_vm

        assert build_engine_runs_in_vm() is False


class TestSelectPushEngine:
    """``select_push_engine`` is the single source of truth for which
    binary will run the push. ``ImagePusher`` mirrors this rule inline,
    and the dev pre-flight check uses this function directly — drift
    between them is what jxstanford's Critical and High both flagged."""

    @patch("kamiwaza_extensions.registry_resolution._has_podman", return_value=True)
    def test_insecure_plus_podman_picks_podman(self, _mock):
        assert select_push_engine(insecure=True) == "podman"

    @patch("kamiwaza_extensions.registry_resolution._has_podman", return_value=False)
    def test_insecure_without_podman_picks_docker(self, _mock):
        assert select_push_engine(insecure=True) == "docker"

    @patch("kamiwaza_extensions.registry_resolution._has_podman", return_value=True)
    def test_secure_always_picks_docker(self, _mock):
        # Insecure=False means no need for --tls-verify=false; docker
        # is the universal default even when podman is installed.
        assert select_push_engine(insecure=False) == "docker"


class TestDockerInsecureRegistries:
    """jxstanford iter-4 Critical #1: detect whether docker will actually
    push insecurely to the rewritten alias before retag/push fails."""

    @patch("kamiwaza_extensions.registry_resolution.subprocess.run")
    def test_accepts_alias_listed_in_index_configs(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                '{"InsecureRegistryCIDRs":[],'
                '"IndexConfigs":{"host.docker.internal:30010":'
                '{"Name":"host.docker.internal:30010","Secure":false}}}'
            ),
        )
        assert docker_accepts_insecure_push_to("host.docker.internal:30010") is True

    @patch("kamiwaza_extensions.registry_resolution.subprocess.run")
    def test_rejects_alias_not_in_index_configs(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"InsecureRegistryCIDRs":["127.0.0.0/8"],"IndexConfigs":{}}',
        )
        assert docker_accepts_insecure_push_to("host.docker.internal:30010") is False

    @patch("kamiwaza_extensions.registry_resolution.subprocess.run")
    def test_accepts_ip_inside_insecure_cidr(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"InsecureRegistryCIDRs":["127.0.0.0/8"],"IndexConfigs":{}}',
        )
        # 127.0.0.1:30010 falls inside the default 127.0.0.0/8 CIDR.
        assert docker_accepts_insecure_push_to("127.0.0.1:30010") is True
        # 10.0.0.5:30010 does not.
        assert docker_accepts_insecure_push_to("10.0.0.5:30010") is False

    @patch(
        "kamiwaza_extensions.registry_resolution.subprocess.run",
        side_effect=FileNotFoundError(),
    )
    def test_returns_true_when_docker_absent(self, _mock):
        """If docker isn't installed it won't be the engine pushing, so
        the predicate must not gate a podman-only setup."""

        assert docker_accepts_insecure_push_to("host.docker.internal:30010") is True

    def test_daemon_json_fix_includes_registry_and_command(self):
        msg = insecure_registry_daemon_json_fix("host.docker.internal:30010")
        assert "host.docker.internal:30010" in msg
        assert "insecure-registries" in msg
        assert "daemon.json" in msg


class TestDockerInfoCache:
    @patch("kamiwaza_extensions.registry_resolution.subprocess.run")
    def test_docker_info_is_memoized_within_process(self, mock_run):
        """``_docker_info`` should run ``docker info`` at most once per
        CLI invocation to avoid the worst-case ~10s of sequential 5s
        timeouts CR iter-3 flagged in the latency suggestion."""

        from kamiwaza_extensions.registry_resolution import (
            _docker_is_working,
            build_engine_runs_in_vm,
        )

        mock_run.return_value = MagicMock(returncode=0, stdout="linux|fedora")
        # Call the two consumers; both should hit the cache after the
        # first invocation rather than spawning a new subprocess.
        with patch(
            "kamiwaza_extensions.registry_resolution.platform.system",
            return_value="Darwin",
        ):
            assert _docker_is_working() is True
            assert build_engine_runs_in_vm() is True

        assert mock_run.call_count == 1


class TestPushRefMap:
    def test_rewrites_only_refs_under_image_registry(self):
        refs = [
            "127.0.0.1:30010/my-app-api:dev1",
            "ghcr.io/example/custom-api:dev1",
        ]

        push_refs = build_push_ref_map(
            refs,
            image_registry="127.0.0.1:30010",
            push_registry="host.containers.internal:30010",
        )

        assert push_refs == {
            "127.0.0.1:30010/my-app-api:dev1": "host.containers.internal:30010/my-app-api:dev1",
        }

    def test_replace_registry_prefix_preserves_repo_tag_and_digest(self):
        assert (
            replace_registry_prefix(
                "127.0.0.1:30010/ns/app:dev@sha256:" + "a" * 64,
                old_registry="127.0.0.1:30010",
                new_registry="host.containers.internal:30010",
            )
            == "host.containers.internal:30010/ns/app:dev@sha256:" + "a" * 64
        )


class TestDetectorParsing:
    @patch(
        "kamiwaza_extensions.registry_resolution.platform.system", return_value="Darwin"
    )
    @patch("kamiwaza_extensions.registry_resolution.subprocess.run")
    def test_build_engine_vm_detects_linux_docker_server(self, mock_run, _mock_system):
        from kamiwaza_extensions.registry_resolution import build_engine_runs_in_vm

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="linux|fedora",
        )

        assert build_engine_runs_in_vm() is True

    @patch("kamiwaza_extensions.registry_resolution.subprocess.run")
    def test_running_podman_machine_name_reads_json(self, mock_run):
        from kamiwaza_extensions.registry_resolution import running_podman_machine_name

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"Name": "stopped", "Running": False},
                    {"Name": "podman-machine-default", "Running": True},
                ]
            ),
        )

        assert running_podman_machine_name() == "podman-machine-default"
