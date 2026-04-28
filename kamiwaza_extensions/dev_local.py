"""DevLocalRunner — env overlay, Docker Compose lifecycle for local dev."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from rich.console import Console

from kamiwaza_extensions.connections import ConnectionInfo, ConnectionManager
from kamiwaza_extensions.extension_detector import ExtensionDetector

console = Console(stderr=True)


class DevLocalRunner:
    """Runs an extension locally via Docker Compose with Kamiwaza env overlay."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self._conn_mgr = ConnectionManager(config_dir=config_dir)
        self._detector = ExtensionDetector()

    def run(
        self,
        *,
        detach: bool = False,
        sdk_repo: Optional[str] = None,
    ) -> int:
        from kamiwaza_extensions.sdk_override import (
            SdkOverrideSpec,
            build_typescript_lib,
            generate_compose_override,
            print_override_diagnostics,
            resolve_sdk_override,
            validate_sdk_override,
        )

        # 1. Detect extension (shared logic)
        info = self._detector.detect()

        # 2. Ensure compose file exists
        if info.compose_path is None:
            raise FileNotFoundError(
                f"No compose file found in {info.path}. "
                "Expected docker-compose.yml or compose.yml."
            )

        # 3. Detect compose command
        compose_cmd = detect_compose_command()

        # 4. Build env overlay
        connection = self._conn_mgr.get_active_connection()
        env = os.environ.copy()
        if connection:
            overlay = build_env_overlay(connection, info.name)
            env.update(overlay)
            console.print(f"[dim]Using connection:[/dim] {connection.name} ({connection.url})")
            console.print(f"[dim]KAMIWAZA_USE_AUTH=false (local dev mode)[/dim]")
        else:
            console.print("[yellow]No Kamiwaza connection configured — running in standalone mode[/yellow]")

        # 5. Resolve SDK override
        override_spec = resolve_sdk_override(sdk_repo, info.path)

        if override_spec:
            validation = validate_sdk_override(override_spec)
            for err in validation.errors:
                console.print(f"[red]SDK override error: {err}[/red]")
            for warn in validation.warnings:
                console.print(f"[yellow]SDK override: {warn}[/yellow]")

            if not validation.ok:
                console.print("[red]SDK override disabled due to errors above[/red]")
                override_spec = None
            else:
                # Build TypeScript if needed
                if override_spec.typescript and (
                    override_spec.build_typescript
                    or not override_spec.typescript_dist_path.is_dir()
                ):
                    if not build_typescript_lib(override_spec):
                        console.print("[yellow]Continuing without TypeScript override[/yellow]")
                        override_spec = SdkOverrideSpec(
                            sdk_repo=override_spec.sdk_repo,
                            python=override_spec.python,
                            typescript=False,
                            build_typescript=False,
                        )

                print_override_diagnostics(override_spec)

        # 6. Check ports and remap if needed
        remaps: Dict[str, Tuple[int, int]] = {}
        patched_compose_file: Optional[str] = None
        sdk_override_file: Optional[str] = None

        try:
            if info.compose_data:
                remaps = resolve_port_conflicts(info.compose_data)
                if remaps:
                    for svc, (orig, new) in remaps.items():
                        console.print(
                            f"[yellow]Port {orig} in use — remapping {svc} to {new}[/yellow]"
                        )

            # If ports need remapping, write a patched copy of the compose file
            # (compose override files append ports rather than replacing them)
            if remaps and info.compose_data:
                patched = apply_port_remaps(info.compose_data, remaps)
                fd = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".yml", prefix="kz-ports-", delete=False
                )
                yaml.dump(patched, fd, default_flow_style=False)
                fd.close()
                patched_compose_file = fd.name
                compose_file_arg = patched_compose_file
            else:
                compose_file_arg = str(info.compose_path)

            # 7. Generate SDK override compose file
            if override_spec and info.compose_data:
                sdk_override_data = generate_compose_override(
                    override_spec, info.compose_data, extension_dir=info.path,
                )
                fd = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".yml", prefix="kz-sdk-", delete=False
                )
                yaml.dump(sdk_override_data, fd, default_flow_style=False)
                fd.close()
                sdk_override_file = fd.name

            # 8. Build command
            cmd = compose_cmd + ["-f", compose_file_arg]
            if sdk_override_file:
                cmd += ["-f", sdk_override_file]
            if patched_compose_file or sdk_override_file:
                cmd += ["--project-directory", str(info.path)]
            cmd += ["up", "--build"]
            if detach:
                cmd.append("-d")

            console.print(f"[dim]Running:[/dim] {' '.join(cmd)}")

            # 9. Print access URLs
            self._print_urls(info.compose_data, remaps)

            # 10. Run subprocess with signal forwarding
            return self._run_subprocess(cmd, env=env, cwd=str(info.path))
        finally:
            for tmp in (patched_compose_file, sdk_override_file):
                if tmp:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass

    # ------------------------------------------------------------------
    # URL display
    # ------------------------------------------------------------------

    def _print_urls(
        self,
        compose_data: Optional[dict],
        remaps: Dict[str, Tuple[int, int]],
    ) -> None:
        if not compose_data:
            return
        services = compose_data.get("services", {})
        for svc_name, svc_config in services.items():
            ports = svc_config.get("ports", [])
            for port_spec in ports:
                host_port, _ = parse_port_mapping(str(port_spec))
                if host_port is None:
                    continue
                if svc_name in remaps:
                    host_port = remaps[svc_name][1]
                console.print(f"[dim]{svc_name}:[/dim] http://localhost:{host_port}")

    # ------------------------------------------------------------------
    # Subprocess management
    # ------------------------------------------------------------------

    def _run_subprocess(self, cmd: List[str], *, env: dict, cwd: str) -> int:
        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

        def _forward_signal(signum, frame):
            proc.send_signal(signum)

        prev_int = signal.signal(signal.SIGINT, _forward_signal)
        prev_term = signal.signal(signal.SIGTERM, _forward_signal)

        try:
            return proc.wait()
        finally:
            signal.signal(signal.SIGINT, prev_int)
            signal.signal(signal.SIGTERM, prev_term)


