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
from kamiwaza_extensions_lib.local_dev import (
    BRIDGE_ENV_VARS,
    BridgeContext,
    extract_extra_hosts,
    prepare_bridge_context,
    public_api_url_from,
    rewrite_bare_loopback_url,
)

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
        auth: bool = False,
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

        # 4. Build env overlay (with optional --auth bridge)
        connection = self._conn_mgr.get_active_connection()
        bridge: Optional[BridgeContext] = None
        if auth:
            # prepare_bridge_context raises LocalDevAuthError on no
            # connection / missing bearer / expired token. Surface it to
            # the developer and exit non-zero rather than starting compose.
            bridge = prepare_bridge_context(connection_manager=self._conn_mgr)

        env = os.environ.copy()
        # Defense-in-depth: when --auth is NOT set, scrub any pre-existing
        # bridge env vars from the developer's shell (e.g. left over from
        # another tool) so they cannot accidentally activate the bridge or
        # leak a stale bearer into the container.
        if not auth:
            for var in BRIDGE_ENV_VARS:
                env.pop(var, None)

        if connection:
            overlay = build_env_overlay(
                connection, info.name, auth=auth, bridge=bridge
            )
            env.update(overlay)
            console.print(f"[dim]Using connection:[/dim] {connection.name} ({connection.url})")
            if auth:
                who = (bridge.user_id if bridge else None) or "?"
                console.print(
                    f"[dim]--auth bridge active: forwarding identity for {who}[/dim]"
                )
            else:
                console.print("[dim]KAMIWAZA_USE_AUTH=false (local dev mode)[/dim]")
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
        extra_hosts_file: Optional[str] = None
        auth_env_file: Optional[str] = None

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

            # 7b. Generate extra_hosts overlay when --auth is set. We always
            # inject host.docker.internal:host-gateway under --auth so
            # containers on Linux Docker Engine can reach the host (the
            # bare-loopback URL rewrite to host.docker.internal in
            # build_env_overlay assumes that name resolves, which is only
            # implicit on Docker Desktop). Named loopback hostnames
            # (kamiwaza.test) get their own alias too.
            if auth and connection and info.compose_data:
                eh_entries = build_compose_extra_hosts(connection, auth=True)
                if eh_entries:
                    extra_hosts_file = _write_compose_overlay(
                        prefix="kz-extra-hosts-",
                        services=info.compose_data.get("services", {}),
                        per_service={"extra_hosts": list(eh_entries)},
                    )
                    console.print(
                        f"[dim]Routing {', '.join(eh_entries)} via host-gateway[/dim]"
                    )

            # 7c. Generate env-passthrough overlay under --auth so the
            # bridge env vars actually reach EVERY service inside the
            # container. The runner sets these on the parent compose-CLI
            # process via env.update(overlay), but Docker Compose only
            # propagates env vars into a service's container when the
            # service explicitly declares them in `environment:` or
            # `env_file:`. Without this overlay, frontend containers
            # whose template doesn't list the bridge vars would silently
            # see the gate as undefined and the bridge would no-op (PR
            # #87 round-2 review Critical #1, codex + claude consensus).
            if auth and connection and info.compose_data:
                services = info.compose_data.get("services", {})
                # Use list-of-strings form (`KEY=value`) to override any
                # service-level value the template might already have set.
                bridge_env_entries = [
                    f"{var}={env[var]}" for var in BRIDGE_ENV_VARS if var in env
                ]
                if bridge_env_entries and services:
                    auth_env_file = _write_compose_overlay(
                        prefix="kz-auth-env-",
                        services=services,
                        per_service={"environment": list(bridge_env_entries)},
                    )

            # 8. Build the project-identifier prefix (compose binary +
            # -f / --project-directory args). The same prefix is used for
            # `compose up` and the post-up `compose port` lookup so they
            # query the same project even when the user invokes from a
            # parent directory or with override files (review re-review
            # PR #84 M1).
            compose_prefix = compose_cmd + ["-f", compose_file_arg]
            if sdk_override_file:
                compose_prefix += ["-f", sdk_override_file]
            if extra_hosts_file:
                compose_prefix += ["-f", extra_hosts_file]
            if auth_env_file:
                compose_prefix += ["-f", auth_env_file]
            if (
                patched_compose_file
                or sdk_override_file
                or extra_hosts_file
                or auth_env_file
            ):
                compose_prefix += ["--project-directory", str(info.path)]

            cmd = list(compose_prefix) + ["up", "--build"]
            if detach:
                cmd.append("-d")

            console.print(f"[dim]Running:[/dim] {' '.join(cmd)}")

            # 9. Print access URLs (pre-up). For bare-port specs the host
            # port isn't assigned yet — emit a hint instead so users know
            # what to expect. Detach mode (10b) re-prints with the resolved
            # host port after `compose up -d` returns.
            self._print_urls(info.compose_data, remaps, post_up=False)

            # 10. Run subprocess with signal forwarding
            rc = self._run_subprocess(cmd, env=env, cwd=str(info.path))

            # 10b. Detach mode only: re-resolve bare-port URLs once Docker
            # has actually published them. Foreground mode blocks on
            # compose logs until the user Ctrl+Cs, so there is no
            # post-up moment to reach. Pass the same compose prefix +
            # cwd so the port query targets the project that was started.
            if detach and rc == 0:
                self._print_urls(
                    info.compose_data,
                    remaps,
                    post_up=True,
                    compose_cmd=compose_prefix,
                    cwd=str(info.path),
                )

            return rc
        finally:
            for tmp in (
                patched_compose_file,
                sdk_override_file,
                extra_hosts_file,
                auth_env_file,
            ):
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
        *,
        post_up: bool = False,
        compose_cmd: Optional[List[str]] = None,
        cwd: Optional[str] = None,
    ) -> None:
        """Print per-service access URLs.

        Two modes:
          * ``post_up=False`` (pre-up, default): runs before the compose
            subprocess starts. Mapped ports (``"3000:3000"``) resolve to the
            literal host port. Bare ports (``"3000"``) print a hint —
            Docker hasn't assigned a host port yet, and querying
            ``docker compose port`` here returns nothing.
          * ``post_up=True`` (detach mode only): runs after ``compose up -d``
            returns. Bare ports query ``docker compose port`` to print the
            actual auto-assigned host port.

        Foreground (non-detach) mode blocks on compose logs until the user
        Ctrl+Cs, so there is no post-up moment for it.
        """
        if not compose_data:
            return
        services = compose_data.get("services", {})
        for svc_name, svc_config in services.items():
            ports = svc_config.get("ports", [])
            for port_spec in ports:
                host_port, container_port = parse_port_mapping(str(port_spec))
                if host_port is None:
                    # Bare-port spec (e.g. "3000") — Docker assigns the host
                    # port (ENG-3889 P2).
                    if not post_up:
                        if container_port is not None:
                            console.print(
                                f"[dim]{svc_name}:[/dim] container port "
                                f"{container_port} (host port assigned by Docker; "
                                "run `docker compose ps` once started)"
                            )
                        continue
                    if container_port is not None:
                        host_port = self._docker_compose_port(
                            svc_name, container_port,
                            compose_cmd=compose_cmd,
                            cwd=cwd,
                        )
                    if host_port is None:
                        continue
                if svc_name in remaps:
                    host_port = remaps[svc_name][1]
                console.print(f"[dim]{svc_name}:[/dim] http://localhost:{host_port}")

    @staticmethod
    def _docker_compose_port(
        service: str,
        container_port: int,
        compose_cmd: Optional[List[str]] = None,
        cwd: Optional[str] = None,
    ) -> Optional[int]:
        """Look up the host port Docker assigned to ``service:container_port``.

        ``compose_cmd`` should include the same ``-f`` / ``--project-directory``
        args used to invoke ``compose up`` so the lookup targets the
        right project. The runtime caller in :meth:`run` passes the
        full ``compose_prefix`` it built earlier; ad-hoc callers can
        omit it and the function falls back to ``detect_compose_command()``
        with no project args (works only when there is one project in
        the resolved cwd).

        ``cwd`` defaults to the process cwd. Pass the extension dir
        explicitly when the user invoked from a parent directory or with
        temp override files — otherwise compose looks for a
        ``docker-compose.yml`` in the wrong place (review re-review
        PR #84 M1).
        """
        if compose_cmd is None:
            try:
                compose_cmd = detect_compose_command()
            except FileNotFoundError:
                return None
        try:
            result = subprocess.run(
                [*compose_cmd, "port", service, str(container_port)],
                capture_output=True, text=True, timeout=10, cwd=cwd,
            )
            if result.returncode != 0:
                return None
            line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
            if ":" in line:
                return int(line.rsplit(":", 1)[1])
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
            return None
        return None

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


