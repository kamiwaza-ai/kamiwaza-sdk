"""DevLocalRunner — env overlay, Docker Compose lifecycle for local dev."""

from __future__ import annotations

import errno
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
from kamiwaza_extensions.extension_detector import (
    ExtensionDetector,
    infer_extension_type,
)
from kamiwaza_extensions_lib.local_dev import (
    BRIDGE_ENV_VARS,
    BridgeContext,
    LocalDevAuthError,
    extract_extra_hosts,
    prepare_bridge_context,
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
            # PR #87 round-5 review High #2 — `--auth` only works for
            # `app`-type extensions because the bridge mechanism is the
            # Next.js middleware shipped in the app template. For
            # `service` and `tool` extensions there's no Next.js layer
            # to inject envelope headers, so KAMIWAZA_USE_AUTH=true with
            # no bridge would just 401 every protected route. Refuse
            # with a clear hint instead of silently misbehaving.
            #
            # PR #87 round-6 review: route the type lookup through the
            # shared ``infer_extension_type`` helper so the legacy
            # ``template_type`` fallback (and name-prefix heuristics for
            # ``tool-``/``service-``/``mcp-``) are honored. Without this,
            # a legacy extension whose kamiwaza.json carries only
            # ``template_type: "service"`` would default to ``"app"``
            # here and silently slip past the gate.
            ext_type = infer_extension_type(info.metadata or {})
            if ext_type != "app":
                raise LocalDevAuthError(
                    f"--auth is only supported for `app`-type extensions; "
                    f"this extension type is `{ext_type}`. The bridge synthesizes "
                    "envelope headers via the Next.js middleware shipped with "
                    "the app template — service/tool extensions have no "
                    "equivalent Python-side bridge in v1. Run without --auth, "
                    "or wire forwarded-auth headers manually for testing."
                )

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
                # Use **mapping form** (``{KEY: value}``) for the env
                # overlay — defense-in-depth against Docker Compose's
                # ``$`` variable interpolation in list-of-strings form
                # (``["KEY=value"]``). Round-10 review (Comprehensive H)
                # raised this; round-11 (Comprehensive H + Claude H)
                # corrected the rationale: canonical bearers are
                # base64url-encoded JWTs whose alphabet is
                # ``[A-Za-z0-9_-]`` so the **on-the-wire** token never
                # contains ``$``. The defensive concern is the *general
                # case* — if the bridge ever forwards a non-JWT bearer
                # (e.g. a future opaque-PAT path) or if an env value
                # the bridge synthesizes ever contains ``$``, the
                # mapping form is exempt from interpolation per the
                # Compose spec and won't silently eat the literal.
                bridge_env_map = {
                    var: env[var] for var in BRIDGE_ENV_VARS if var in env
                }
                if bridge_env_map and services:
                    auth_env_file = _write_compose_overlay(
                        prefix="kz-auth-env-",
                        services=services,
                        per_service={"environment": dict(bridge_env_map)},
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

    # Two URLs, two consumers (PR #87 round-5 review Critical #1):
    #
    #   container_url — used by the extension's BACKEND code making
    #     server-to-platform calls from inside the Docker container.
    #     Rewrites bare loopbacks to host.docker.internal so the
    #     container can actually reach the host.
    #
    #   browser_url — used as KAMIWAZA_PUBLIC_API_URL, which feeds
    #     /auth/login-url and /auth/logout redirects sent to the
    #     developer's BROWSER. The browser cannot resolve
    #     host.docker.internal; rewriting here would break the auth
    #     flow and TLS hostname verification for localhost certs.
    #     Always keep the developer's original host (localhost,
    #     kamiwaza.test, etc.).
    container_url = connection.url
    if auth:
        container_url = rewrite_bare_loopback_url(container_url)
    browser_url = connection.url

    env = {
        "KAMIWAZA_API_URL": container_url,
        # KAMIWAZA_PUBLIC_API_URL is the RAW browser-facing API URL —
        # keep ``/api`` intact. ``session.create_session_router`` reads
        # ``config.public_api_url`` directly to build
        # ``${base}/auth/login`` and ``${base}/auth/logout`` redirects;
        # the platform serves those endpoints under ``/api/auth/*``, so
        # stripping ``/api`` here produces 404s on every login/logout
        # under ``--auth`` (PR #87 round-10 codex P2). Browser-display
        # consumers (``url.public_base_url``) strip ``/api`` on demand —
        # the env var holds the raw URL.
        "KAMIWAZA_PUBLIC_API_URL": browser_url.rstrip("/"),
        "KAMIWAZA_ENDPOINT": (
            f"{container_url}/v1"
            if not container_url.endswith("/v1")
            else container_url
        ),
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

    Each service gets a deep-copy of ``per_service`` so a future caller
    that mutates the input post-call cannot silently corrupt the values
    written for other services (PR #87 round-3 review defensive coding).
    """
    import copy

    overlay = {
        "services": {
            svc: copy.deepcopy(per_service) for svc in services.keys()
        }
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

    Round-10 review (Comprehensive H): also probes IPv6 loopback so an
    IPv6-only listener bound to ``[::]`` doesn't falsely appear free.
    On dual-stack hosts the IPv4 probe usually catches the listener
    via the v4-mapped binding; on Linux ``net.ipv6.bindv6only=1`` hosts
    or pure-IPv6 services (some local proxies, kubernetes-style
    sidecars) the v4 probe misses and ``compose up`` then fails with
    ``bind: address already in use``.
    """
    # First check: can we connect via IPv4? If yes, something is listening.
    # 50ms is plenty for loopback — ECONNREFUSED returns in microseconds
    # on a free port, and we run this 100× from ``find_available_port``
    # so the cumulative budget matters (round-12 review, Comprehensive M).
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.05)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return False
    except OSError:
        pass

    # Same probe over IPv6 loopback for v6-only listeners.
    if socket.has_ipv6:
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.05)
                if sock.connect_ex(("::1", port, 0, 0)) == 0:
                    return False
        except OSError:
            pass

    # Bind check: try IPv4 first, then IPv6 if v4 succeeds. Round-11
    # review (Comprehensive M2): the asymmetry where the connect probe
    # checked both stacks but the bind only checked v4 meant a port
    # that's v4-free but v6-occupied could pass ``is_port_available``
    # and still fail at ``compose up`` bind time. Both must succeed.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
    except OSError:
        return False
    # Round-12 review (Comprehensive H + Claude H): ``socket.has_ipv6``
    # is a Python build-time constant — it does NOT reflect runtime
    # availability. On hosts with the kernel-level v6 stack disabled
    # (``net.ipv6.conf.all.disable_ipv6=1``, hardened Linux servers,
    # some CI runners), ``socket.socket(AF_INET6, ...)`` or its bind
    # raises OSError unconditionally for every port. The previous
    # ``except OSError: return False`` then made every port look
    # taken and broke ``find_available_port``'s 100-port window. Treat
    # only EADDRINUSE as "port taken"; other errors (EAFNOSUPPORT,
    # EADDRNOTAVAIL, etc.) mean v6 is unavailable here — accept the v4
    # bind as authoritative.
    if socket.has_ipv6:
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
                # ``IPV6_V6ONLY=1`` makes the v6 socket reserve ONLY the
                # v6 stack — without it, on macOS / dual-stack Linux a v6
                # bind to ``::`` also claims the v4 mapping and races
                # with the just-released v4 binding above (lingers in
                # TIME_WAIT for a few hundred ms after socket close,
                # producing a spurious False from this probe).
                try:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                except (AttributeError, OSError):
                    pass
                # Translate the v4 host arg to the corresponding v6 host
                # (was ``"::"`` unconditionally before round-12 — Comprehensive M
                # caught this asymmetry where caller intent was silently broadened).
                v6_host = "::1" if host == "127.0.0.1" else "::"
                sock.bind((v6_host, port, 0, 0))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                return False
            # v6 stack unavailable / unusable for any other reason:
            # the v4 bind already succeeded, so the port is bindable for
            # the actual workload (Docker Compose binds v4 by default
            # on hosts with v6 disabled). Round-12 review (Comprehensive H +
            # Claude H): ``socket.has_ipv6`` is a build-time flag, not a
            # runtime capability — kernel-disabled v6 hosts (e.g.
            # ``net.ipv6.conf.all.disable_ipv6=1``) raise EAFNOSUPPORT /
            # EADDRNOTAVAIL here, which previously made every port look
            # taken and broke ``find_available_port``.
    return True


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