# ------------------------------------------------------------------
# Standalone helpers (testable without a runner instance)
# ------------------------------------------------------------------


def build_env_overlay(connection: ConnectionInfo, extension_name: str) -> Dict[str, str]:
    """Build environment variable overlay from a connection."""
    url = connection.url
    env = {
        "KAMIWAZA_API_URL": url,
        "KAMIWAZA_PUBLIC_API_URL": url.removesuffix("/api"),
        "KAMIWAZA_ENDPOINT": f"{url}/v1" if not url.endswith("/v1") else url,
        "KAMIWAZA_USE_AUTH": "false",
        "KAMIWAZA_APP_NAME": extension_name,
    }
    if not connection.verify_ssl:
        env["KAMIWAZA_VERIFY_SSL"] = "false"
    return env


def parse_port_mapping(port_spec: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse a compose port mapping like '3000:3000' into (host_port, container_port).

    Returns (None, None) for container-only specs like '3000'.
    """
    port_spec = str(port_spec).strip()
    # Remove protocol suffix if present (e.g., "8000:8000/tcp")
    port_spec = port_spec.split("/")[0]

    if ":" in port_spec:
        parts = port_spec.rsplit(":", 1)
        try:
            return int(parts[0].split(":")[-1]), int(parts[1])
        except (ValueError, IndexError):
            return None, None
    return None, None


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    """Check if a TCP port is available for binding.

    Uses connect to detect listeners (catches Docker/other processes bound to
    any interface) and bind without SO_REUSEADDR as a fallback.
    """
    # First check: can we connect? If yes, something is listening.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return False
    except OSError:
        pass

    # Second check: can we bind without SO_REUSEADDR?
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
            return True
    except OSError:
        return False


def find_available_port(start: int, host: str = "0.0.0.0", max_tries: int = 100) -> int:
    """Find the next available port starting from *start*."""
    for offset in range(max_tries):
        candidate = start + offset
        if candidate > 65535:
            break
        if is_port_available(candidate, host):
            return candidate
    raise RuntimeError(f"No available port found in range {start}–{start + max_tries - 1}")


def resolve_port_conflicts(
    compose_data: dict,
) -> Dict[str, Tuple[int, int]]:
    """Check compose services for host port conflicts and find alternatives.

    Returns a dict of ``{service_name: (original_host_port, new_host_port)}``
    for each service whose host port is occupied.  Returns an empty dict if all
    ports are free.
    """
    remaps: Dict[str, Tuple[int, int]] = {}
    # Track ports we've already claimed (either original or remapped) so two
    # services don't both remap to the same port.
    claimed: set[int] = set()

    services = compose_data.get("services", {})
    for svc_name, svc_config in services.items():
        for port_spec in svc_config.get("ports", []):
            host_port, _ = parse_port_mapping(str(port_spec))
            if host_port is None:
                continue

            if is_port_available(host_port) and host_port not in claimed:
                claimed.add(host_port)
            else:
                new_port = find_available_port(host_port + 1)
                while new_port in claimed:
                    new_port = find_available_port(new_port + 1)
                remaps[svc_name] = (host_port, new_port)
                claimed.add(new_port)
            # Only handle the first host port mapping per service
            break

    return remaps


def apply_port_remaps(
    compose_data: dict,
    remaps: Dict[str, Tuple[int, int]],
) -> dict:
    """Return a deep copy of *compose_data* with host ports replaced.

    For each service in *remaps*, every port mapping whose host port matches the
    original is rewritten to use the new host port.  All other compose content
    (build contexts, volumes, environment, etc.) is preserved as-is.
    """
    import copy

    patched = copy.deepcopy(compose_data)
    services = patched.get("services", {})

    for svc_name, (original_host, new_host) in remaps.items():
        svc = services.get(svc_name)
        if not svc or "ports" not in svc:
            continue
        new_ports = []
        for port_spec in svc["ports"]:
            hp, cp = parse_port_mapping(str(port_spec))
            if hp == original_host and cp is not None:
                new_ports.append(f"{new_host}:{cp}")
            else:
                new_ports.append(port_spec)
        svc["ports"] = new_ports

    return patched


def detect_compose_command() -> List[str]:
    """Detect whether docker compose v2 or v1 is available."""
    # Try v2 plugin first
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        return ["docker", "compose"]
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try v1 standalone
    try:
        subprocess.run(
            ["docker-compose", "--version"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        return ["docker-compose"]
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    raise FileNotFoundError(
        "Docker Compose not found. Install Docker Desktop or docker-compose."
    )
