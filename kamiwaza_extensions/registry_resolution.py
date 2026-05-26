"""Registry resolution helpers shared by ``kz-ext dev`` and doctor."""

from __future__ import annotations

import ipaddress
import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

CORE_CONFIG_NAMESPACE = "kamiwaza"
CORE_CONFIG_NAME = "core-config"
REGISTRY_EXTERNAL_HOST_KEY = "KAMIWAZA_REGISTRY_EXTERNAL_HOST"

# VM aliases used by Docker Desktop and Podman machine respectively to
# expose the host's loopback interface to processes running in the VM.
# ``host.docker.internal`` is the canonical Docker Desktop alias on macOS
# and Windows; recent Podman releases also accept it, but emit
# ``host.containers.internal`` by default. We prefer the docker alias
# whenever Docker is the active build engine (the default in
# ``ImagePusher.push``), and only fall back to the podman alias when
# Docker is absent and a Podman machine is running.
DOCKER_VM_HOST_ALIAS = "host.docker.internal"
PODMAN_VM_HOST_ALIAS = "host.containers.internal"


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
        return normalize_registry_env("KAMIWAZA_REGISTRY", env_registry), "KAMIWAZA_REGISTRY"

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
        return (
            normalize_registry_env("KAMIWAZA_PUSH_REGISTRY", push_override),
            "KAMIWAZA_PUSH_REGISTRY",
        )

    if not is_loopback_registry(image_registry):
        return image_registry, "image registry"

    if not build_engine_runs_in_vm():
        return image_registry, "image registry"

    # Prefer the Docker Desktop alias whenever Docker is the *working*
    # engine, since ``ImagePusher.push`` only falls back to Podman when
    # ``insecure=True`` and Podman is installed. Pick the Podman alias
    # whenever Docker isn't actually usable (CLI absent OR daemon down)
    # and a Podman machine is running — otherwise doctor output would
    # claim ``host.docker.internal`` while the push actually goes through
    # ``podman``. (Review iteration 3, ENG-5719.)
    host = DOCKER_VM_HOST_ALIAS
    if not _docker_is_working() and running_podman_machine_name() is not None:
        host = PODMAN_VM_HOST_ALIAS
    return replace_registry_host(image_registry, host), "build VM loopback alias"


def normalize_registry_env(var_name: str, raw: str) -> str:
    """Strip a user-supplied registry env var to a bare ``host[:port]`` form.

    Tolerates pasted URLs (``https://reg:5000/``) but raises ``ValueError``
    for values that still don't look like a registry reference after the
    obvious cleanup, so the broken value never reaches ``compose_transformer``
    and silently produces malformed image refs like ``https://reg:5000/foo``.

    Rejects userinfo (``user:pass@reg``), query strings, fragments, and any
    embedded whitespace — including ``\\n``/``\\t`` that ``str.strip`` leaves
    in the interior of the value. Also rejects non-numeric port suffixes
    such as ``127.0.0.1:not-a-port`` that would otherwise raise an
    uncaught ``ValueError`` from ``urlparse().port`` deeper in the stack.
    """

    value = raw.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.rstrip("/")

    def _fail(reason: str) -> None:
        raise ValueError(
            f"{var_name}={raw!r} is not a valid registry ({reason}); "
            "expected 'host' or 'host:port'"
        )

    if not value:
        _fail("empty after normalization")
    if any(c.isspace() for c in value):
        _fail("contains whitespace")
    if any(c in value for c in ("/", "@", "?", "#")):
        _fail("contains '/', '@', '?', or '#'")
    # Validate numeric port (urlparse raises ValueError lazily on .port).
    try:
        parsed_port = urlparse(f"//{value}").port
    except ValueError:
        _fail("port is not an integer")
    if ":" in value and parsed_port is None:
        _fail("port suffix could not be parsed")
    return value


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
    """True when *registry* hostname routes back to the local interface.

    Uses :class:`ipaddress.ip_address` so the full 127.0.0.0/8 range, the
    IPv6 loopback ``::1``, and IPv4-mapped forms like ``::ffff:127.0.0.1``
    are all caught — string-equality alone misses ``127.0.0.2`` and similar
    cluster-side conventions. Returns False (rather than raising) on
    malformed input so callers don't have to wrap defensively.
    """

    try:
        parsed = urlparse(f"//{registry}")
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def replace_registry_host(registry: str, host: str) -> str:
    """Swap the registry hostname while preserving an explicit port.

    Returns *registry* unchanged when no explicit port is present: silently
    substituting only the host can corrupt the push (e.g., changing the
    target port from a registry's 5000 to the default HTTPS 443). Callers
    should ensure the input carries ``host:port`` before invoking. Defensive
    against malformed inputs (returns input unchanged on ``ValueError``)
    even though ``normalize_registry_env`` rejects such values upstream.
    """

    try:
        parsed_port = urlparse(f"//{registry}").port
    except ValueError:
        return registry
    if parsed_port is None:
        return registry
    return f"{host}:{parsed_port}"


