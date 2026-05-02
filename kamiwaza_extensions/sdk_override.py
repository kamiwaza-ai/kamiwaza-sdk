"""SDK override — resolve local kamiwaza-sdk source for dev builds."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# In-container path the SDK repo is bind-mounted at. Adding this to
# PYTHONPATH lets ``import kamiwaza_extensions_lib`` resolve to the
# local checkout without rebuilding the image.
_SDK_BIND_TARGET = "/sdk"

# In-container path the TypeScript SDK package gets bind-mounted to.
# Shadowing ``/app/node_modules/@kamiwaza-ai/extensions-lib`` with the
# SDK repo's package directory lets the existing app code resolve the
# local sources via standard Node module resolution. No npm install
# at runtime, no shell required — works on Chainguard distroless
# runtime images that lack ``/bin/sh`` and ``npm``.
_TS_LIB_PACKAGE_DIR = "kamiwaza-ai-extensions-lib"
_TS_LIB_NODE_MODULES_TARGET = "/app/node_modules/@kamiwaza-ai/extensions-lib"


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
            svc_name,
            svc_config,
            extension_dir=extension_dir,
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
        svc_name,
        svc_config,
        extension_dir=extension_dir,
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

    alias_map = {alias: index for index, (_base, alias) in enumerate(stages) if alias}

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


def generate_local_build_dockerfile_patches(
    spec: SdkOverrideSpec,
    compose_data: dict,
    extension_dir: Path,
) -> Dict[str, str]:
    """Per-service patched Dockerfile content for the ``dev local`` build phase.

    Returns ``{service_name: patched_dockerfile_source}`` for every backend
    (Python) and frontend (TypeScript/Node) service whose Dockerfile
    contains a recognizable install line. The caller is responsible for
    plumbing each value into a compose override (via
    ``build.dockerfile`` pointing at a temp file).

    Required because the runtime overlay (``generate_compose_override``)
    can only kick in once the image exists. When the scaffold's
    runtime-lib pin is not yet published on the language's package
    registry (PyPI / npm), the build's install step fails before any
    runtime overlay runs, leaving the developer with a hard build error
    and no path forward. The strip step inserted here makes each install
    succeed without the pin; the runtime overlay then surfaces the local
    source.

    Returns an empty dict when no service has a recognizable install line
    (e.g. a poetry-based custom Dockerfile, or a Node image that bakes
    deps differently — those users are responsible for their own
    runtime-lib install).
    """
    patches: Dict[str, str] = {}
    for svc_name, svc_config in compose_data.get("services", {}).items():
        if "build" not in svc_config:
            continue
        # Use the multi-stage-aware classifier (mirrors the cluster-deploy
        # ``generate_build_overrides`` path) so a multi-stage
        # ``FROM node … AS builder; FROM nginx:alpine`` frontend still
        # receives the TS strip — its final base is ``nginx`` (which
        # ``detect_service_runtime`` would tag as ``static``) but its
        # builder stage is the one running ``npm ci`` against the
        # unpublished pin (PR #91 round-3 reviewer H2 / Codex).
        svc_runtime = _detect_build_service_runtime(
            svc_name, svc_config, extension_dir=extension_dir
        )
        if svc_runtime == "backend" and spec.python:
            pattern = _PYTHON_PIP_INSTALL_PATTERN
            strip_steps = _PYTHON_PRE_INSTALL_STRIP
        elif svc_runtime == "frontend" and spec.typescript:
            pattern = _TS_NPM_INSTALL_PATTERN
            strip_steps = _TS_PRE_INSTALL_STRIP
        else:
            continue
        df_path = _resolve_dockerfile(svc_config["build"], extension_dir)
        if df_path is None or not df_path.exists():
            continue
        original = df_path.read_text()
        patched = _insert_before_install_pattern(original, strip_steps, pattern)
        if patched != original:
            # Mirror the cluster-deploy ``apply_build_overlay`` behavior:
            # if the matched install line uses ``npm ci``, rewrite it to
            # ``npm install`` so the package.json/lockfile divergence the
            # strip creates doesn't abort the build (PR #91 round-3 H1 /
            # Codex P2 — applies to local-dev path too, not just cluster
            # deploy).
            if pattern is _TS_NPM_INSTALL_PATTERN:
                patched = _TS_NPM_CI_LINE_PATTERN.sub(
                    r"\1npm install", patched
                )
            patches[svc_name] = patched
    return patches


def generate_compose_override(
    spec: SdkOverrideSpec,
    compose_data: dict,
    extension_dir: Optional[Path] = None,
) -> dict:
    """Generate a compose override dict for local SDK development.

    Surfaces the local SDK to each service via shell-free mechanisms so
    the override works against any runtime image — including Chainguard
    distroless variants (``cgr.dev/kamiwaza/python``,
    ``cgr.dev/kamiwaza/node``) that have no ``/bin/sh``, ``apt``, or
    ``npm`` in the runtime stage.

    Mechanism per service type:

    - **Backend (Python)**: bind-mount the SDK repo at ``/sdk`` and set
      ``PYTHONPATH=/sdk`` via compose ``environment``. The existing
      Dockerfile entrypoint is left untouched and inherits the env var.
    - **Frontend (TypeScript)**: bind-mount the SDK's package directory
      directly into ``/app/node_modules/@kamiwaza-ai/extensions-lib``,
      shadowing whatever the build phase installed. Standard Node
      module resolution picks up the local source — no runtime install,
      no shell.

    Only overrides services that have a ``build`` key (pre-built images
    like redis/postgres are skipped).

    *extension_dir* is no longer required for correctness, but is still
    accepted for compatibility with callers that pass it.
    """
    override_services: dict = {}
    services = compose_data.get("services", {})

    for svc_name, svc_config in services.items():
        # Skip services without a build context (pre-built images)
        if "build" not in svc_config:
            continue

        svc_type = detect_service_runtime(
            svc_name,
            svc_config,
            extension_dir=extension_dir,
        )
        svc_override: dict = {}

        if svc_type == "backend" and spec.python:
            svc_override["volumes"] = [
                {
                    "type": "bind",
                    "source": str(spec.sdk_repo),
                    "target": _SDK_BIND_TARGET,
                    "read_only": True,
                }
            ]
            # Set PYTHONPATH so the running interpreter picks up the
            # local SDK without touching the entrypoint. This overwrites
            # any image-baked PYTHONPATH; that is intentional for
            # ``--sdk-repo`` mode (developers asking to use the local
            # SDK want the local SDK to win unconditionally).
            svc_override["environment"] = {"PYTHONPATH": _SDK_BIND_TARGET}

        elif svc_type == "frontend" and spec.typescript:
            ts_pkg_source = spec.sdk_repo / _TS_LIB_PACKAGE_DIR
            svc_override["volumes"] = [
                {
                    "type": "bind",
                    "source": str(ts_pkg_source),
                    "target": _TS_LIB_NODE_MODULES_TARGET,
                    "read_only": True,
                }
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
    pre_install_steps: str = ""  # Inserted before RUN pip install -r requirements.txt
    # Which install pattern ``apply_build_overlay`` should match for the
    # ``pre_install_steps`` insert: "python" → ``RUN pip install -r
    # requirements.txt``, "typescript" → ``RUN npm install`` / ``RUN npm
    # ci``. None preserves the legacy "try both, Python first" behavior
    # for callers that don't set it (Codex P2 review on PR #91 round-4 —
    # without explicit language tagging, a frontend Dockerfile that also
    # runs ``pip install`` for build tooling would have the TS strip
    # inserted before the pip line, which is a no-op there because
    # ``package.json`` hasn't been copied yet).
    language: Optional[str] = None


_PYTHON_OVERLAY = (
    "# --- SDK override: install local Python runtime lib ---\n"
    "USER root\n"
    "COPY --from=sdk kamiwaza_extensions_lib /tmp/kamiwaza_extensions_lib\n"
    # Resolve the site-packages dir via ``sysconfig`` rather than by
    # importing ``kamiwaza_extensions_lib``. The pre-install strip
    # (above) removed the lib from requirements.txt, so it's NOT
    # installed via pip — importing it would crash here. ``purelib``
    # is the canonical pure-Python site-packages path for the current
    # interpreter and is always resolvable regardless of what's
    # installed. (ENG-3901 / F-002 round-3.)
    'RUN PURELIB=$(python -c "import sysconfig; print(sysconfig.get_paths()[\\"purelib\\"])")'
    ' && mkdir -p "$PURELIB"'
    ' && rm -rf "$PURELIB/kamiwaza_extensions_lib"'
    ' && cp -r /tmp/kamiwaza_extensions_lib "$PURELIB/"'
    " && rm -rf /tmp/kamiwaza_extensions_lib\n"
    "{restore_user_block}"
)

# Pattern that locates the standard scaffolded backend's pip install line so
# the pre-install strip step can be inserted before it.
_PYTHON_PIP_INSTALL_PATTERN = re.compile(
    # ``\b-r`` would not match because ``-`` is non-word and the preceding
    # space is also non-word, so the boundary doesn't apply. Use a literal
    # space instead. Trailing ``\b`` is fine — boundary between ``t`` and
    # newline / whitespace.
    r"^\s*RUN\s+.*\bpip\s+install\b.*\s-r\s+requirements\.txt\b",
    re.IGNORECASE,
)

# Pattern locating the frontend scaffold's npm install (``npm install`` or
# ``npm ci``). Matched per-line; ``RUN npm install`` and ``RUN npm ci`` are
# both accepted, optionally with flags before/after.
_TS_NPM_INSTALL_PATTERN = re.compile(
    r"^\s*RUN\s+.*\bnpm\s+(install|ci)\b", re.IGNORECASE
)

# Rewrites ``RUN ... npm ci ...`` to ``RUN ... npm install ...`` line-by-line.
# Required because the TS pre-install strip mutates ``package.json`` while
# leaving ``package-lock.json`` unchanged. ``npm ci`` enforces strict
# package.json ↔ lockfile parity and aborts on any divergence; ``npm install``
# consults the lockfile but tolerates mismatches and re-resolves. Local-build
# overrides already break strict lockfile reproducibility (we swap in a
# local source-built tarball at install time via ``_TS_OVERLAY``), so
# accepting looser install semantics is consistent and necessary
# (Codex P2 review on PR #91).
_TS_NPM_CI_LINE_PATTERN = re.compile(
    r"^(\s*RUN\s+.*\b)npm\s+ci\b", re.IGNORECASE | re.MULTILINE
)

# Drops the ``kamiwaza-extensions-lib`` pin from requirements.txt before pip
# install runs. The post-install ``_PYTHON_OVERLAY`` will copy the local
# source into site-packages, so removing the pin avoids a hard failure when
# the declared range isn't published yet (PR #89 dry-run finding F-002).
# Word-boundary check on the package name so prefix-aliases like
# ``kamiwaza-extensions-lib-extras`` are NOT stripped.
_PYTHON_PRE_INSTALL_STRIP = (
    "# --- SDK override: strip kamiwaza-extensions-lib from requirements.txt ---\n"
    "# The post-install overlay below copies the local runtime-lib source into\n"
    "# site-packages, so the PyPI install is redundant and would fail whenever\n"
    "# the pinned version is not yet published. See sdk_override.py docs.\n"
    "USER root\n"
    "RUN if [ -f requirements.txt ]; then"
    " sed -i -E '/^[[:space:]]*kamiwaza-extensions-lib($|[^A-Za-z0-9_-])/d'"
    " requirements.txt; fi\n"
    "{restore_user_block}"
)

# Drops ``@kamiwaza-ai/extensions-lib`` from every npm dependency-map
# field (the three documented dep maps + ``optionalDependencies``,
# ``bundleDependencies`` / ``bundledDependencies``, ``overrides``,
# ``resolutions``) in package.json before ``npm install`` runs. The
# post-install ``_TS_OVERLAY`` (or local-mode bind-mount + npm install)
# ships the runtime lib via ``npm pack``; removing the dep avoids a
# hard ETARGET failure when the declared version range isn't on the
# npm registry yet (mirror of ``_PYTHON_PRE_INSTALL_STRIP`` for the TS
# side).
#
# Implementation note: package.json is JSON, so a sed-line-strip would
# leave dangling commas. Use ``node -e`` (always present in the frontend
# image) to parse → mutate → write a structurally valid manifest.
_TS_PRE_INSTALL_STRIP = (
    "# --- SDK override: strip @kamiwaza-ai/extensions-lib from package.json ---\n"
    "# The post-install overlay below installs the local runtime-lib source\n"
    "# via ``npm pack``, so the registry install is redundant and would fail\n"
    "# whenever the pinned version is not yet published. Covers all five\n"
    "# dependency-map keys plus ``overrides`` / ``resolutions`` so a pin in\n"
    "# any of them won't survive the strip. Guarded with a file-exists check\n"
    "# so non-canonical Dockerfile layouts (no package.json at WORKDIR) fail\n"
    "# open instead of breaking the build.\n"
    "USER root\n"
    'RUN if [ -f package.json ]; then node -e "'
    "const fs=require('fs');"
    "const p=JSON.parse(fs.readFileSync('package.json','utf8'));"
    "const N='@kamiwaza-ai/extensions-lib';"
    "for(const k of ['dependencies','devDependencies','peerDependencies',"
    "'optionalDependencies','bundleDependencies','bundledDependencies',"
    "'overrides','resolutions'])"
    "{if(p[k]&&typeof p[k]==='object'&&!Array.isArray(p[k]))delete p[k][N];"
    " else if(Array.isArray(p[k]))p[k]=p[k].filter(x=>x!==N);}"
    "fs.writeFileSync('package.json', JSON.stringify(p,null,2)+'\\n');\";"
    " fi\n"
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
            svc_name,
            svc_config,
            extension_dir=extension_dir,
        )

        if svc_type == "backend" and spec.python:
            overrides.append(
                BuildOverride(
                    service_name=svc_name,
                    overlay_steps=_PYTHON_OVERLAY,
                    additional_build_contexts={"sdk": str(spec.sdk_repo)},
                    pre_install_steps=_PYTHON_PRE_INSTALL_STRIP,
                    language="python",
                )
            )

        elif svc_type == "frontend" and spec.typescript:
            overrides.append(
                BuildOverride(
                    service_name=svc_name,
                    overlay_steps=_TS_OVERLAY,
                    additional_build_contexts={"sdk": str(spec.sdk_repo)},
                    insert_before_build=True,
                    # Mirror the backend's pre-install strip: drop
                    # ``@kamiwaza-ai/extensions-lib`` from package.json
                    # before ``npm install`` runs, otherwise the build
                    # ETARGET-fails when the pinned version isn't on the
                    # npm registry yet (ENG-3901 / F-002 round-2 — cluster
                    # deploy hit the same wall as dev local).
                    pre_install_steps=_TS_PRE_INSTALL_STRIP,
                    language="typescript",
                )
            )

    return overrides


def apply_build_overlay(dockerfile_content: str, overlay: BuildOverride) -> str:
    """Apply a build overlay to Dockerfile content.

    Two insertion points, applied in order:

    1. ``pre_install_steps`` — inserted immediately before the first
       ``RUN ... pip install -r requirements.txt`` (Python) or
       ``RUN npm install`` (TypeScript) line, so the runtime-lib pin can
       be stripped before the install runs. No-op when the Dockerfile
       has no matching install line.
    2. ``overlay_steps`` — inserted before a frontend build line when
       ``insert_before_build`` is True; appended at end otherwise.
    """
    content = dockerfile_content
    if overlay.pre_install_steps:
        # Pick the install pattern based on the overlay's declared
        # language. A frontend Dockerfile that also runs ``pip install``
        # for build tooling would otherwise have the TS strip inserted
        # before the pip line (no-op, because package.json hasn't been
        # copied yet) and the actual ``npm install`` would still hit the
        # unstripped pin (Codex P2 review on PR #91 round-4).
        if overlay.language == "python":
            patterns: Tuple["re.Pattern[str]", ...] = (_PYTHON_PIP_INSTALL_PATTERN,)
        elif overlay.language == "typescript":
            patterns = (_TS_NPM_INSTALL_PATTERN,)
        else:
            # Legacy fallback for callers that don't declare a language —
            # try Python first, fall through to TS. Same behavior as
            # before round-4.
            patterns = (_PYTHON_PIP_INSTALL_PATTERN, _TS_NPM_INSTALL_PATTERN)
        for pattern in patterns:
            new_content = _insert_before_install_pattern(
                content, overlay.pre_install_steps, pattern
            )
            if new_content is not content:
                content = new_content
                # If the matched install line uses ``npm ci``, rewrite to
                # ``npm install`` so the lockfile mismatch the strip step
                # creates doesn't abort the build (Codex P2 review on
                # PR #91). Safe even when no ``npm ci`` line is present.
                if pattern is _TS_NPM_INSTALL_PATTERN:
                    content = _TS_NPM_CI_LINE_PATTERN.sub(
                        r"\1npm install", content
                    )
                break

    lines = content.splitlines(keepends=True)
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
    return content.rstrip() + "\n\n" + overlay_steps


def _insert_before_install_pattern(
    dockerfile_content: str,
    pre_steps: str,
    pattern: "re.Pattern[str]",
) -> str:
    """Insert ``pre_steps`` immediately before the first line matching
    ``pattern`` (e.g. ``RUN pip install -r requirements.txt`` or
    ``RUN npm install``). Returns the content unchanged when no such line
    is present — the user's Dockerfile is then responsible for runtime-lib
    install on its own, and the post-install overlay (if any) still
    appends as before."""
    lines = dockerfile_content.splitlines(keepends=True)
    insert_idx = None
    for i, line in enumerate(lines):
        if pattern.match(line):
            insert_idx = i
            break
    if insert_idx is None:
        return dockerfile_content
    pre_steps_resolved = pre_steps.replace(
        "{restore_user_block}",
        _restore_user_block(_find_active_user(lines[:insert_idx])),
    )
    # Ensure the inserted block starts on its own line and doesn't fuse with
    # the preceding directive.
    leading = (
        "" if not lines[:insert_idx] or lines[insert_idx - 1].endswith("\n") else "\n"
    )
    trailing = "" if pre_steps_resolved.endswith("\n") else "\n"
    return (
        "".join(lines[:insert_idx])
        + leading
        + pre_steps_resolved
        + trailing
        + "".join(lines[insert_idx:])
    )


def _find_active_user(lines: List[str]) -> Optional[str]:
    """Return the last USER declared in the given Dockerfile lines."""
    active_user = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("USER "):
            user = stripped[len("USER ") :].strip()
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
