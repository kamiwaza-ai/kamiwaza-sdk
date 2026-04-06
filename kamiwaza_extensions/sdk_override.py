"""SDK override — resolve local kamiwaza-sdk source for dev builds."""

from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from rich.console import Console

console = Console(stderr=True)


# ------------------------------------------------------------------
# Config model
# ------------------------------------------------------------------


@dataclass
class SdkOverrideSpec:
    """Resolved SDK override configuration."""

    sdk_repo: Path
    python: bool = True
    typescript: bool = True
    build_typescript: bool = False

    @property
    def python_lib_path(self) -> Path:
        return self.sdk_repo / "kamiwaza_extensions_lib"

    @property
    def typescript_lib_path(self) -> Path:
        return self.sdk_repo / "kamiwaza-ai-extensions-lib"

    @property
    def typescript_dist_path(self) -> Path:
        return self.typescript_lib_path / "dist"


# ------------------------------------------------------------------
# Config resolution
# ------------------------------------------------------------------

_CONFIG_DIR = ".kz-ext"
_CONFIG_FILE = "local.yaml"


def resolve_sdk_override(
    cli_sdk_repo: Optional[str],
    extension_path: Path,
) -> Optional[SdkOverrideSpec]:
    """Resolve SDK override from CLI flag or .kz-ext/local.yaml.

    CLI flag takes precedence over config file.
    Returns None if no override is configured.
    """
    sdk_repo: Optional[Path] = None
    python = True
    typescript = True
    build_typescript = False

    # 1. Try CLI flag first
    if cli_sdk_repo:
        sdk_repo = Path(cli_sdk_repo).expanduser().resolve()
    else:
        # 2. Try .kz-ext/local.yaml
        config_path = extension_path / _CONFIG_DIR / _CONFIG_FILE
        if config_path.is_file():
            try:
                with open(config_path) as f:
                    config = yaml.safe_load(f) or {}
            except (yaml.YAMLError, OSError):
                return None

            raw_repo = config.get("sdk_repo")
            if not raw_repo:
                return None
            sdk_repo = Path(raw_repo).expanduser().resolve()

            libs = config.get("runtime_libs", {})
            python = libs.get("python", "local") == "local"
            typescript = libs.get("typescript", "local") == "local"
            build_typescript = bool(config.get("build_typescript", False))

    if sdk_repo is None:
        return None

    return SdkOverrideSpec(
        sdk_repo=sdk_repo,
        python=python,
        typescript=typescript,
        build_typescript=build_typescript,
    )


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------


@dataclass
class ValidationResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def validate_sdk_override(spec: SdkOverrideSpec) -> ValidationResult:
    """Validate SDK repo structure. Returns errors and warnings."""
    result = ValidationResult()

    if not spec.sdk_repo.is_dir():
        result.errors.append(f"SDK repo not found: {spec.sdk_repo}")
        return result

    # H6: Reject paths with '=' which break Docker --build-context parsing
    if "=" in str(spec.sdk_repo):
        result.errors.append(
            f"SDK repo path contains '=' which is incompatible with Docker "
            f"--build-context: {spec.sdk_repo}"
        )
        return result

    if spec.python and not spec.python_lib_path.is_dir():
        result.errors.append(
            f"Python runtime lib not found: {spec.python_lib_path}"
        )

    if spec.typescript:
        if not spec.typescript_lib_path.is_dir():
            result.errors.append(
                f"TypeScript runtime lib not found: {spec.typescript_lib_path}"
            )
        elif not spec.typescript_dist_path.is_dir():
            result.warnings.append(
                f"TypeScript dist/ missing — run: cd {spec.typescript_lib_path} && npm run build"
            )
        else:
            # Check staleness: is dist/ older than src/?
            src_dir = spec.typescript_lib_path / "src"
            if src_dir.is_dir():
                dist_mtime = _newest_mtime(spec.typescript_dist_path)
                src_mtime = _newest_mtime(src_dir)
                if src_mtime > dist_mtime:
                    result.warnings.append(
                        "TypeScript dist/ may be stale (src/ is newer) — consider rebuilding"
                    )

    return result


def _newest_mtime(directory: Path) -> float:
    """Return the newest mtime of any file under *directory*."""
    newest = 0.0
    for f in directory.rglob("*"):
        if f.is_file():
            newest = max(newest, f.stat().st_mtime)
    return newest


# ------------------------------------------------------------------
# TypeScript build
# ------------------------------------------------------------------