def build_engine_runs_in_vm() -> bool:
    """Return True when the local Docker endpoint appears to be a VM.

    Darwin always runs Docker Desktop / Colima / Podman machine inside a
    Linux VM. Windows runs Docker Desktop inside WSL2 or Hyper-V, which
    is the same nested-loopback problem ENG-5719 fixes. Native Linux is
    excluded because ``docker.sock`` is the host kernel.
    """

    system = platform.system()
    if system not in ("Darwin", "Windows"):
        return False
    docker = _docker_info()
    if docker is None or not docker.ok:
        # On Windows, Docker Desktop with the WSL2 backend always virtualizes
        # Linux even when ``docker info`` errors out (e.g., the user hasn't
        # selected a context yet). Trust the platform signal so the loopback
        # remap still fires. On Darwin without a usable Docker engine, fall
        # back to detecting a running Podman machine — that's the same
        # nested-VM topology ENG-5719 needs to remap (codex iter-2 P2 gap).
        if system == "Windows":
            return True
        return running_podman_machine_name() is not None
    return "linux" in docker.output.lower()


def _docker_is_working() -> bool:
    """True iff ``docker info`` succeeded — i.e., the daemon is reachable.

    Distinct from ``shutil.which("docker")``: docker may be installed but
    the daemon down (Docker Desktop quit, context not selected). Picking
    the VM host alias based on PATH alone leads to ``host.docker.internal``
    being chosen even when ``ImagePusher`` will actually fall through to
    Podman (claude iter-3 Important).
    """

    info = _docker_info()
    return info is not None and info.ok


@dataclass(frozen=True)
class _DockerInfo:
    ok: bool
    output: str


def _docker_info() -> Optional["_DockerInfo"]:
    """Cached probe of ``docker info``. Returns None when docker is absent.

    Memoized for the process lifetime to keep ``resolve_dev_registries``
    and ``build_engine_runs_in_vm`` from invoking ``docker info`` twice on
    the same CLI run.
    """

    global _DOCKER_INFO_CACHE
    if _DOCKER_INFO_CACHE is not _DOCKER_INFO_UNSET:
        return _DOCKER_INFO_CACHE
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}|{{.OperatingSystem}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _DOCKER_INFO_CACHE = None
        return None
    _DOCKER_INFO_CACHE = _DockerInfo(ok=(result.returncode == 0), output=result.stdout)
    return _DOCKER_INFO_CACHE


_DOCKER_INFO_UNSET: object = object()
_DOCKER_INFO_CACHE: Optional["_DockerInfo"] = _DOCKER_INFO_UNSET  # type: ignore[assignment]


def _reset_docker_info_cache() -> None:
    """Test hook: clear the ``docker info`` memo between cases."""

    global _DOCKER_INFO_CACHE
    _DOCKER_INFO_CACHE = _DOCKER_INFO_UNSET  # type: ignore[assignment]


def _has_docker() -> bool:
    """True when ``docker`` is on PATH. Used by VM-alias selection.

    Distinct from ``_docker_is_working``: PATH presence answers "is the
    CLI installed" while ``_docker_is_working`` answers "is the daemon
    actually reachable". The latter is what alias selection uses now;
    this remains for callers that only care whether the binary exists.
    """

    return shutil.which("docker") is not None


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
