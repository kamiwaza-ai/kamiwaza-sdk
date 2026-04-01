"""DevLocalRunner — env overlay, Docker Compose lifecycle for local dev."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console

from kamiwaza_extensions.connections import ConnectionInfo, ConnectionManager
from kamiwaza_extensions.extension_detector import ExtensionDetector

console = Console(stderr=True)


class DevLocalRunner:
    """Runs an extension locally via Docker Compose with Kamiwaza env overlay."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self._conn_mgr = ConnectionManager(config_dir=config_dir)
        self._detector = ExtensionDetector()

    def run(self, *, detach: bool = False) -> int:
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

        # 5. Build command
        cmd = compose_cmd + ["-f", str(info.compose_path), "up", "--build"]
        if detach:
            cmd.append("-d")

        console.print(f"[dim]Running:[/dim] {' '.join(cmd)}")

        # 6. Run subprocess with signal forwarding
        return self._run_subprocess(cmd, env=env, cwd=str(info.path))

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
