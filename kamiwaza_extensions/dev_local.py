"""DevLocalRunner — env overlay, Docker Compose lifecycle for local dev."""

from __future__ import annotations

import errno
import ipaddress
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from rich.console import Console

from kamiwaza_extensions.connections import ConnectionInfo, ConnectionManager
from kamiwaza_extensions.extension_detector import (
    ExtensionDetector,
    ExtensionInfo,
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
TEMPLATE_DEV_COMPOSE_FILENAME = "kamiwaza-compose.dev.yml"


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
        info = self._detector.detect()
        if info.compose_path is None:
            raise FileNotFoundError(
                f"No compose file found in {info.path}. "
                "Expected docker-compose.yml or compose.yml."
            )

        compose_cmd = detect_compose_command()
        env, connection = self._prepare_environment(info, auth=auth)
        override_spec = self._prepare_sdk_override(sdk_repo, info.path)

        remaps: Dict[str, Tuple[int, int]] = {}
        temporary_files: List[str] = []
        url_poll_thread: Optional[threading.Thread] = None
        url_poll_stop = threading.Event()

        try:
            remaps, compose_file_arg, base_was_patched = self._prepare_base_compose(
                info, temporary_files
            )
            template_dev_override_file = self._find_template_dev_override(info.path)
            local_override_file = self._find_local_override(info.compose_path)
            sdk_override_file, sdk_build_patch_file = self._prepare_sdk_overlays(
                info, override_spec, temporary_files
            )
            extra_hosts_file, auth_env_file = self._prepare_auth_overlays(
                info,
                auth=auth,
                connection=connection,
                env=env,
                temporary_files=temporary_files,
            )
            overlays = [
                template_dev_override_file,
                local_override_file,
                sdk_override_file,
                sdk_build_patch_file,
                extra_hosts_file,
                auth_env_file,
            ]
            compose_prefix = self._build_compose_prefix(
                compose_cmd,
                compose_file_arg,
                overlays,
                info.path,
                force_project_directory=base_was_patched,
            )

            cmd = list(compose_prefix) + ["up", "--build"]
            if detach:
                cmd.append("-d")

            console.print(f"[dim]Running:[/dim] {' '.join(cmd)}")

            # 9. Print access URLs (pre-up). For bare-port specs the host
            # port isn't assigned yet — emit a hint instead so users know
            # what to expect. Detach mode (10b) re-prints with the resolved
            # host port after `compose up -d` returns; foreground mode
            # delegates to a background polling thread (10a) so the URL
            # surfaces while compose is still streaming logs.
            self._print_urls(info.compose_data, remaps, post_up=False)

            # 10a. Foreground mode: spawn a background thread that polls
            # ``docker compose port`` until the bare-port assignments are
            # visible, then prints the URL line. Without this, the user
            # sees "host port assigned by Docker" but never gets the
            # actual URL (it scrolls past in the build / Next.js startup
            # logs and is easy to miss). Daemon thread so it dies with
            # the process if compose exits early. (ENG-3901 / F-008)
            #
            # The stop event lets the ``finally`` cleanup signal the
            # poller to exit before unlinking the compose override files
            # the poller passes to ``docker compose port``. Without it
            # the poller could wake from ``time.sleep`` after the temp
            # YAMLs are gone and shell out to compose with stale ``-f``
            # paths (PR #91 round-2 review High consensus).
            if not detach and self._has_bare_ports(info.compose_data):
                url_poll_thread = threading.Thread(
                    target=self._poll_and_print_urls,
                    args=(info.compose_data, compose_prefix, str(info.path)),
                    kwargs={"stop_event": url_poll_stop},
                    daemon=True,
                    name="kz-ext-url-poll",
                )
                url_poll_thread.start()

            # 10. Run subprocess with signal forwarding
            rc = self._run_subprocess(cmd, env=env, cwd=str(info.path))

            # 10b. Detach mode only: re-resolve bare-port URLs once Docker
            # has actually published them. Foreground mode handled by 10a
            # above. Pass the same compose prefix + cwd so the port query
            # targets the project that was started.
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
            url_poll_stop.set()
            if url_poll_thread is not None and url_poll_thread.is_alive():
                url_poll_thread.join(timeout=2.0)
            self._cleanup_temp_files(temporary_files)

    def _prepare_environment(
        self, info: ExtensionInfo, *, auth: bool
    ) -> Tuple[Dict[str, str], Optional[ConnectionInfo]]:
        """Build the compose environment and optional local-auth bridge."""
        connection = self._conn_mgr.get_active_connection()
        bridge: Optional[BridgeContext] = None
        if auth:
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
            bridge = prepare_bridge_context(connection_manager=self._conn_mgr)

        env = os.environ.copy()
        if not auth:
            for var in BRIDGE_ENV_VARS:
                env.pop(var, None)

        if not connection:
            console.print(
                "[yellow]No Kamiwaza connection configured — running in standalone mode[/yellow]"
            )
            return env, connection

        env.update(build_env_overlay(connection, info.name, auth=auth, bridge=bridge))
        console.print(
            f"[dim]Using connection:[/dim] {connection.name} ({connection.url})"
        )
        if auth:
            who = (bridge.user_id if bridge else None) or "?"
            console.print(
                f"[dim]--auth bridge active: forwarding identity for {who}[/dim]"
            )
        else:
            console.print("[dim]KAMIWAZA_USE_AUTH=false (local dev mode)[/dim]")
        return env, connection

    @staticmethod
    def _prepare_sdk_override(sdk_repo: Optional[str], extension_path: Path) -> Any:
        """Resolve, validate, and build an SDK override when requested."""
        from kamiwaza_extensions.sdk_override import (
            SdkOverrideSpec,
            build_typescript_lib,
            is_typescript_dist_stale,
            print_override_diagnostics,
            resolve_sdk_override,
            validate_sdk_override,
        )

        override_spec = resolve_sdk_override(sdk_repo, extension_path)
        if not override_spec:
            return None

        validation = validate_sdk_override(override_spec)
        for err in validation.errors:
            console.print(f"[red]SDK override error: {err}[/red]")
        for warn in validation.warnings:
            console.print(f"[yellow]SDK override: {warn}[/yellow]")
        if not validation.ok:
            console.print("[red]SDK override disabled due to errors above[/red]")
            return None

        typescript_needs_build = override_spec.typescript and (
            override_spec.build_typescript
            or not override_spec.typescript_dist_path.is_dir()
            or is_typescript_dist_stale(override_spec)
        )
        if typescript_needs_build and not build_typescript_lib(override_spec):
            console.print("[yellow]Continuing without TypeScript override[/yellow]")
            override_spec = SdkOverrideSpec(
                sdk_repo=override_spec.sdk_repo,
                python=override_spec.python,
                typescript=False,
                build_typescript=False,
            )

        print_override_diagnostics(override_spec)
        return override_spec

    @staticmethod
    def _prepare_base_compose(
        info: ExtensionInfo, temporary_files: List[str]
    ) -> Tuple[Dict[str, Tuple[int, int]], str, bool]:
        """Resolve port conflicts and return the effective base compose file."""
        remaps = resolve_port_conflicts(info.compose_data) if info.compose_data else {}
        for svc, (orig, new) in remaps.items():
            console.print(
                f"[yellow]Port {orig} in use — remapping {svc} to {new}[/yellow]"
            )
        if not remaps or not info.compose_data:
            return remaps, str(info.compose_path), False

        patched = apply_port_remaps(info.compose_data, remaps)
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", prefix="kz-ports-", delete=False
        )
        yaml.dump(patched, fd, default_flow_style=False)
        fd.close()
        temporary_files.append(fd.name)
        return remaps, fd.name, True

    @staticmethod
    def _find_template_dev_override(extension_path: Path) -> Optional[str]:
        candidate = extension_path / TEMPLATE_DEV_COMPOSE_FILENAME
        return str(candidate) if candidate.is_file() else None

    @staticmethod
    def _find_local_override(compose_path: Path) -> Optional[str]:
        """Find Compose's user-owned override matching the detected base file."""
        compose_dir = compose_path.parent
        base_ext = compose_path.suffix.lstrip(".") or "yml"
        override_exts = [base_ext] + [ext for ext in ("yml", "yaml") if ext != base_ext]
        for ext in override_exts:
            override_name = f"{compose_path.stem}.override.{ext}"
            candidate = compose_dir / override_name
            if candidate.is_file():
                console.print(f"[dim]Loading local override: {override_name}[/dim]")
                return str(candidate)
        return None

    @staticmethod
    def _prepare_sdk_overlays(
        info: ExtensionInfo, override_spec: Any, temporary_files: List[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        """Generate runtime and Dockerfile SDK compose overlays."""
        from kamiwaza_extensions.sdk_override import (
            generate_compose_override,
            generate_local_build_dockerfile_patches,
        )

        if not override_spec or not info.compose_data:
            return None, None

        sdk_override_data = generate_compose_override(
            override_spec, info.compose_data, extension_dir=info.path
        )
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", prefix="kz-sdk-", delete=False
        )
        yaml.dump(sdk_override_data, fd, default_flow_style=False)
        fd.close()
        temporary_files.append(fd.name)
        sdk_override_file = fd.name

        df_patches = generate_local_build_dockerfile_patches(
            override_spec, info.compose_data, info.path
        )
        if not df_patches:
            return sdk_override_file, None

        build_overlay_services: dict = {}
        base_services = info.compose_data.get("services", {})
        for svc, patched in df_patches.items():
            df_fd = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".Dockerfile",
                prefix=f"kz-sdk-df-{svc}-",
                delete=False,
            )
            df_fd.write(patched)
            df_fd.close()
            temporary_files.append(df_fd.name)
            base_build = (base_services.get(svc) or {}).get("build")
            merged_build: Dict[str, Any] = {}
            if isinstance(base_build, str):
                merged_build["context"] = base_build
            elif isinstance(base_build, dict):
                merged_build.update(base_build)
            merged_build["dockerfile"] = df_fd.name
            build_overlay_services[svc] = {"build": merged_build}

        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", prefix="kz-sdk-build-", delete=False
        )
        yaml.dump({"services": build_overlay_services}, fd, default_flow_style=False)
        fd.close()
        temporary_files.append(fd.name)
        return sdk_override_file, fd.name

    @staticmethod
    def _prepare_auth_overlays(
        info: ExtensionInfo,
        *,
        auth: bool,
        connection: Optional[ConnectionInfo],
        env: Dict[str, str],
        temporary_files: List[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        """Generate host routing and bridge-environment compose overlays."""
        if not auth or not connection or not info.compose_data:
            return None, None

        services = info.compose_data.get("services", {})
        extra_hosts_file: Optional[str] = None
        eh_entries = build_compose_extra_hosts(connection, auth=True)
        if eh_entries:
            extra_hosts_file = _write_compose_overlay(
                prefix="kz-extra-hosts-",
                services=services,
                per_service={"extra_hosts": list(eh_entries)},
            )
            temporary_files.append(extra_hosts_file)
            console.print(
                f"[dim]Routing {', '.join(eh_entries)} via host-gateway[/dim]"
            )

        auth_env_file: Optional[str] = None
        bridge_env_map = {var: env[var] for var in BRIDGE_ENV_VARS if var in env}
        if bridge_env_map and services:
            auth_env_file = _write_compose_overlay(
                prefix="kz-auth-env-",
                services=services,
                per_service={"environment": dict(bridge_env_map)},
            )
            temporary_files.append(auth_env_file)
        return extra_hosts_file, auth_env_file

    @staticmethod
    def _build_compose_prefix(
        compose_cmd: List[str],
        compose_file_arg: str,
        overlays: List[Optional[str]],
        project_directory: Path,
        *,
        force_project_directory: bool,
    ) -> List[str]:
        """Build one stable Compose prefix for up and port inspection."""
        prefix = compose_cmd + ["-f", compose_file_arg]
        for overlay in overlays:
            if overlay:
                prefix += ["-f", overlay]
        if force_project_directory or any(overlays):
            prefix += ["--project-directory", str(project_directory)]
        return prefix

    @staticmethod
    def _cleanup_temp_files(paths: List[str]) -> None:
        for tmp in paths:
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
                            svc_name,
                            container_port,
                            compose_cmd=compose_cmd,
                            cwd=cwd,
                        )
                    if host_port is None:
                        continue
                if svc_name in remaps:
                    host_port = remaps[svc_name][1]
                console.print(f"[dim]{svc_name}:[/dim] http://localhost:{host_port}")

    @staticmethod
    def _has_bare_ports(compose_data: Optional[dict]) -> bool:
        """True iff any service in ``compose_data`` has a bare-port spec
        (e.g. ``"3000"``) whose host port Docker will auto-assign.

        Mapped specs (``"3000:3000"``) are already known and printed by
        the pre-up pass. Only bare specs need post-up resolution.
        """
        if not compose_data:
            return False
        for svc_config in compose_data.get("services", {}).values():
            for port_spec in svc_config.get("ports", []) or []:
                host_port, _ = parse_port_mapping(str(port_spec))
                if host_port is None:
                    return True
        return False

    def _poll_and_print_urls(
        self,
        compose_data: dict,
        compose_cmd: List[str],
        cwd: str,
        *,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """Background-thread loop: poll ``docker compose port`` until the
        bare-port host ports are assigned, then print the URLs.

        Runs in a daemon thread alongside the foreground compose process.
        Polls at ~1.5s intervals up to ~60s (long enough for a cold image
        pull + Next.js build) before giving up silently. The user can
        always fall back to ``docker compose port`` themselves; this is
        a "make the common case easy" affordance, not a hard guarantee.

        If ``stop_event`` is set (the runner's ``finally`` block signals
        it), the loop returns promptly so the cleanup can unlink the
        compose override files this thread depends on.
        """
        if stop_event is None:
            stop_event = threading.Event()
        # Initial delay so we don't compete with compose's own "Building" /
        # "Pulling" / "Creating" output for the user's eye. Use the stop
        # event's wait so a fast Ctrl+C doesn't have to bleed off this
        # delay before exiting.
        if stop_event.wait(2.0):
            return
        deadline = time.monotonic() + 60.0
        # Dedupe ``(svc_name, container_port)`` so a (technically illegal
        # but YAML-valid) duplicate bare-port spec like ``ports: ["3000",
        # "3000"]`` doesn't make the loop's completion check
        # (``len(printed) < len(services_with_bare_ports)``) permanently
        # true and spin until the 60s deadline (Claude review on PR #91).
        # ``dict.fromkeys`` preserves insertion order; sets would not.
        services_with_bare_ports: List[Tuple[str, int]] = list(
            dict.fromkeys(
                (svc_name, container_port)
                for svc_name, svc_config in compose_data.get("services", {}).items()
                for port_spec in svc_config.get("ports", []) or []
                for host_port, container_port in [parse_port_mapping(str(port_spec))]
                if host_port is None and container_port is not None
            )
        )
        # Key on ``(svc_name, container_port)`` so multi-port services
        # (e.g. a frontend exposing both 3000 and 4173 for HMR) get every
        # URL printed, and the loop's completion check actually
        # terminates rather than spinning to the 60s deadline (Codex P3
        # / Claude review on PR #91).
        printed: Dict[Tuple[str, int], int] = {}
        while (
            not stop_event.is_set()
            and time.monotonic() < deadline
            and len(printed) < len(services_with_bare_ports)
        ):
            for svc_name, container_port in services_with_bare_ports:
                if stop_event.is_set():
                    return
                key = (svc_name, container_port)
                if key in printed:
                    continue
                host_port = self._docker_compose_port(
                    svc_name,
                    container_port,
                    compose_cmd=compose_cmd,
                    cwd=cwd,
                )
                if host_port is None:
                    continue
                console.print(
                    f"\n[bold green]{svc_name}:[/bold green] "
                    f"[link]http://localhost:{host_port}[/link]"
                )
                printed[key] = host_port
            if len(printed) < len(services_with_bare_ports):
                if stop_event.wait(1.5):
                    return

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
                capture_output=True,
                text=True,
                timeout=10,
                cwd=cwd,
            )
            if result.returncode != 0:
                return None
            line = (
                result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
            )
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
    if not connection.effective_verify_ssl():
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

    overlay = {"services": {svc: copy.deepcopy(per_service) for svc in services.keys()}}
    fd = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yml",
        prefix=prefix,
        delete=False,
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
                # Translate the v4 host arg to the corresponding v6 host.
                # Round-12 (Comprehensive M1) flagged the unconditional
                # ``"::"``; round-13 (Claude M3) further narrows non-loopback
                # v4 IPs to their IPv4-mapped v6 form so caller intent
                # (e.g. ``192.168.1.5``) isn't silently broadened to
                # all v6 interfaces. Non-IP hosts (e.g. ``"0.0.0.0"``)
                # default to the wildcard v6 address.
                if host == "127.0.0.1":
                    v6_host = "::1"
                elif host == "0.0.0.0":
                    v6_host = "::"
                else:
                    try:
                        v6_host = f"::ffff:{ipaddress.IPv4Address(host)}"
                    except (ValueError, ipaddress.AddressValueError):
                        v6_host = "::"
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
    raise RuntimeError(
        f"No available port found in range {start}–{start + max_tries - 1}"
    )


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
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
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
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        pass

    raise FileNotFoundError(
        "Docker Compose not found. Install Docker Desktop or docker-compose."
    )
