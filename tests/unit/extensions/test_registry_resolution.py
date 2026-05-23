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
        "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
        return_value="podman-machine-default",
    )
    def test_loopback_registry_splits_for_podman_vm(
        self, _mock_podman, _mock_vm, _mock_core
    ):
        resolution = resolve_dev_registries(
            _conn(), kind_registry_detector=lambda: None
        )

        assert resolution.image_registry == "127.0.0.1:30010"
        assert resolution.push_registry == "host.containers.internal:30010"
        assert resolution.push_split is True

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
