"""DoctorChecker — environment diagnostics for kz-ext."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from kamiwaza_extensions import __version__
from kamiwaza_extensions.connections import ConnectionManager


@dataclass
class CheckResult:
    name: str
    status: Literal["pass", "fail", "warn"]
    message: str
    fix: Optional[str] = None


class DoctorChecker:
    """Runs diagnostic checks on the development environment."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self._conn_mgr = ConnectionManager(config_dir=config_dir)

    def run_all(self) -> List[CheckResult]:
        results: List[CheckResult] = []

        # System checks (always run)
        results.append(self._check_python_version())
        results.append(self._check_docker_installed())
        results.append(self._check_compose())
        results.append(self._check_docker_running())

        # Connection checks (if configured)
        results.append(self._check_connection())

        # Extension checks (if in extension directory)
        ext_results = self._check_extension_context()
        results.extend(ext_results)

        return results

    # ------------------------------------------------------------------
    # System checks
    # ------------------------------------------------------------------

    def _check_python_version(self) -> CheckResult:
        v = sys.version_info
        version_str = f"{v.major}.{v.minor}.{v.micro}"
        if v >= (3, 10):
            return CheckResult("Python version", "pass", f"{version_str}")
        return CheckResult(
            "Python version", "fail", f"{version_str} (requires >= 3.10)",
            fix="Install Python 3.10 or newer",
        )

    def _check_docker_installed(self) -> CheckResult:
        try:
            result = subprocess.run(
                ["docker", "--version"], capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip().split(",")[0]
                return CheckResult("Docker installed", "pass", version)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return CheckResult(
            "Docker installed", "fail", "Not found",
            fix="Install Docker Desktop: https://docs.docker.com/get-docker/",
        )

    def _check_compose(self) -> CheckResult:
        # Try v2
        try:
            result = subprocess.run(
                ["docker", "compose", "version"], capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return CheckResult("Docker Compose", "pass", result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # Try v1
        try:
            result = subprocess.run(
                ["docker-compose", "--version"], capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return CheckResult("Docker Compose", "pass", result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return CheckResult(
            "Docker Compose", "fail", "Not found",
            fix="Install Docker Desktop (includes Compose v2) or pip install docker-compose",
        )

    def _check_docker_running(self) -> CheckResult:
        try:
            result = subprocess.run(
                ["docker", "info"], capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return CheckResult("Docker running", "pass", "Docker daemon is running")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return CheckResult(
            "Docker running", "fail", "Docker daemon not responding",
            fix="Start Docker Desktop or run 'sudo systemctl start docker'",
        )

    # ------------------------------------------------------------------
    # Connection checks
    # ------------------------------------------------------------------

    def _check_connection(self) -> CheckResult:
        conn = self._conn_mgr.get_active_connection()
        if conn is None:
            return CheckResult(
                "Kamiwaza connection", "warn", "No connection configured",
                fix="Run 'kz-ext login <url>' to connect",
            )
        token = self._conn_mgr.get_token()
        if token is None:
            return CheckResult(
                "Kamiwaza connection", "warn",
                f"Connection '{conn.name}' has no token",
                fix=f"Run 'kz-ext login {conn.url}' to re-authenticate",
            )
        # Try health endpoint
        try:
            import requests
            resp = requests.get(
                f"{conn.url}/api/health",
                headers={"Authorization": f"Bearer {token.access_token}"},
                timeout=5,
            )
            if resp.ok:
                return CheckResult("Kamiwaza connection", "pass", f"{conn.name} ({conn.url})")
            return CheckResult(
                "Kamiwaza connection", "warn",
                f"{conn.name}: API returned {resp.status_code}",
                fix=f"Check if {conn.url} is reachable",
            )
        except Exception:
            return CheckResult(
                "Kamiwaza connection", "warn",
                f"{conn.name}: unreachable ({conn.url})",
                fix="Check network connectivity or VPN",
            )

    # ------------------------------------------------------------------
    # Extension context checks
    # ------------------------------------------------------------------

    def _check_extension_context(self) -> List[CheckResult]:
        results: List[CheckResult] = []
        cwd = Path.cwd()

        metadata_path = cwd / "kamiwaza.json"
        if not metadata_path.exists():
            # Check one level deep
            found = list(cwd.glob("*/kamiwaza.json"))
            if found:
                metadata_path = found[0]
            else:
                return results  # Not in an extension dir — skip extension checks

        # CLI version compatibility
        try:
            with metadata_path.open() as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return results

        kz_ext_version = data.get("kz_ext_version")
        if kz_ext_version:
            results.append(self._check_cli_version(kz_ext_version))

        ext_dir = metadata_path.parent

        # Python runtime lib
        req_file = ext_dir / "requirements.txt"
        if not req_file.exists():
            req_file = ext_dir / "backend" / "requirements.txt"
        if req_file.exists():
            results.append(self._check_python_runtime_lib(req_file))

        # TypeScript runtime lib
        pkg_file = ext_dir / "package.json"
        if not pkg_file.exists():
            pkg_file = ext_dir / "frontend" / "package.json"
        if pkg_file.exists():
            results.append(self._check_ts_runtime_lib(pkg_file))

        return results

    def _check_cli_version(self, specifier_str: str) -> CheckResult:
        try:
            spec = SpecifierSet(specifier_str)
            ver = Version(__version__)
            if ver in spec:
                return CheckResult("CLI version compatibility", "pass", f"{__version__} matches {specifier_str}")
            return CheckResult(
                "CLI version compatibility", "fail",
                f"{__version__} does not match {specifier_str}",
                fix=f"Install a compatible version: pip install 'kamiwaza-extensions{specifier_str}'",
            )
        except (InvalidSpecifier, InvalidVersion) as exc:
            return CheckResult(
                "CLI version compatibility", "warn",
                f"Could not parse kz_ext_version: {exc}",
            )

    def _check_python_runtime_lib(self, req_file: Path) -> CheckResult:
        content = req_file.read_text()
        if "kamiwaza-extensions-lib" in content:
            return CheckResult("Runtime lib (Python)", "pass", "kamiwaza-extensions-lib found in requirements.txt")
        return CheckResult(
            "Runtime lib (Python)", "warn",
            "kamiwaza-extensions-lib not found in requirements.txt",
            fix="Add 'kamiwaza-extensions-lib>=0.1.0' to requirements.txt",
        )

    def _check_ts_runtime_lib(self, pkg_file: Path) -> CheckResult:
        try:
            with pkg_file.open() as f:
                data = json.load(f)
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "@kamiwaza-ai/extensions-lib" in deps:
                return CheckResult("Runtime lib (TypeScript)", "pass", "@kamiwaza-ai/extensions-lib found in package.json")
        except (json.JSONDecodeError, FileNotFoundError):
            pass
        return CheckResult(
            "Runtime lib (TypeScript)", "warn",
            "@kamiwaza-ai/extensions-lib not found in package.json",
            fix='Add "@kamiwaza-ai/extensions-lib": "^0.2.0" to package.json dependencies',
        )
