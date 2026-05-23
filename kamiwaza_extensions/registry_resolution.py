"""Registry resolution helpers shared by ``kz-ext dev`` and doctor."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

CORE_CONFIG_NAMESPACE = "kamiwaza"
CORE_CONFIG_NAME = "core-config"
REGISTRY_EXTERNAL_HOST_KEY = "KAMIWAZA_REGISTRY_EXTERNAL_HOST"


@dataclass(frozen=True)
class RegistryResolution:
    """Resolved image and push registries for a remote dev run."""

    image_registry: str
    image_registry_source: str
    push_registry: str
    push_registry_source: str

    @property
    def push_split(self) -> bool:
        return self.push_registry != self.image_registry


def resolve_dev_registries(
    connection,
    *,
    kind_registry_detector: Optional[Callable[[], Optional[str]]] = None,
) -> RegistryResolution:
    """Resolve the deployment image registry and build-engine push registry.

    ``image_registry`` is embedded in the Kubernetes payload. ``push_registry``
    is where the local build engine pushes the same repository/tag. They are
    usually identical, except for macOS nested-VM topologies where loopback
    means different things from the build VM and from the k0s node.
    """

    image_registry, image_source = resolve_image_registry(
        connection,
        kind_registry_detector=kind_registry_detector,
    )
    push_registry, push_source = resolve_push_registry(image_registry)
    return RegistryResolution(
        image_registry=image_registry,
        image_registry_source=image_source,
        push_registry=push_registry,
        push_registry_source=push_source,
    )


def resolve_image_registry(
    connection,
    *,
    kind_registry_detector: Optional[Callable[[], Optional[str]]] = None,
) -> tuple[str, str]:
    """Resolve the registry host used in deployment image references."""

    env_registry = os.environ.get("KAMIWAZA_REGISTRY")
    if env_registry:
        return env_registry.strip(), "KAMIWAZA_REGISTRY"

    core_config_registry = detect_core_config_registry()
    if core_config_registry:
        return core_config_registry, f"{CORE_CONFIG_NAMESPACE}/{CORE_CONFIG_NAME}"

    if kind_registry_detector is None:
        kind_registry_detector = detect_kind_registry
    kind_registry = kind_registry_detector()
    if kind_registry:
        return kind_registry, "kind local-registry-hosting"

    cluster_url = connection.url.removesuffix("/api")
    parsed = urlparse(cluster_url)
    if parsed.hostname:
        return f"registry.{parsed.hostname}", "registry.<connection-hostname>"
    raise ValueError("Could not derive registry from connection URL")


def resolve_push_registry(image_registry: str) -> tuple[str, str]:
    """Resolve the registry host reachable from the active build/push engine."""

    push_override = os.environ.get("KAMIWAZA_PUSH_REGISTRY")
    if push_override:
        return push_override.strip(), "KAMIWAZA_PUSH_REGISTRY"

    if not is_loopback_registry(image_registry):
        return image_registry, "image registry"

    if not build_engine_runs_in_vm():
        return image_registry, "image registry"

    host = "host.docker.internal"
    if running_podman_machine_name() is not None:
        host = "host.containers.internal"
    return replace_registry_host(image_registry, host), "build VM loopback alias"


def detect_core_config_registry() -> Optional[str]:
    """Best-effort read of the platform-advertised registry host."""

    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "configmap",
                CORE_CONFIG_NAME,
                "-n",
                CORE_CONFIG_NAMESPACE,
                "-o",
                f"jsonpath={{.data.{REGISTRY_EXTERNAL_HOST_KEY}}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def detect_kind_registry() -> Optional[str]:
    """Auto-detect a Kind local registry from the kube-public configmap."""

    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "configmap",
                "local-registry-hosting",
                "-n",
                "kube-public",
                "-o",
                "jsonpath={.data.localRegistryHosting\\.v1}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None

    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("host:"):
            host_val = line.split(":", 1)[1].strip().strip('"').strip("'")
            parsed = urlparse(f"//{host_val}")
            port = parsed.port or 5001
            return f"localhost:{port}"
    return None


def build_push_ref_map(
    image_refs: Iterable[str],
    *,
    image_registry: str,
    push_registry: str,
) -> dict[str, str]:
    """Return refs that need retagging before push, keyed by image ref."""

    if image_registry == push_registry:
        return {}
    out: dict[str, str] = {}
    for image_ref in image_refs:
        push_ref = replace_registry_prefix(
            image_ref,
            old_registry=image_registry,
            new_registry=push_registry,
        )
        if push_ref != image_ref:
            out[image_ref] = push_ref
    return out


def replace_registry_prefix(
    image_ref: str,
    *,
    old_registry: str,
    new_registry: str,
) -> str:
    """Replace ``old_registry`` at the start of an image ref, if present."""

    old = old_registry.rstrip("/")
    new = new_registry.rstrip("/")
    if image_ref == old:
        return new
    prefix = f"{old}/"
    if image_ref.startswith(prefix):
        return f"{new}/{image_ref[len(prefix):]}"
    return image_ref


def is_loopback_registry(registry: str) -> bool:
    parsed = urlparse(f"//{registry}")
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def replace_registry_host(registry: str, host: str) -> str:
    parsed = urlparse(f"//{registry}")
    if parsed.port is None:
        return host
    return f"{host}:{parsed.port}"


def build_engine_runs_in_vm() -> bool:
    """Return True when the local Docker endpoint appears to be a VM."""

    if platform.system() != "Darwin":
        return False
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}|{{.OperatingSystem}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return "linux" in result.stdout.lower()


def running_podman_machine_name() -> Optional[str]:
    """Return the first running Podman machine name, if discoverable."""

    try:
        result = subprocess.run(
            ["podman", "machine", "list", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        machines = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(machines, list):
        return None
    for machine in machines:
        if isinstance(machine, dict) and machine.get("Running"):
            name = machine.get("Name")
            return name if isinstance(name, str) and name else None
    return None
