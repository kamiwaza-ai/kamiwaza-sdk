"""SDK override — resolve local kamiwaza-sdk source for dev builds."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from rich.console import Console

console = Console(stderr=True)

_DEFAULT_BACKEND_STARTUP = [
    "uvicorn",
    "app.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
    "--reload",
]
_DEFAULT_FRONTEND_STARTUP = ["npm", "start"]


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


_PYTHON_LIB_COPY = 'export PYTHONPATH="/sdk${PYTHONPATH:+:$PYTHONPATH}"'

_TS_LIB_INSTALL = (
    "TARBALL=$(cd /sdk/kamiwaza-ai-extensions-lib"
    " && npm pack --ignore-scripts --pack-destination /tmp 2>/dev/null | tail -1)"
    ' && cd /app && npm install --ignore-scripts "/tmp/$TARBALL"'
)


def _escape_compose_shell_vars(command: str) -> str:
    """Escape shell variable syntax so Docker Compose preserves it.

    Compose interpolates ``$VAR`` and ``${VAR}`` when it parses YAML. Local
    SDK override commands intentionally rely on those variables being expanded
    later inside the container shell, so they must be emitted as ``$$...``.
    """
    return command.replace("$", "$$")


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


def detect_service_runtime(
    svc_name: str,
    svc_config: dict,
    *,
    extension_dir: Optional[Path] = None,
) -> str:
    """Classify a service runtime for SDK override purposes.

    Returns one of:
    - ``frontend`` for likely Node/Next-style services
    - ``backend`` for likely Python/backend services
    - ``static`` for generic web servers such as nginx/caddy/httpd

    When the Dockerfile is available, prefer its final base image over naming
    heuristics so converted static apps do not get a Python SDK overlay.
    """
    dockerfile = None
    if extension_dir is not None and "build" in svc_config:
        dockerfile = _resolve_dockerfile(svc_config["build"], extension_dir)

    base_image = _read_final_base_image(dockerfile) if dockerfile else None
    if base_image:
        base = base_image.split(":")[0].rsplit("/", 1)[-1]
        if "python" in base:
            return "backend"
        if "node" in base or "bun" in base:
            return "frontend"
        if any(token in base for token in ("nginx", "caddy", "httpd", "apache")):
            return "static"

    return detect_service_type(svc_name, svc_config)


def _detect_build_service_runtime(
    svc_name: str,
    svc_config: dict,
    *,
    extension_dir: Optional[Path] = None,
) -> str:
    """Classify a service for build-time SDK overlay insertion.

    Multi-stage frontends often compile in a Node/Bun stage and ship from a
    static final image such as nginx. Those should still receive the
    TypeScript overlay during ``kz-ext dev --sdk-repo`` because the local SDK
    must be installed before the frontend bundle is built.
    """
    dockerfile = None
    if extension_dir is not None and "build" in svc_config:
        dockerfile = _resolve_dockerfile(svc_config["build"], extension_dir)

    stage_bases = _read_dockerfile_stage_bases(dockerfile)
    if not stage_bases:
        return detect_service_runtime(
            svc_name, svc_config, extension_dir=extension_dir,
        )

    final_base = _image_basename(stage_bases[-1])
    if "python" in final_base:
        return "backend"
    if "node" in final_base or "bun" in final_base:
        return "frontend"
    if any(token in final_base for token in ("nginx", "caddy", "httpd", "apache")):
        if any(
            "node" in _image_basename(base) or "bun" in _image_basename(base)
            for base in stage_bases[:-1]
        ):
            return "frontend"
        return "static"

    return detect_service_runtime(
        svc_name, svc_config, extension_dir=extension_dir,
    )


def _resolve_dockerfile(build_spec: Any, extension_dir: Path) -> Optional[Path]:
    """Resolve the Dockerfile path from a compose build spec."""
    if isinstance(build_spec, str):
        return extension_dir / build_spec / "Dockerfile"
    elif isinstance(build_spec, dict):
        ctx = build_spec.get("context", ".")
        context = extension_dir / ctx
        df = build_spec.get("dockerfile", "Dockerfile")
        return Path(df) if Path(df).is_absolute() else context / df
    return None


@dataclass
class DockerStartup:
    """Resolved Dockerfile startup instructions."""

    entrypoint: Optional[List[str]] = None
    cmd: Optional[List[str]] = None


def _parse_docker_command(instruction: str) -> List[str]:
    """Parse a Docker CMD/ENTRYPOINT instruction into argv."""
    if instruction.startswith("["):
        try:
            parts = json.loads(instruction)
        except (json.JSONDecodeError, TypeError):
            return ["/bin/sh", "-c", instruction]
        if isinstance(parts, list):
            return [str(part) for part in parts]
    return ["/bin/sh", "-c", instruction]


def _read_dockerfile_startup(dockerfile: Path) -> Optional[DockerStartup]:
    """Read CMD and ENTRYPOINT startup instructions from a Dockerfile."""
    if not dockerfile.is_file():
        return None
    try:
        content = dockerfile.read_text()
    except OSError:
        return None

    startup = DockerStartup()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("ENTRYPOINT "):
            startup.entrypoint = _parse_docker_command(
                stripped[len("ENTRYPOINT "):].strip()
            )
        elif stripped.startswith("CMD "):
            startup.cmd = _parse_docker_command(stripped[len("CMD "):].strip())

    if not startup.entrypoint and not startup.cmd:
        return None
    return startup


def _read_final_base_image(dockerfile: Optional[Path]) -> Optional[str]:
    """Return the final FROM image name from a Dockerfile, if readable."""
    stage_bases = _read_dockerfile_stage_bases(dockerfile)
    if not stage_bases:
        return None
    return stage_bases[-1]


def _read_dockerfile_stage_bases(dockerfile: Optional[Path]) -> List[str]:
    """Return effective base images for each Dockerfile stage."""
    from kamiwaza_extensions.validators.platform_runtime import parse_from_instruction

    if not dockerfile or not dockerfile.is_file():
        return []

    try:
        content = dockerfile.read_text()
    except OSError:
        return []

    stages: List[tuple[str, Optional[str]]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            base_ref, alias = parse_from_instruction(stripped)
            if base_ref:
                base = base_ref.lower()
                stages.append((base, alias.lower() if alias else None))

    if not stages:
        return []

    alias_map = {
        alias: index for index, (_base, alias) in enumerate(stages) if alias
    }

    def resolve_stage_base(index: int, seen: Optional[set[str]] = None) -> str:
        base_ref = stages[index][0]
        key = base_ref.lower()
        alias_idx = alias_map.get(key)
        seen = seen or set()
        if alias_idx is None or key in seen:
            return base_ref
        seen.add(key)
        return resolve_stage_base(alias_idx, seen)

    return [resolve_stage_base(index) for index in range(len(stages))]


def _image_basename(image_ref: str) -> str:
    ref = image_ref.split("@", 1)[0]
    name = ref.rsplit("/", 1)[-1]
    return name.split(":", 1)[0].lower()


def _startup_argv(
    startup: Optional[DockerStartup],
    fallback: List[str],
) -> List[str]:
    """Return the argv Docker would execute for startup."""
    if not startup:
        return fallback
    if startup.entrypoint:
        return startup.entrypoint + (startup.cmd or [])
    if startup.cmd:
        return startup.cmd
    return fallback


def generate_compose_override(
    spec: SdkOverrideSpec,
    compose_data: dict,
    extension_dir: Optional[Path] = None,
) -> dict:
    """Generate a compose override dict for local SDK development.

    Adds volume mounts and install command overrides so containers use
    the local SDK runtime libraries instead of published packages.
    Only overrides services that have a ``build`` key (skips pre-built
    images like redis/postgres).

    When *extension_dir* is provided, reads each service's Dockerfile to
    determine the correct startup command.  Docker Compose clears the
    image's CMD when ``entrypoint`` is overridden, so the original startup
    command must be provided explicitly.
    """
    override_services: dict = {}
    services = compose_data.get("services", {})

    for svc_name, svc_config in services.items():
        # Skip services without a build context (pre-built images)
        if "build" not in svc_config:
            continue

        svc_type = detect_service_runtime(
            svc_name, svc_config, extension_dir=extension_dir,
        )
        svc_override: dict = {}

        # Read the original startup command from the Dockerfile
        startup = None
        if extension_dir:
            df = _resolve_dockerfile(svc_config["build"], extension_dir)
            if df:
                startup = _read_dockerfile_startup(df)

        if svc_type == "backend" and spec.python:
            svc_override["volumes"] = [{
                "type": "bind",
                "source": str(spec.sdk_repo),
                "target": "/sdk",
                "read_only": True,
            }]
            exec_cmd = shlex.join(
                _startup_argv(startup, _DEFAULT_BACKEND_STARTUP)
            )
            svc_override["entrypoint"] = ["/bin/sh", "-c"]
            svc_override["command"] = [
                _escape_compose_shell_vars(_PYTHON_LIB_COPY) + f" && exec {exec_cmd}"
            ]

        elif svc_type == "frontend" and spec.typescript:
            svc_override["volumes"] = [{
                "type": "bind",
                "source": str(spec.sdk_repo),
                "target": "/sdk",
                "read_only": True,
            }]
            exec_cmd = shlex.join(
                _startup_argv(startup, _DEFAULT_FRONTEND_STARTUP)
            )
            svc_override["entrypoint"] = ["/bin/sh", "-c"]
            svc_override["command"] = [
                _escape_compose_shell_vars(_TS_LIB_INSTALL) + f" && exec {exec_cmd}"
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
    "{restore_user_block}"
)

_TS_OVERLAY = (
    "# --- SDK override: install local TypeScript runtime lib ---\n"
    "USER root\n"
    "COPY --from=sdk kamiwaza-ai-extensions-lib /tmp/kamiwaza-ai-extensions-lib\n"
    "RUN TARBALL=$(cd /tmp/kamiwaza-ai-extensions-lib"
    " && npm pack --ignore-scripts --pack-destination /tmp 2>/dev/null | tail -1)"
    ' && cd /app && npm install --ignore-scripts "/tmp/$TARBALL"'
    " && rm -rf /tmp/kamiwaza-ai-extensions-lib*\n"
    "{restore_user_block}"
)

# Patterns that indicate the TS lib must be installed BEFORE this line
_TS_BUILD_PATTERNS = re.compile(
    r"^\s*RUN\s+.*(?:npm\s+run\s+build|next\s+build|yarn\s+build)", re.IGNORECASE
)


def generate_build_overrides(
    spec: SdkOverrideSpec,
    compose_data: dict,
    extension_dir: Optional[Path] = None,
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

        svc_type = _detect_build_service_runtime(
            svc_name, svc_config, extension_dir=extension_dir,
        )

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
    lines = dockerfile_content.splitlines(keepends=True)
    insert_idx = None

    if overlay.insert_before_build:
        for i, line in enumerate(lines):
            if _TS_BUILD_PATTERNS.match(line):
                insert_idx = i
                break

    user_scope = lines[:insert_idx] if insert_idx is not None else lines
    overlay_steps = overlay.overlay_steps.replace(
        "{restore_user_block}",
        _restore_user_block(_find_active_user(user_scope)),
    )

    if insert_idx is not None:
        return (
            "".join(lines[:insert_idx])
            + "\n"
            + overlay_steps
            + "\n"
            + "".join(lines[insert_idx:])
        )

    # No build line found or not insert_before_build — append at end
    return dockerfile_content.rstrip() + "\n\n" + overlay_steps


def _find_active_user(lines: List[str]) -> Optional[str]:
    """Return the last USER declared in the given Dockerfile lines."""
    active_user = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("USER "):
            user = stripped[len("USER "):].strip()
            if user:
                active_user = user
    return active_user


def _restore_user_block(user: Optional[str]) -> str:
    """Render a USER restore directive when the Dockerfile was non-root."""
    if not user or user.lower() == "root":
        return ""
    return f"USER {user}\n"


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
        console.print(
            "  [dim]Python lib:[/dim]  [green]local[/green] "
            "(kamiwaza_extensions_lib/)"
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
