"""SDK override config: resolution, validation, dist-rebuild, diagnostics."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml
from rich.console import Console

console = Console(stderr=True)

_CONFIG_DIR = ".kz-ext"
_CONFIG_FILE = "local.yaml"


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
            # Resolve relative to the config file's directory, not CWD
            repo_path = Path(raw_repo).expanduser()
            if not repo_path.is_absolute():
                repo_path = (config_path.parent / repo_path).resolve()
            else:
                repo_path = repo_path.resolve()
            sdk_repo = repo_path

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
        result.errors.append(f"Python runtime lib not found: {spec.python_lib_path}")

    if spec.typescript:
        if not spec.typescript_lib_path.is_dir():
            result.errors.append(
                f"TypeScript runtime lib not found: {spec.typescript_lib_path}"
            )
        elif not spec.typescript_dist_path.is_dir():
            result.warnings.append(
                f"TypeScript dist/ missing — run: cd {spec.typescript_lib_path} && npm run build"
            )
        elif is_typescript_dist_stale(spec):
            result.warnings.append(
                "TypeScript dist/ is stale (src/ is newer) — will rebuild before bind-mount"
            )

    return result


def is_typescript_dist_stale(spec: SdkOverrideSpec) -> bool:
    """Return True when ``dist/`` exists but is older than ``src/``.

    Used as a trigger for ``build_typescript_lib`` in the ``dev local`` /
    ``dev`` flows. Treats stale-dist the same as missing-dist: if the
    developer asked us to bind-mount their local SDK, they want fresh
    artifacts — anything else turns into a baffling "module not found"
    at the consumer (e.g. dropped subpath exports between releases —
    PR #87 → ``dist/local-dev-auth/`` was added to ``package.json``
    but the dist/ wasn't rebuilt before merge).
    """
    if not spec.typescript_dist_path.is_dir():
        return False
    src_dir = spec.typescript_lib_path / "src"
    if not src_dir.is_dir():
        return False
    return _newest_mtime(src_dir) > _newest_mtime(spec.typescript_dist_path)


def _newest_mtime(directory: Path) -> float:
    """Return the newest mtime of any file under *directory*."""
    newest = 0.0
    for f in directory.rglob("*"):
        if f.is_file():
            newest = max(newest, f.stat().st_mtime)
    return newest


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


def print_override_diagnostics(spec: SdkOverrideSpec) -> None:
    """Print which SDK overrides are active."""
    import os as _os

    console.print()
    console.print("[bold]SDK Override Active:[/bold]")
    console.print(f"  [dim]SDK repo:[/dim]    {spec.sdk_repo}")

    if spec.python:
        console.print(
            "  [dim]Python lib:[/dim]  [green]local[/green] "
            "(kamiwaza_extensions_lib/)"
        )
        # PYTHONPATH composition: /sdk (always first) + any
        # PYTHONPATH baked into the Dockerfile (auto-detected per
        # service at compose-build time) + KZ_SDK_PYTHONPATH_APPEND
        # for paths beyond what's in the Dockerfile.
        extra = _os.environ.get("KZ_SDK_PYTHONPATH_APPEND", "").strip()
        if extra:
            console.print(
                f"  [dim]PYTHONPATH:[/dim] /sdk : <Dockerfile baked> : {extra} "
                "[dim](image-baked PYTHONPATH preserved; "
                "KZ_SDK_PYTHONPATH_APPEND adds extra paths)[/dim]"
            )
        else:
            console.print(
                "  [dim]PYTHONPATH:[/dim] /sdk : <Dockerfile baked, if any> "
                "[dim](image-baked PYTHONPATH preserved automatically)[/dim]"
            )
    else:
        console.print("  [dim]Python lib:[/dim]  published")

    if spec.typescript:
        ts_status = "ok"
        if not spec.typescript_dist_path.is_dir():
            ts_status = "[yellow]missing[/yellow]"
        console.print(
            "  [dim]TS lib:[/dim]      [green]local[/green] "
            "(kamiwaza-ai-extensions-lib/)"
        )
        console.print(f"  [dim]TS dist/:[/dim]    {ts_status}")
    else:
        console.print("  [dim]TS lib:[/dim]      published")

    console.print()
