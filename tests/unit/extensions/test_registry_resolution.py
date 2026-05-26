"""Tests for remote-dev registry resolution."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kamiwaza_extensions.registry_resolution import (
    build_push_ref_map,
    replace_registry_prefix,
    resolve_dev_registries,
)

pytestmark = pytest.mark.unit


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
        "kamiwaza_extensions.registry_resolution._has_docker",
        return_value=True,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value="podman-machine-default",
    )
    def test_loopback_registry_prefers_docker_alias_when_docker_present(
        self, _mock_podman, _mock_docker, _mock_vm, _mock_core
    ):
        # Default ``ImagePusher.push`` uses Docker unless ``insecure=True`` and
        # Podman is installed — so when Docker is on PATH we must emit the
        # Docker Desktop alias, even if a Podman machine happens to be
        # running. (Review iteration 1, ENG-5719.)
        resolution = resolve_dev_registries(
            _conn(), kind_registry_detector=lambda: None
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
        "kamiwaza_extensions.registry_resolution._has_docker",
        return_value=False,
    )
    @patch(
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value="podman-machine-default",
    )
    def test_loopback_registry_falls_back_to_podman_alias_without_docker(
        self, _mock_podman, _mock_docker, _mock_vm, _mock_core
    ):
        resolution = resolve_dev_registries(
            _conn(), kind_registry_detector=lambda: None
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

        assert (
            replace_registry_host("127.0.0.1", "host.docker.internal")
            == "127.0.0.1"
        )


class TestBuildEngineVmPlatformGate:
    @patch(
        "kamiwaza_extensions.registry_resolution.platform.system",
        return_value="Linux",
    )
    @patch("kamiwaza_extensions.registry_resolution.subprocess.run")
    def test_build_engine_vm_is_false_on_native_linux(
        self, mock_run, _mock_system
    ):
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