def build_env_overlay(
    connection: ConnectionInfo,
    extension_name: str,
    *,
    auth: bool = False,
    bridge: Optional[BridgeContext] = None,
) -> Dict[str, str]:
    """Build environment variable overlay from a connection.

    When ``auth=True``, ``bridge`` MUST be provided (caller is expected to
    have validated the active connection via ``prepare_bridge_context``
    upstream so any ``LocalDevAuthError`` surfaces before container start).
    Adds ``KZ_EXT_DEV_LOCAL_AUTH=1``, ``KAMIWAZA_BEARER_TOKEN``, and
    ``KAMIWAZA_USE_AUTH=true`` to the overlay; rewrites bare loopback URLs
    (``localhost`` / ``127.0.0.1`` / ``::1``) to ``host.docker.internal``
    so they're reachable from inside the container.

    Named loopback hostnames (``kamiwaza.test``, ``dev.local``) are NEVER
    rewritten — they keep their TLS-cert-bound name and rely on the compose
    overlay's ``extra_hosts`` (see ``build_compose_extra_hosts``).
    """
    if auth and bridge is None:
        raise ValueError("bridge is required when auth=True")

    url = connection.url
    if auth:
        url = rewrite_bare_loopback_url(url)

    env = {
        "KAMIWAZA_API_URL": url,
        # public_api_url_from is the single source of truth for the
        # /api-stripping convention — keeps prepare_bridge_context and
        # build_env_overlay consistent for trailing-slash URLs.
        "KAMIWAZA_PUBLIC_API_URL": public_api_url_from(url),
        "KAMIWAZA_ENDPOINT": f"{url}/v1" if not url.endswith("/v1") else url,
        "KAMIWAZA_USE_AUTH": "true" if auth else "false",
        "KAMIWAZA_APP_NAME": extension_name,
    }
    if not connection.verify_ssl:
        env["KAMIWAZA_VERIFY_SSL"] = "false"
    if auth:
        # bridge is non-None here (checked above) — narrow for type checkers.
        assert bridge is not None
        env["KZ_EXT_DEV_LOCAL_AUTH"] = "1"
        env["KAMIWAZA_BEARER_TOKEN"] = bridge.bearer_token
    return env


