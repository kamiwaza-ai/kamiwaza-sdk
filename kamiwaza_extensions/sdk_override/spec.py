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
    def python_client_path(self) -> Path:
        return self.sdk_repo / "kamiwaza_sdk"

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
    if cli_sdk_repo:
        return SdkOverrideSpec(sdk_repo=Path(cli_sdk_repo).expanduser().resolve())

    config_path = extension_path / _CONFIG_DIR / _CONFIG_FILE
    config = _read_override_config(config_path)
    if config is None:
        return None

    sdk_repo = _configured_sdk_repo(config, config_path)
    if sdk_repo is None:
        return None
    libs = config.get("runtime_libs", {})
    return SdkOverrideSpec(
        sdk_repo=sdk_repo,
        python=libs.get("python", "local") == "local",
        typescript=libs.get("typescript", "local") == "local",
        build_typescript=bool(config.get("build_typescript", False)),
    )


def _read_override_config(config_path: Path) -> Optional[dict]:
    if not config_path.is_file():
        return None
    try:
        with open(config_path) as config_file:
            return yaml.safe_load(config_file) or {}
    except (yaml.YAMLError, OSError):
        return None


def _configured_sdk_repo(config: dict, config_path: Path) -> Optional[Path]:
    raw_repo = config.get("sdk_repo")
    if not raw_repo:
        return None
    repo_path = Path(raw_repo).expanduser()
    base_path = config_path.parent if not repo_path.is_absolute() else Path()
    return (base_path / repo_path).resolve()


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
    repository_error = _repository_error(spec)
    if repository_error:
        result.errors.append(repository_error)
        return result

    if spec.python:
        result.errors.extend(_python_package_errors(spec))

    if spec.typescript:
        _validate_typescript_package(spec, result)

    return result


def _repository_error(spec: SdkOverrideSpec) -> Optional[str]:
    if not spec.sdk_repo.is_dir():
        return f"SDK repo not found: {spec.sdk_repo}"
    if "=" in str(spec.sdk_repo):
        return (
            "SDK repo path contains '=' which is incompatible with Docker "
            f"--build-context: {spec.sdk_repo}"
        )
    return None


def _python_package_errors(spec: SdkOverrideSpec) -> List[str]:
    packages = (
        ("Python SDK client", spec.python_client_path),
        ("Python runtime lib", spec.python_lib_path),
    )
    return [
        f"{label} not found: {path}" for label, path in packages if not path.is_dir()
    ]


def _validate_typescript_package(
    spec: SdkOverrideSpec, result: ValidationResult
) -> None:
    if not spec.typescript_lib_path.is_dir():
        result.errors.append(
            f"TypeScript runtime lib not found: {spec.typescript_lib_path}"
        )
        return
    if not spec.typescript_dist_path.is_dir():
        result.warnings.append(
            f"TypeScript dist/ missing — run: cd {spec.typescript_lib_path} && npm run build"
        )
        return
    if is_typescript_dist_stale(spec):
        result.warnings.append(
            "TypeScript dist/ is stale (src/ is newer) — will rebuild before bind-mount"
        )


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

    _print_python_diagnostics(spec, _os.environ.get("KZ_SDK_PYTHONPATH_APPEND", ""))
    _print_typescript_diagnostics(spec)

    console.print()


def _print_python_diagnostics(spec: SdkOverrideSpec, extra_path: str) -> None:
    if not spec.python:
        console.print("  [dim]Python libs:[/dim] published")
        return
    console.print(
        "  [dim]Python libs:[/dim] [green]local[/green] "
        "(kamiwaza_sdk/, kamiwaza_extensions_lib/)"
    )
    extra = extra_path.strip()
    if extra:
        console.print(
            f"  [dim]PYTHONPATH:[/dim] /sdk : <Dockerfile/compose baked> : {extra} "
            "[dim](Dockerfile + compose PYTHONPATH preserved; "
            "KZ_SDK_PYTHONPATH_APPEND adds extra paths)[/dim]"
        )
        return
    console.print(
        "  [dim]PYTHONPATH:[/dim] /sdk : <Dockerfile/compose baked, if any> "
        "[dim](Dockerfile + compose PYTHONPATH preserved automatically)[/dim]"
    )


def _print_typescript_diagnostics(spec: SdkOverrideSpec) -> None:
    if not spec.typescript:
        console.print("  [dim]TS lib:[/dim]      published")
        return
    ts_status = (
        "ok" if spec.typescript_dist_path.is_dir() else "[yellow]missing[/yellow]"
    )
    console.print(
        "  [dim]TS lib:[/dim]      [green]local[/green] (kamiwaza-ai-extensions-lib/)"
    )
    console.print(f"  [dim]TS dist/:[/dim]    {ts_status}")