def build_typescript_lib(spec: SdkOverrideSpec) -> bool:
    """Build the TypeScript runtime lib. Returns True on success."""
    ts_path = spec.typescript_lib_path
    console.print("[dim]Building TypeScript runtime lib...[/dim]")

    try:
        # npm install
        subprocess.run(
            ["npm", "install"],
            cwd=str(ts_path),
            check=True,
            capture_output=True,
            timeout=120,
        )
        # npm run build
        subprocess.run(
            ["npm", "run", "build"],
            cwd=str(ts_path),
            check=True,
            capture_output=True,
            timeout=120,
        )
        console.print("[green]  TypeScript dist/ ready[/green]")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        console.print(f"[red]  TypeScript build failed: {stderr[-200:]}[/red]")
        return False
    except subprocess.TimeoutExpired:
        console.print("[red]  TypeScript build timed out (120s)[/red]")
        return False
    except FileNotFoundError:
        console.print("[red]  npm not found — cannot build TypeScript lib[/red]")
        return False


# ------------------------------------------------------------------
# Compose override generation (local dev — volume mounts)
# ------------------------------------------------------------------


_PYTHON_LIB_COPY = (
    'SITE=$(python -c "import kamiwaza_extensions_lib as m, os;'
    ' print(os.path.dirname(m.__file__))")'
    ' && cp -r /sdk/kamiwaza_extensions_lib/* "$SITE/"'
)

_TS_LIB_INSTALL = (
    "TARBALL=$(cd /sdk/kamiwaza-ai-extensions-lib"
    " && npm pack --pack-destination /tmp 2>/dev/null | tail -1)"
    ' && cd /app && npm install "/tmp/$TARBALL"'
)


def detect_service_type(
    svc_name: str,
    svc_config: dict,
) -> str:
    """Heuristic: classify a compose service as 'frontend' or 'backend'.

    Uses service name, port, and Dockerfile path as signals.
    """
    name_lower = svc_name.lower()
    if "frontend" in name_lower or "ui" in name_lower or "web" in name_lower:
        return "frontend"

    # Check Dockerfile path
    build = svc_config.get("build", {})
    if isinstance(build, dict):
        dockerfile = build.get("dockerfile", "")
        context = build.get("context", "")
        if "frontend" in dockerfile.lower() or "frontend" in context.lower():
            return "frontend"

    # Check ports
    for port_spec in svc_config.get("ports", []):
        port_str = str(port_spec)
        if ":3000" in port_str or ":3001" in port_str:
            return "frontend"

    return "backend"


def generate_compose_override(
    spec: SdkOverrideSpec,
    compose_data: dict,
) -> dict:
    """Generate a compose override dict for local SDK development.

    Adds volume mounts and install command overrides so containers use
    the local SDK runtime libraries instead of published packages.
    Only overrides services that have a ``build`` key (skips pre-built
    images like redis/postgres).
    """
    override_services: dict = {}
    services = compose_data.get("services", {})

    for svc_name, svc_config in services.items():
        # Skip services without a build context (pre-built images)
        if "build" not in svc_config:
            continue

        svc_type = detect_service_type(svc_name, svc_config)
        svc_override: dict = {}

        # Volume mount: SDK repo → /sdk (read-only, long-form for path safety)
        svc_override["volumes"] = [{
            "type": "bind",
            "source": str(spec.sdk_repo),
            "target": "/sdk",
            "read_only": True,
        }]

        if svc_type == "backend" and spec.python:
            # Use exec "$@" to preserve the original Dockerfile CMD
            svc_override["entrypoint"] = [
                "/bin/sh", "-c",
                _PYTHON_LIB_COPY + ' && exec "$@"',
                "--",
            ]

        elif svc_type == "frontend" and spec.typescript:
            # Frontend templates use ENTRYPOINT (not CMD), so $@ may be empty.
            # Use `exec npm start` as a safe fallback for any Next.js app.
            svc_override["entrypoint"] = ["/bin/sh", "-c"]
            svc_override["command"] = [
                _TS_LIB_INSTALL + " && exec npm start"
            ]

        if svc_override:
            override_services[svc_name] = svc_override

    return {"services": override_services}


# ------------------------------------------------------------------
# Build override generation (remote deploy — bake into image)
# ------------------------------------------------------------------


@dataclass
class BuildOverride:
    """Override instructions for a single service's Docker build."""

    service_name: str
    overlay_steps: str  # Dockerfile lines appended/inserted into the original
    additional_build_contexts: Dict[str, str]
    insert_before_build: bool = False  # Insert before npm/next build line


