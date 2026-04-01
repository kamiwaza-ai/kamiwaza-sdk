"""DevLocalRunner — extension detection, env overlay, Docker Compose lifecycle."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console

from kamiwaza_extensions.connections import ConnectionInfo, ConnectionManager
from kamiwaza_extensions.constants import COMPOSE_FILENAMES

console = Console(stderr=True)


class DevLocalRunner:
    """Runs an extension locally via Docker Compose with Kamiwaza env overlay."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self._conn_mgr = ConnectionManager(config_dir=config_dir)

    def run(self, *, detach: bool = False) -> int:
        # 1. Find extension
        ext_dir = self._find_extension()

        # 2. Read extension name from kamiwaza.json
        ext_name = self._read_extension_name(ext_dir)

        # 3. Find compose file
        compose_file = self._find_compose_file(ext_dir)

        # 4. Detect compose command
        compose_cmd = detect_compose_command()

        # 5. Build env overlay
        connection = self._conn_mgr.get_active_connection()
        env = os.environ.copy()
        if connection:
            overlay = build_env_overlay(connection, ext_name)
            env.update(overlay)
            console.print(f"[dim]Using connection:[/dim] {connection.name} ({connection.url})")
            console.print(f"[dim]KAMIWAZA_USE_AUTH=false (local dev mode)[/dim]")
        else:
            console.print("[yellow]No Kamiwaza connection configured — running in standalone mode[/yellow]")

        # 6. Build command
        cmd = compose_cmd + ["-f", str(compose_file), "up", "--build"]
        if detach:
            cmd.append("-d")

        console.print(f"[dim]Running:[/dim] {' '.join(cmd)}")

        # 7. Run subprocess with signal forwarding
        return self._run_subprocess(cmd, env=env, cwd=str(ext_dir))

    # ------------------------------------------------------------------
    # Extension detection
    # ------------------------------------------------------------------

    def _find_extension(self) -> Path:
        cwd = Path.cwd()

        # Check root
        if (cwd / "kamiwaza.json").exists():
            return cwd

        # Check one level deep
        found = [d.parent for d in cwd.glob("*/kamiwaza.json")]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            dirs = ", ".join(str(d.name) for d in found)
            raise FileNotFoundError(
                f"Multiple kamiwaza.json found: {dirs}. Run from inside a specific extension directory."
            )

        raise FileNotFoundError(
            "No kamiwaza.json found. Run this in an extension directory or use `kz-ext create`."
        )

    def _read_extension_name(self, ext_dir: Path) -> str:
        try:
            with (ext_dir / "kamiwaza.json").open("r") as f:
                data = json.load(f)
            return data.get("name", ext_dir.name)
        except (json.JSONDecodeError, FileNotFoundError):
            return ext_dir.name

    def _find_compose_file(self, ext_dir: Path) -> Path:
        for name in COMPOSE_FILENAMES:
            candidate = ext_dir / name
            if candidate.exists():
                return candidate
        raise FileNotFoundError(
            f"No compose file found in {ext_dir}. Expected one of: {', '.join(COMPOSE_FILENAMES)}"
        )

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
    return {
        "KAMIWAZA_API_URL": url,
        "KAMIWAZA_PUBLIC_API_URL": url.removesuffix("/api"),
        "KAMIWAZA_ENDPOINT": f"{url}/v1" if not url.endswith("/v1") else url,
        "KAMIWAZA_USE_AUTH": "false",
        "KAMIWAZA_APP_NAME": extension_name,
    }


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