def _write_compose_overlay(
    *,
    prefix: str,
    services: Dict[str, dict],
    per_service: Dict[str, object],
) -> str:
    """Write a compose overlay tempfile that applies ``per_service`` to every
    service in ``services`` and return its path. Caller is responsible for
    deleting the file; ``DevLocalRunner.run`` does this in its ``finally``
    block.

    Used to inject ``extra_hosts`` and bridge env vars without touching the
    extension's ``docker-compose.yml`` so existing extensions get the fix
    without re-scaffolding.
    """
    overlay = {
        "services": {svc: dict(per_service) for svc in services.keys()}
    }
    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", prefix=prefix, delete=False,
    )
    yaml.dump(overlay, fd, default_flow_style=False)
    fd.close()
    return fd.name


def build_compose_extra_hosts(
    connection: ConnectionInfo,
    *,
    auth: bool = False,
) -> List[str]:
    """Return compose ``extra_hosts`` entries needed to reach the connection's
    Kamiwaza URL from inside a container.

    When ``auth=True``, always includes ``host.docker.internal:host-gateway``
    so containers can reach the host on Linux Docker Engine — Docker Desktop
    resolves this name implicitly, but plain Linux Docker Engine does not
    unless the alias is in compose's ``extra_hosts``. Without this, the URL
    rewrite to ``host.docker.internal`` (applied by ``build_env_overlay`` for
    bare loopbacks) fails on Linux with name-resolution errors. Harmless on
    Docker Desktop where it's already aliased.

    Named-loopback hostnames (``kamiwaza.test``, ``dev.local``) get their own
    ``<name>:host-gateway`` entry regardless of ``auth`` so the existing
    behaviour (no ``--auth``, just running locally against a named loopback)
    is preserved.
    """
    entries: List[str] = []
    if auth:
        entries.append("host.docker.internal:host-gateway")
    entries.extend(extract_extra_hosts(connection.url))
    return entries


def parse_port_mapping(port_spec: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse a compose port mapping into ``(host_port, container_port)``.

    Examples::

        '3000:3000'      -> (3000, 3000)
        '8080:3000'      -> (8080, 3000)
        '127.0.0.1:3000:3000' -> (3000, 3000)
        '8000:8000/tcp'  -> (8000, 8000)
        '3000'           -> (None, 3000)   # bare container port; host auto-assigned
        ''               -> (None, None)
    """
    port_spec = str(port_spec).strip()
    # Remove protocol suffix if present (e.g., "8000:8000/tcp")
    port_spec = port_spec.split("/")[0]

    if not port_spec:
        return None, None

    if ":" in port_spec:
        parts = port_spec.rsplit(":", 1)
        try:
            return int(parts[0].split(":")[-1]), int(parts[1])
        except (ValueError, IndexError):
            return None, None

    # Bare container-port spec — host port is auto-assigned by Docker.
    try:
        return None, int(port_spec)
    except ValueError:
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
