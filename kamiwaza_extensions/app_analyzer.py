"""App analyzer — gather context from existing apps for conversion."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import yaml

from kamiwaza_extensions import __version__
from kamiwaza_extensions.constants import COMPOSE_FILENAMES
from kamiwaza_extensions.monorepo import (
    MONOREPO_BARE_DIRS,
    MONOREPO_PARENT_DIRS,
    SKIP_DIRS,
)

_SKIP_DIRS = SKIP_DIRS
_DOCKERFILE_NEGATIVE_SUFFIXES = (
    ".template",
    ".tmpl",
    ".tpl",
    ".bak",
    ".example",
    ".sample",
)
_VENDORABLE_ARTIFACT_SUFFIXES = (
    ".whl",
    ".tgz",
    ".tar.gz",
    ".zip",
    ".jar",
    ".pex",
)
_MAX_MONOREPO_INVENTORY_ENTRIES = 200
_MANIFEST_FILES = {
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "uv.lock",
    "vite.config.js",
    "vite.config.ts",
    "webpack.config.js",
    "webpack.config.ts",
    "README.md",
    "nginx.conf",
    "Caddyfile",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
}
_ENTRYPOINT_NAMES = {
    "main.py",
    "app.py",
    "server.py",
    "index.html",
    "index.js",
    "index.ts",
    "main.js",
    "main.ts",
}
_CONTEXT_SUFFIXES = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".conf",
)
_MAX_REPO_TREE_ENTRIES = 40
_MAX_CONTEXT_FILES = 80
_MAX_CONTEXT_FILE_SIZE = 10000
_SENSITIVE_FILE_NAMES = {
    ".env",
    ".envrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "credential.json",
    "secret.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}
_SENSITIVE_FILE_SUFFIXES = (
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".der",
)
_SENSITIVE_STEMS = {"secret", "secrets", "credential", "credentials"}


# Skip list for the monorepo-inventory pass. Strict subset of
# ``SKIP_DIRS`` — deliberately omits ``dist``, ``build``, ``target``,
# and ``coverage`` because that's exactly where shared publish
# artifacts (.whl, .tgz, .jar, etc.) live in monorepos. The whole
# point of the inventory pass is to surface those for the LLM's
# ``copy`` action; pruning them silently breaks the vendoring flow.
_INVENTORY_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".next",
        ".agents",
        ".claude",
        ".cursor",
        ".aider",
        ".specstory",
        ".idea",
        ".vscode",
        ".devcontainer",
    }
)


def _walk_files(
    root: Path,
    extensions: tuple[str, ...] | None = None,
    *,
    skip_dirs: frozenset[str] | set[str] | None = None,
) -> Generator[Path, None, None]:
    """Walk *root* yielding files, pruning ``skip_dirs`` in-place.

    Defaults to ``_SKIP_DIRS`` (which prunes ``dist``/``build``/etc. for
    speed on JS projects with large ``node_modules``). Pass an
    explicit ``skip_dirs`` to override — e.g. the monorepo-inventory
    pass uses ``_INVENTORY_SKIP_DIRS`` because vendorable artifacts
    live precisely in the otherwise-skipped ``dist`` directories.
    """
    effective_skip = skip_dirs if skip_dirs is not None else _SKIP_DIRS
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in-place so os.walk doesn't descend
        dirnames[:] = [d for d in dirnames if d not in effective_skip]
        for fname in filenames:
            if extensions is None or any(fname.endswith(ext) for ext in extensions):
                yield Path(dirpath) / fname


class AmbiguousMonorepoError(Exception):
    """Raised when multiple monorepo subdirectories contain extension candidates.

    The caller is expected to report the candidates and ask the user to
    re-run with the specific subdirectory. ``candidates`` holds the absolute
    paths of every directory with a compose file under the searched
    monorepo conventions.
    """

    def __init__(self, candidates: List[Path]) -> None:
        self.candidates = candidates
        rendered = ", ".join(str(p) for p in candidates)
        super().__init__(f"Multiple extension candidates found: {rendered}")


@dataclass
class ServiceInfo:
    """Detected service from compose or Dockerfiles."""

    name: str
    dockerfile: Optional[Path] = None
    base_image: Optional[str] = None
    language: Optional[str] = None  # "python", "node", "go", etc.
    ports: List[int] = field(default_factory=list)
    has_build_context: bool = False
    build_context: Optional[str] = None


@dataclass
class AnalysisResult:
    """Results of analyzing an existing app."""

    # Discovered structure
    app_dir: Path
    app_name: str
    services: List[ServiceInfo] = field(default_factory=list)
    compose_path: Optional[Path] = None
    compose_data: Optional[Dict[str, Any]] = None
    # Set when monorepo detection rebased ``app_dir`` to a subdirectory.
    # ``rebased_from`` holds the original CLI-supplied path so the caller
    # can surface the rebase to the user.
    rebased_from: Optional[Path] = None
    # When rebased: a list of file paths (relative to ``rebased_from``)
    # that live *outside* ``app_dir`` and may be referenced by the LLM
    # via ``copy`` modifications (e.g. vendoring shared wheels/tarballs
    # from a monorepo's ``shared/`` directory).
    monorepo_inventory: List[str] = field(default_factory=list)
    # Subset of ``monorepo_inventory`` that look like vendor-able binary
    # artifacts (.whl, .tgz, .tar.gz, .zip, .jar). Surfaced separately so
    # the LLM can find them without reading file_contents.
    vendorable_artifacts: List[str] = field(default_factory=list)

    # Compatibility checks
    has_host_ports: List[str] = field(default_factory=list)
    has_bind_mounts: List[str] = field(default_factory=list)
    missing_resource_limits: List[str] = field(default_factory=list)
    has_health_endpoint: bool = False

    # SDK integration status
    has_python_runtime_lib: bool = False
    has_ts_runtime_lib: bool = False

    # File contents (for LLM context)
    file_contents: Dict[str, str] = field(default_factory=dict)
    repo_tree: List[str] = field(default_factory=list)
    detected_manifests: List[str] = field(default_factory=list)
    candidate_entrypoints: List[str] = field(default_factory=list)
    runtime_hints: List[str] = field(default_factory=list)

    # Description
    description: str = ""

    # Inferred type
    extension_type: str = "app"
    conversion_mode: str = "structured"


class AppAnalyzer:
    """Analyze an existing containerized app for Kamiwaza extension conversion."""

    def analyze(self, app_dir: Path) -> AnalysisResult:
        """Run full analysis on the given directory.

        Raises ``AmbiguousMonorepoError`` if multiple monorepo
        subdirectories contain extension candidates.
        """
        app_dir = Path(app_dir).resolve()
        if not app_dir.is_dir():
            raise FileNotFoundError(f"Directory not found: {app_dir}")

        effective_root, rebased_from = self._resolve_effective_root(app_dir)

        result = AnalysisResult(
            app_dir=effective_root,
            app_name=self._sanitize_name(effective_root.name),
            rebased_from=rebased_from,
        )

        self._find_compose(result)
        self._find_dockerfiles(result)
        self._check_deployment_compat(result)
        self._detect_sdk_integration(result)
        self._detect_health_endpoint(result)
        self._infer_description(result)
        self._infer_type(result)
        self._gather_repo_inventory(result)
        self._gather_file_contents(result)
        self._gather_monorepo_inventory(result)
        self._infer_conversion_mode(result)

        return result

    def generate_kamiwaza_json(self, result: AnalysisResult) -> Dict[str, Any]:
        """Generate kamiwaza.json content from analysis results."""
        major = __version__.split(".")[0]
        next_major = str(int(major) + 1)
        return {
            "name": result.app_name,
            "version": "0.1.0",
            "type": result.extension_type,
            "source_type": "user_repo",
            "visibility": "private",
            "description": result.description or f"A Kamiwaza {result.extension_type} extension",
            "risk_tier": 0,
            "verified": False,
            "kz_ext_version": f">={__version__},<{next_major}.0.0",
            "tags": [],
            "env_defaults": {},
            "required_env_vars": [],
        }

    # ------------------------------------------------------------------
    # Private analysis steps
    # ------------------------------------------------------------------

    def _resolve_effective_root(self, app_dir: Path) -> tuple[Path, Optional[Path]]:
        """Locate the directory the analyzer should treat as the extension root.

        Returns ``(effective_root, rebased_from)``. ``rebased_from`` is
        ``None`` when no rebase happened (compose at the user-supplied
        path, or no compose anywhere). When set it holds the original
        path so the CLI can tell the user we descended into a subdir.

        Raises ``AmbiguousMonorepoError`` when multiple monorepo
        subdirectories contain compose files.
        """
        # If the user-supplied path itself looks like an extension
        # root (compose, kamiwaza.json, Dockerfile, manifest), don't
        # rebase — they pointed at the right place.
        if _looks_like_extension_root(app_dir):
            return app_dir, None

        candidates: List[Path] = []
        # Two-level pattern: <parent>/<name>/<extension-signal>
        for parent_name in MONOREPO_PARENT_DIRS:
            parent = app_dir / parent_name
            if not parent.is_dir():
                continue
            for child in sorted(parent.iterdir()):
                if child.is_dir() and _looks_like_extension_root(child):
                    candidates.append(child)
        # One-level bare-dir pattern: <name>/<extension-signal>
        for bare_name in MONOREPO_BARE_DIRS:
            candidate = app_dir / bare_name
            if candidate.is_dir() and _looks_like_extension_root(candidate):
                candidates.append(candidate)

        if not candidates:
            return app_dir, None
        if len(candidates) > 1:
            raise AmbiguousMonorepoError(candidates)
        return candidates[0], app_dir

    def _find_compose(self, result: AnalysisResult) -> None:
        for name in COMPOSE_FILENAMES:
            path = result.app_dir / name
            if path.exists():
                result.compose_path = path
                try:
                    result.compose_data = yaml.safe_load(path.read_text(encoding="utf-8"))
                except (yaml.YAMLError, OSError):
                    pass
                break

    def _find_dockerfiles(self, result: AnalysisResult) -> None:
        # From compose services first
        if result.compose_data:
            for svc_name, svc in (result.compose_data.get("services") or {}).items():
                info = ServiceInfo(name=svc_name)

                # Build context
                build = svc.get("build")
                if isinstance(build, str):
                    info.has_build_context = True
                    info.build_context = build
                    df = result.app_dir / build / "Dockerfile"
                    if df.exists():
                        info.dockerfile = df
                elif isinstance(build, dict):
                    info.has_build_context = True
                    ctx = build.get("context", ".")
                    info.build_context = ctx
                    df_name = build.get("dockerfile", "Dockerfile")
                    df = result.app_dir / ctx / df_name
                    if df.exists():
                        info.dockerfile = df

                # Ports
                for port_spec in svc.get("ports", []):
                    port_str = str(port_spec)
                    # Extract container port
                    parts = port_str.split(":")
                    try:
                        info.ports.append(int(parts[-1]))
                    except ValueError:
                        pass

                # Detect language from Dockerfile
                if info.dockerfile and info.dockerfile.exists():
                    info.base_image, info.language = self._detect_language(info.dockerfile)

                result.services.append(info)
            return

        # Fallback: find Dockerfiles in subdirectories
        for dockerfile in sorted(
            path for path in _walk_files(result.app_dir) if _is_dockerfile(path)
        ):
            parent = dockerfile.parent
            svc_name = parent.name if parent != result.app_dir else "app"
            base_image, language = self._detect_language(dockerfile)
            result.services.append(
                ServiceInfo(
                    name=svc_name,
                    dockerfile=dockerfile,
                    base_image=base_image,
                    language=language,
                    has_build_context=True,
                    build_context=str(parent.relative_to(result.app_dir)),
                )
            )

    def _detect_language(self, dockerfile: Path) -> tuple[Optional[str], Optional[str]]:
        try:
            content = dockerfile.read_text(encoding="utf-8")
        except OSError:
            return None, None

        # Use the LAST FROM instruction (final stage in multi-stage builds)
        last_image = None
        for line in content.splitlines():
            line = line.strip()
            if line.upper().startswith("FROM "):
                last_image = line.split()[1].lower()

        if last_image is None:
            return None, None

        # Extract the base image name (before : tag) for matching.
        # e.g., "python:3.11-slim" → "python", "ghcr.io/org/my-python:1" → "my-python"
        base = last_image.split(":")[0].rsplit("/", 1)[-1]

        if "python" in base:
            return last_image, "python"
        if "node" in base or "bun" in base:
            return last_image, "node"
        if base in ("golang", "go") or base.startswith("golang"):
            return last_image, "go"
        if "rust" in base:
            return last_image, "rust"
        if "ruby" in base:
            return last_image, "ruby"
        return last_image, None

    def _check_deployment_compat(self, result: AnalysisResult) -> None:
        if not result.compose_data:
            return

        for svc_name, svc in (result.compose_data.get("services") or {}).items():
            # Host ports
            for port_spec in svc.get("ports", []):
                port_str = str(port_spec)
                if ":" in port_str:
                    result.has_host_ports.append(f"{svc_name}: {port_str}")

            # Bind mounts
            for vol in svc.get("volumes", []):
                vol_str = str(vol)
                if vol_str.startswith("./") or vol_str.startswith("/") or vol_str.startswith("../"):
                    result.has_bind_mounts.append(f"{svc_name}: {vol_str}")

            # Resource limits
            deploy = svc.get("deploy", {})
            resources = deploy.get("resources", {})
            if not resources.get("limits"):
                result.missing_resource_limits.append(svc_name)

    def _detect_sdk_integration(self, result: AnalysisResult) -> None:
        # Python runtime lib
        for req_file in _walk_files(result.app_dir, ("requirements.txt",)):
            try:
                content = req_file.read_text(encoding="utf-8")
                if "kamiwaza-extensions-lib" in content or "kamiwaza_extensions_lib" in content:
                    result.has_python_runtime_lib = True
                    break
            except OSError:
                pass

        # TypeScript runtime lib
        for pkg_file in _walk_files(result.app_dir, ("package.json",)):
            try:
                content = pkg_file.read_text(encoding="utf-8")
                if "@kamiwaza-ai/extensions-lib" in content:
                    result.has_ts_runtime_lib = True
                    break
            except OSError:
                pass

    def _detect_health_endpoint(self, result: AnalysisResult) -> None:
        # Look for /health endpoint in Python or JS files
        patterns = [
            re.compile(r"""['"]/health['"]"""),
            re.compile(r"""@app\.(get|route)\s*\(\s*['"]/health"""),
            re.compile(r"""router\.(get|route)\s*\(\s*['"]/health"""),
        ]
        for src in _walk_files(result.app_dir, (".py", ".js", ".ts", ".jsx", ".tsx")):
            try:
                content = src.read_text(encoding="utf-8")
                for pat in patterns:
                    if pat.search(content):
                        result.has_health_endpoint = True
                        return
            except OSError:
                pass

    def _infer_description(self, result: AnalysisResult) -> None:
        readme = result.app_dir / "README.md"
        if readme.exists():
            try:
                lines = readme.read_text(encoding="utf-8").splitlines()
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        result.description = line[:200]
                        return
                    if line.startswith("# "):
                        result.description = line.lstrip("# ").strip()[:200]
                        return
            except OSError:
                pass

    def _infer_type(self, result: AnalysisResult) -> None:
        name = result.app_name.lower()
        if name.startswith("tool-") or name.startswith("mcp-"):
            result.extension_type = "tool"
        elif name.startswith("service-"):
            result.extension_type = "service"
        else:
            # Check for MCP patterns
            for svc in result.services:
                if svc.dockerfile and svc.dockerfile.exists():
                    try:
                        content = svc.dockerfile.read_text(encoding="utf-8")
                        if "mcp" in content.lower() or "FastMCP" in content:
                            result.extension_type = "tool"
                            return
                    except OSError:
                        pass
            result.extension_type = "app"

    def _gather_repo_inventory(self, result: AnalysisResult) -> None:
        """Collect lightweight repo structure hints for generic conversion."""
        entries = []
        for entry in sorted(result.app_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if entry.name in _SKIP_DIRS:
                continue
            if entry.is_file() and _is_sensitive_context_file(entry):
                continue
            label = f"{entry.name}/" if entry.is_dir() else entry.name
            entries.append(label)
            if len(entries) >= _MAX_REPO_TREE_ENTRIES:
                break
        result.repo_tree = entries

        manifests: List[str] = []
        entrypoints: List[str] = []
        runtime_hints: set[str] = set()

        for path in _walk_files(result.app_dir):
            if _is_sensitive_context_file(path):
                continue
            rel = str(path.relative_to(result.app_dir))
            name = path.name

            if _is_manifest(path):
                manifests.append(rel)
            if _is_entrypoint(path):
                entrypoints.append(rel)

            suffix = path.suffix.lower()
            if suffix == ".html":
                runtime_hints.add("static-html")
            elif suffix in (".js", ".jsx", ".ts", ".tsx"):
                runtime_hints.add("javascript-or-typescript")
            elif suffix == ".py":
                runtime_hints.add("python")

            if name == "package.json":
                runtime_hints.add("node-package")
            elif name in {"requirements.txt", "pyproject.toml", "Pipfile"}:
                runtime_hints.add("python-package")
            elif name == "nginx.conf":
                runtime_hints.add("nginx")
            elif name == "Caddyfile":
                runtime_hints.add("caddy")
            elif _is_dockerfile(path):
                runtime_hints.add("dockerized")

        for svc in result.services:
            if svc.language:
                runtime_hints.add(f"{svc.language}-service")
            if svc.base_image and any(token in svc.base_image for token in ("nginx", "caddy", "httpd")):
                runtime_hints.add("static-web-server")

        result.detected_manifests = manifests[:_MAX_CONTEXT_FILES]
        result.candidate_entrypoints = entrypoints[:_MAX_CONTEXT_FILES]
        result.runtime_hints = sorted(runtime_hints)

    def _gather_monorepo_inventory(self, result: AnalysisResult) -> None:
        """List files in the broader source tree outside the rebased ext root.

        Only runs when monorepo detection rebased ``app_dir``. The result
        is exposed to the LLM so it can ``copy`` artifacts (e.g. shared
        wheels/tarballs) from elsewhere in the source tree into the
        extension directory.
        """
        if result.rebased_from is None:
            return

        source_root = result.rebased_from.resolve()
        ext_root = result.app_dir.resolve()

        inventory: List[str] = []
        artifacts: List[str] = []
        # Use the inventory-specific skip list so vendorable artifacts
        # in ``dist``/``build``/``target`` are surfaced to the LLM.
        for path in _walk_files(source_root, skip_dirs=_INVENTORY_SKIP_DIRS):
            try:
                if path.resolve().is_relative_to(ext_root):
                    continue
            except (OSError, ValueError):
                continue
            if _is_sensitive_context_file(path):
                continue
            try:
                rel = path.relative_to(source_root).as_posix()
            except ValueError:
                continue
            inventory.append(rel)
            name = path.name.lower()
            if any(name.endswith(suffix) for suffix in _VENDORABLE_ARTIFACT_SUFFIXES):
                artifacts.append(rel)
            if len(inventory) >= _MAX_MONOREPO_INVENTORY_ENTRIES:
                break

        result.monorepo_inventory = inventory
        result.vendorable_artifacts = artifacts

    def _gather_file_contents(self, result: AnalysisResult) -> None:
        """Read key files to provide as context for the LLM agent."""
        targets = []

        # Compose file
        if result.compose_path:
            targets.append(result.compose_path)

        # Dockerfiles
        for svc in result.services:
            if svc.dockerfile:
                targets.append(svc.dockerfile)

        for path in _walk_files(result.app_dir):
            if _is_context_file(path):
                targets.append(path)
            if len(targets) >= _MAX_CONTEXT_FILES:
                break

        # README
        readme = result.app_dir / "README.md"
        if readme.exists():
            targets.append(readme)

        # Deduplicate and read
        seen = set()
        for path in targets:
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            try:
                content = path.read_text(encoding="utf-8")
                rel = str(path.relative_to(result.app_dir))
                # Limit file size to avoid blowing up the LLM context
                if len(content) > _MAX_CONTEXT_FILE_SIZE:
                    content = content[:_MAX_CONTEXT_FILE_SIZE] + "\n... (truncated)"
                result.file_contents[rel] = content
            except (OSError, UnicodeDecodeError):
                pass

    def _infer_conversion_mode(self, result: AnalysisResult) -> None:
        if result.compose_path or result.services:
            result.conversion_mode = "structured"
            return
        if result.file_contents or result.detected_manifests or result.candidate_entrypoints:
            result.conversion_mode = "generic"
            return
        result.conversion_mode = "generic"

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitize directory name for use as extension name."""
        name = name.lower().strip()
        name = re.sub(r"[^a-z0-9-]", "-", name)
        name = re.sub(r"-+", "-", name)
        name = name.strip("-")
        return name or "my-extension"


# Files whose presence at a directory's top level marks that directory
# as "this looks like an extension/app root" for monorepo rebase. Order
# is approximate strength of signal; any one is enough.
_EXTENSION_SIGNAL_FILES = (
    *COMPOSE_FILENAMES,
    "kamiwaza.json",
    "Dockerfile",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "go.mod",
    "Cargo.toml",
)


def _has_compose_file(directory: Path) -> bool:
    return any((directory / name).exists() for name in COMPOSE_FILENAMES)


def _looks_like_extension_root(directory: Path) -> bool:
    """Return True when *directory* has any signal of being an
    extension/app root.

    Used by monorepo rebase to detect candidate subdirectories even in
    the greenfield case where ``docker-compose.yml`` is precisely what
    the conversion is going to *generate* (so it isn't there yet).
    Mirrors the kinds of signals a developer would point ``kz-ext
    convert`` at directly: existing kamiwaza.json, an existing
    Dockerfile, a Python/Node manifest, etc.
    """
    return any((directory / name).exists() for name in _EXTENSION_SIGNAL_FILES)


def _is_dockerfile(path: Path) -> bool:
    name = path.name
    if name.endswith(_DOCKERFILE_NEGATIVE_SUFFIXES):
        return False
    return name == "Dockerfile" or name.startswith("Dockerfile.")


def _is_manifest(path: Path) -> bool:
    return path.name in _MANIFEST_FILES


def _is_entrypoint(path: Path) -> bool:
    if path.name in _ENTRYPOINT_NAMES:
        return True
    rel = path.as_posix()
    return rel.endswith("/src/index.ts") or rel.endswith("/src/index.js") or rel.endswith("/src/main.ts") or rel.endswith("/src/main.js")


def _is_context_file(path: Path) -> bool:
    if _is_sensitive_context_file(path):
        return False
    if _is_dockerfile(path) or _is_manifest(path) or _is_entrypoint(path):
        return True
    return path.suffix.lower() in _CONTEXT_SUFFIXES


def _is_sensitive_context_file(path: Path) -> bool:
    name = path.name.lower()
    if name in _SENSITIVE_FILE_NAMES or name.startswith(".env."):
        return True
    if path.suffix.lower() in _SENSITIVE_FILE_SUFFIXES:
        return True
    return path.stem.lower() in _SENSITIVE_STEMS
