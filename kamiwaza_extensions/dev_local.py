"""DevLocalRunner — env overlay, auth bridge, Docker Compose lifecycle."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
import yaml
from rich.console import Console
from urllib3.exceptions import InsecureRequestWarning

from kamiwaza_extensions.connections import ConnectionInfo, ConnectionManager
from kamiwaza_extensions.extension_detector import ExtensionDetector

console = Console(stderr=True)

_BRIDGED_AUTH_HEADER_NAMES = (
    "authorization",
    "x-auth-token",
    "x-user-id",
    "x-user-email",
    "x-user-name",
    "x-user-roles",
    "x-workroom-id",
    "x-request-id",
)


@dataclass
class LocalAuthBridge:
    headers: Dict[str, str]
    subject: str
    roles: list[str]


class DevLocalRunner:
    """Runs an extension locally via Docker Compose with Kamiwaza env overlay."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self._conn_mgr = ConnectionManager(config_dir=config_dir)
        self._detector = ExtensionDetector()

    def run(
        self,
        *,
        detach: bool = False,
        use_auth: bool = False,
        use_auth_bridge: bool = True,
    ) -> int:
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
        compose_files = [info.compose_path]
        override_path: Optional[Path] = None
        if connection:
            token = self._conn_mgr.get_token(connection.name)
            api_key = token.access_token if token else None
            overlay = build_env_overlay(
                connection,
                info.name,
                use_auth=use_auth,
                api_key=api_key,
            )
            env.update(overlay)
            console.print(f"[dim]Using connection:[/dim] {connection.name} ({connection.url})")
            bridge = None
            if use_auth and use_auth_bridge and api_key:
                bridge = resolve_local_auth_bridge(connection, api_key)
                if bridge is not None:
                    bridge_env = build_local_auth_env(bridge.headers, api_key)
                    env.update(bridge_env)
                    services = ((info.compose_data or {}).get("services") or {}).keys()
                    override_path = write_compose_override(services, bridge_env)
                    compose_files.append(override_path)
                    roles = ", ".join(bridge.roles) or "no roles"
                    console.print(
                        f"[dim]Local auth bridge:[/dim] {bridge.subject} ({roles})"
                    )
                else:
                    console.print(
                        "[yellow]Warning:[/yellow] Could not resolve local auth bridge "
                        "from the active connection; falling back to raw auth mode."
                    )
            elif use_auth and use_auth_bridge and not api_key:
                console.print(
                    "[yellow]Warning:[/yellow] No active connection token found; "
                    "falling back to raw auth mode."
                )

            if use_auth:
                if bridge is not None:
                    console.print(
                        "[dim]KAMIWAZA_USE_AUTH=true (connection-bridged local mode)[/dim]"
                    )
                else:
                    console.print("[dim]KAMIWAZA_USE_AUTH=true (auth-enabled local mode)[/dim]")
            else:
                console.print("[dim]KAMIWAZA_USE_AUTH=false (local dev mode)[/dim]")
        else:
            console.print("[yellow]No Kamiwaza connection configured — running in standalone mode[/yellow]")

        # 5. Build command
        cmd = compose_cmd
        for compose_file in compose_files:
            cmd.extend(["-f", str(compose_file)])
        cmd.extend(["up", "--build"])
        if detach:
            cmd.append("-d")

        console.print(f"[dim]Running:[/dim] {' '.join(cmd)}")

        # 6. Run subprocess with signal forwarding
        try:
            return self._run_subprocess(cmd, env=env, cwd=str(info.path))
        finally:
            if override_path is not None:
                override_path.unlink(missing_ok=True)

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
    use_auth: bool = False,
    api_key: Optional[str] = None,
) -> Dict[str, str]:
    """Build environment variable overlay from a connection."""
    url = connection.url
    env = {
        "KAMIWAZA_API_URL": url,
        "KAMIWAZA_PUBLIC_API_URL": url.removesuffix("/api"),
        "KAMIWAZA_ENDPOINT": f"{url}/v1" if not url.endswith("/v1") else url,
        "KAMIWAZA_USE_AUTH": "true" if use_auth else "false",
        "KAMIWAZA_APP_NAME": extension_name,
    }
    if not connection.verify_ssl:
        env["KAMIWAZA_VERIFY_SSL"] = "false"
    if api_key:
        env["KAMIWAZA_API_KEY"] = api_key
    return env