_PYTHON_OVERLAY = (
    "# --- SDK override: install local Python runtime lib ---\n"
    "USER root\n"
    "COPY --from=sdk kamiwaza_extensions_lib /tmp/kamiwaza_extensions_lib\n"
    'RUN SITE=$(python -c "import kamiwaza_extensions_lib as m, os;'
    ' print(os.path.dirname(m.__file__))")'
    ' && rm -rf "$SITE"'
    ' && cp -r /tmp/kamiwaza_extensions_lib "$SITE"'
    " && rm -rf /tmp/kamiwaza_extensions_lib\n"
    "USER 1001\n"
)

_TS_OVERLAY = (
    "# --- SDK override: install local TypeScript runtime lib ---\n"
    "USER root\n"
    "COPY --from=sdk kamiwaza-ai-extensions-lib /tmp/kamiwaza-ai-extensions-lib\n"
    "RUN TARBALL=$(cd /tmp/kamiwaza-ai-extensions-lib"
    " && npm pack --pack-destination /tmp 2>/dev/null | tail -1)"
    ' && cd /app && npm install "/tmp/$TARBALL"'
    " && rm -rf /tmp/kamiwaza-ai-extensions-lib*\n"
    "USER 1001\n"
)

# Patterns that indicate the TS lib must be installed BEFORE this line
_TS_BUILD_PATTERNS = re.compile(
    r"^\s*RUN\s+.*(?:npm\s+run\s+build|next\s+build|yarn\s+build)", re.IGNORECASE
)


def generate_build_overrides(
    spec: SdkOverrideSpec,
    compose_data: dict,
) -> List[BuildOverride]:
    """Generate build overrides to bake local SDK source into images.

    For each service with a build context, produces Dockerfile overlay steps.
    For frontend services, if the Dockerfile contains a build step
    (``npm run build``, ``next build``), the overlay is inserted before that
    step so the local lib is compiled into the bundle.
    """
    overrides: List[BuildOverride] = []
    services = compose_data.get("services", {})

    for svc_name, svc_config in services.items():
        if "build" not in svc_config:
            continue

        svc_type = detect_service_type(svc_name, svc_config)

        if svc_type == "backend" and spec.python:
            overrides.append(BuildOverride(
                service_name=svc_name,
                overlay_steps=_PYTHON_OVERLAY,
                additional_build_contexts={"sdk": str(spec.sdk_repo)},
            ))

        elif svc_type == "frontend" and spec.typescript:
            overrides.append(BuildOverride(
                service_name=svc_name,
                overlay_steps=_TS_OVERLAY,
                additional_build_contexts={"sdk": str(spec.sdk_repo)},
                insert_before_build=True,
            ))

    return overrides


def apply_build_overlay(dockerfile_content: str, overlay: BuildOverride) -> str:
    """Apply a build overlay to Dockerfile content.

    If ``overlay.insert_before_build`` is True, scans for a ``RUN npm run build``
    (or similar) line and inserts the overlay before it. Otherwise appends.
    """
    if overlay.insert_before_build:
        lines = dockerfile_content.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if _TS_BUILD_PATTERNS.match(line):
                # Insert overlay before this line
                return "".join(lines[:i]) + "\n" + overlay.overlay_steps + "\n" + "".join(lines[i:])

    # No build line found or not insert_before_build — append at end
    return dockerfile_content.rstrip() + "\n\n" + overlay.overlay_steps


def check_buildkit_available() -> bool:
    """Check if Docker BuildKit is available (needed for --build-context)."""
    try:
        result = subprocess.run(
            ["docker", "buildx", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ------------------------------------------------------------------
# Diagnostics
# ------------------------------------------------------------------


def print_override_diagnostics(spec: SdkOverrideSpec) -> None:
    """Print which SDK overrides are active."""
    console.print()
    console.print("[bold]SDK Override Active:[/bold]")
    console.print(f"  [dim]SDK repo:[/dim]    {spec.sdk_repo}")

    if spec.python:
        console.print(f"  [dim]Python lib:[/dim]  [green]local[/green] (kamiwaza_extensions_lib/)")
    else:
        console.print(f"  [dim]Python lib:[/dim]  published")

    if spec.typescript:
        ts_status = "ok"
        if not spec.typescript_dist_path.is_dir():
            ts_status = "[yellow]missing[/yellow]"
        console.print(
            f"  [dim]TS lib:[/dim]      [green]local[/green] (kamiwaza-ai-extensions-lib/)"
        )
        console.print(f"  [dim]TS dist/:[/dim]    {ts_status}")
    else:
        console.print(f"  [dim]TS lib:[/dim]      published")

    console.print()
