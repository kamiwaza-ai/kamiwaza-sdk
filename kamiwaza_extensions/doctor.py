"""DoctorChecker — environment diagnostics for kz-ext."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import List, Literal, Optional

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from kamiwaza_extensions import __version__
from kamiwaza_extensions.connections import ConnectionManager


@lru_cache(maxsize=1)
def _uac_9d_hints() -> list[dict]:
    """Load UAC-9d class hints from the runtime lib's canonical JSON."""
    data = json.loads(
        resources.files("kamiwaza_extensions_lib")
        .joinpath("exception_names.json")
        .read_text(encoding="utf-8")
    )
    return list(data["classes"])


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

        # SDK override checks (if configured)
        sdk_results = self._check_sdk_override()
        results.extend(sdk_results)

        # UAC-9d reference hints (always emitted)
        results.extend(self._uac_9d_reference_hints())

        return results

    # ------------------------------------------------------------------
    # UAC-9d reference hints
    # ------------------------------------------------------------------

    def _uac_9d_reference_hints(self) -> List[CheckResult]:
        """Reference entries for each UAC-9d runtime-lib exception class.

        These are always-pass informational entries; they surface the
        canonical doctor_hint so extension authors seeing an exception
        in their logs can find the matching diagnosis + fix in
        ``kz-ext doctor`` output (§4.2.8 DoctorUACFailureHints).
        """
        return [
            CheckResult(
                name=f"UAC-9d: {entry['name']}",
                status="pass",
                message=entry["doctor_hint"],
                fix=entry["doctor_hint"],
            )
            for entry in _uac_9d_hints()
        ]

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
        # Try health endpoints — platform may expose different ones
        import requests
        for path in ("/auth/ping", "/auth/health", "/health"):
            try:
                resp = requests.get(
                    f"{conn.url}{path}",
                    headers={"Authorization": f"Bearer {token.access_token}"},
                    timeout=5,
                    verify=conn.verify_ssl,
                )
                if resp.ok:
                    return CheckResult("Kamiwaza connection", "pass", f"{conn.name} ({conn.url})")
            except requests.ConnectionError:
                # Server unreachable — no point trying more paths on same host
                return CheckResult(
                    "Kamiwaza connection", "warn",
                    f"{conn.name}: unreachable ({conn.url})",
                    fix="Check network connectivity or VPN",
                )
            except requests.RequestException:
                continue

        # All endpoints responded but none returned 200
        return CheckResult(
            "Kamiwaza connection", "warn",
            f"{conn.name}: no health endpoint found",
            fix=f"Server is reachable but health check failed",
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
                fix=f"Install a compatible version: pip install 'kamiwaza-sdk{specifier_str}'",
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

    # ------------------------------------------------------------------
    # SDK override checks
    # ------------------------------------------------------------------

    def _check_sdk_override(self) -> List[CheckResult]:
        """Check SDK override configuration if present."""
        from kamiwaza_extensions.sdk_override import (
            resolve_sdk_override,
            validate_sdk_override,
        )

        results: List[CheckResult] = []
        cwd = Path.cwd()

        spec = resolve_sdk_override(None, cwd)
        if spec is None:
            return results  # No override configured — skip

        results.append(CheckResult(
            "SDK override config", "pass",
            f"Loaded from .kz-ext/local.yaml (sdk_repo: {spec.sdk_repo})",
        ))

        validation = validate_sdk_override(spec)

        # SDK repo exists
        if spec.sdk_repo.is_dir():
            results.append(CheckResult("SDK repo exists", "pass", str(spec.sdk_repo)))
        else:
            results.append(CheckResult(
                "SDK repo exists", "fail", f"Not found: {spec.sdk_repo}",
                fix=f"Check path in .kz-ext/local.yaml",
            ))
            return results  # Can't check further

        # Python lib
        if spec.python:
            if spec.python_lib_path.is_dir():
                results.append(CheckResult(
                    "SDK Python lib", "pass",
                    "kamiwaza_extensions_lib/ found",
                ))
            else:
                results.append(CheckResult(
                    "SDK Python lib", "fail",
                    "kamiwaza_extensions_lib/ not found in SDK repo",
                ))

        # TypeScript lib
        if spec.typescript:
            if spec.typescript_lib_path.is_dir():
                results.append(CheckResult(
                    "SDK TypeScript lib", "pass",
                    "kamiwaza-ai-extensions-lib/ found",
                ))
            else:
                results.append(CheckResult(
                    "SDK TypeScript lib", "fail",
                    "kamiwaza-ai-extensions-lib/ not found in SDK repo",
                ))

            # dist/ check
            if spec.typescript_lib_path.is_dir():
                if spec.typescript_dist_path.is_dir():
                    # Check staleness via validation warnings
                    stale = any("stale" in w for w in validation.warnings)
                    if stale:
                        results.append(CheckResult(
                            "SDK TypeScript dist/", "warn",
                            "dist/ exists but may be stale (src/ is newer)",
                            fix=f"cd {spec.typescript_lib_path} && npm run build",
                        ))
                    else:
                        results.append(CheckResult(
                            "SDK TypeScript dist/", "pass", "Built and up to date",
                        ))
                else:
                    results.append(CheckResult(
                        "SDK TypeScript dist/", "warn",
                        "dist/ not found — will be built on first run",
                        fix=f"cd {spec.typescript_lib_path} && npm install && npm run build",
                    ))

        # Template contract check
        ext_dir = cwd
        metadata_path = ext_dir / "kamiwaza.json"
        if not metadata_path.exists():
            found = list(ext_dir.glob("*/kamiwaza.json"))
            if found:
                ext_dir = found[0].parent

        contract_ok = True
        req_file = ext_dir / "backend" / "requirements.txt"
        if not req_file.exists():
            req_file = ext_dir / "requirements.txt"
        if req_file.exists():
            content = req_file.read_text()
            if "kamiwaza-extensions-lib" not in content:
                contract_ok = False

        pkg_file = ext_dir / "frontend" / "package.json"
        if not pkg_file.exists():
            pkg_file = ext_dir / "package.json"
        if pkg_file.exists():
            try:
                with pkg_file.open() as f:
                    data = json.load(f)
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "@kamiwaza-ai/extensions-lib" not in deps:
                    contract_ok = False
            except (json.JSONDecodeError, FileNotFoundError):
                pass

        if contract_ok:
            results.append(CheckResult(
                "SDK override contract", "pass",
                "requirements.txt and package.json reference runtime libs",
            ))
        else:
            results.append(CheckResult(
                "SDK override contract", "warn",
                "Non-standard install — SDK override may need manual configuration",
                fix="Ensure requirements.txt has kamiwaza-extensions-lib and "
                    "package.json has @kamiwaza-ai/extensions-lib",
            ))

        return results