def resolve_local_auth_bridge(
    connection: ConnectionInfo,
    api_key: str,
) -> Optional[LocalAuthBridge]:
    """Resolve a local auth bridge from the active CLI connection."""
    headers = _fetch_validate_headers(connection, api_key)
    if headers:
        subject = headers.get("x-user-name") or headers.get("x-user-id") or "current user"
        roles = _split_roles(headers.get("x-user-roles", ""))
        return LocalAuthBridge(headers=headers, subject=subject, roles=roles)

    user = _fetch_current_user(connection, api_key)
    if user is None:
        return None

    roles = _split_roles(",".join(user.get("roles") or []))
    headers = _compact_headers(
        {
            "authorization": f"Bearer {api_key}",
            "x-user-id": user.get("sub"),
            "x-user-email": user.get("email"),
            "x-user-name": user.get("username") or user.get("sub"),
            "x-user-roles": ",".join(roles),
        }
    )
    subject = headers.get("x-user-name") or headers.get("x-user-id") or "current user"
    return LocalAuthBridge(headers=headers, subject=subject, roles=roles)


def build_local_auth_env(headers: Dict[str, str], api_key: str) -> Dict[str, str]:
    """Build container env vars for the connection-backed local auth bridge."""
    bridge_headers = dict(headers)
    bridge_headers.setdefault("authorization", f"Bearer {api_key}")
    return {
        "KAMIWAZA_API_KEY": api_key,
        "KAMIWAZA_LOCAL_DEV_AUTH_BRIDGE": "true",
        "KAMIWAZA_LOCAL_DEV_AUTH_HEADERS_JSON": json.dumps(
            bridge_headers, separators=(",", ":"), sort_keys=True
        ),
    }


def build_compose_override(
    services: Iterable[str],
    environment: Dict[str, str],
) -> Dict[str, Any]:
    """Build a Compose override that injects env vars into every service."""
    return {
        "services": {
            service_name: {"environment": dict(environment)}
            for service_name in services
        }
    }


def write_compose_override(services: Iterable[str], environment: Dict[str, str]) -> Path:
    """Write a temporary Compose override file for bridge env injection."""
    override = build_compose_override(services, environment)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix="kz-ext-dev-local-",
        suffix=".yml",
        delete=False,
    )
    try:
        yaml.safe_dump(override, handle, sort_keys=False)
    finally:
        handle.close()
    return Path(handle.name)


def _request(
    method: str,
    url: str,
    *,
    headers: Dict[str, str],
    verify_ssl: bool,
) -> requests.Response:
    kwargs = {
        "headers": headers,
        "timeout": 10,
        "verify": verify_ssl,
    }
    if verify_ssl:
        return requests.request(method, url, **kwargs)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        return requests.request(method, url, **kwargs)


def _fetch_validate_headers(connection: ConnectionInfo, api_key: str) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Forwarded-Method": "GET",
        "X-Forwarded-Uri": "/",
    }
    try:
        response = _request(
            "GET",
            f"{connection.url.rstrip('/')}/auth/validate",
            headers=headers,
            verify_ssl=connection.verify_ssl,
        )
    except requests.RequestException:
        return {}

    if not response.ok:
        return {}

    bridged = {"authorization": f"Bearer {api_key}"}
    for key in _BRIDGED_AUTH_HEADER_NAMES:
        if key == "authorization":
            continue
        value = response.headers.get(key)
        if value:
            bridged[key] = value
    return bridged if bridged.get("x-user-id") else {}


def _fetch_current_user(connection: ConnectionInfo, api_key: str) -> Optional[Dict[str, Any]]:
    try:
        response = _request(
            "GET",
            f"{connection.url.rstrip('/')}/auth/users/me",
            headers={"Authorization": f"Bearer {api_key}"},
            verify_ssl=connection.verify_ssl,
        )
    except requests.RequestException:
        return None

    if not response.ok:
        return None

    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _compact_headers(headers: Dict[str, Any]) -> Dict[str, str]:
    return {
        str(key).lower(): str(value)
        for key, value in headers.items()
        if value not in (None, "")
    }


def _split_roles(raw: str) -> list[str]:
    return [role.strip() for role in raw.split(",") if role.strip()]


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
