"""App analyzer — gather context from existing apps for conversion."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from kamiwaza_extensions import __version__


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

    # Description
    description: str = ""

    # Inferred type
    extension_type: str = "app"


class AppAnalyzer:
    """Analyze an existing containerized app for Kamiwaza extension conversion."""

    def analyze(self, app_dir: Path) -> AnalysisResult:
        """Run full analysis on the given directory."""
        app_dir = Path(app_dir).resolve()
        if not app_dir.is_dir():
            raise FileNotFoundError(f"Directory not found: {app_dir}")

        result = AnalysisResult(
            app_dir=app_dir,
            app_name=self._sanitize_name(app_dir.name),
        )

        self._find_compose(result)
        self._find_dockerfiles(result)
        self._check_deployment_compat(result)
        self._detect_sdk_integration(result)
        self._detect_health_endpoint(result)
        self._infer_description(result)
        self._infer_type(result)
        self._gather_file_contents(result)

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

    def _find_compose(self, result: AnalysisResult) -> None:
        compose_names = (
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        )
        for name in compose_names:
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
        for dockerfile in sorted(result.app_dir.rglob("Dockerfile")):
            if ".git" in dockerfile.parts or "node_modules" in dockerfile.parts:
                continue
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

        # Find FROM instructions
        for line in content.splitlines():
            line = line.strip()
            if line.upper().startswith("FROM "):
                image = line.split()[1].lower()
                if "python" in image:
                    return image, "python"
                if "node" in image or "bun" in image:
                    return image, "node"
                if "golang" in image or "go" in image:
                    return image, "go"
                if "rust" in image:
                    return image, "rust"
                if "ruby" in image:
                    return image, "ruby"
                return image, None
        return None, None

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
        for req_file in result.app_dir.rglob("requirements.txt"):
            if ".git" in req_file.parts or "node_modules" in req_file.parts:
                continue
            try:
                content = req_file.read_text(encoding="utf-8")
                if "kamiwaza-extensions-lib" in content or "kamiwaza_extensions_lib" in content:
                    result.has_python_runtime_lib = True
                    break
            except OSError:
                pass

        # TypeScript runtime lib
        for pkg_file in result.app_dir.rglob("package.json"):
            if ".git" in pkg_file.parts or "node_modules" in pkg_file.parts:
                continue
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
        for ext in ("*.py", "*.js", "*.ts", "*.jsx", "*.tsx"):
            for src in result.app_dir.rglob(ext):
                if ".git" in src.parts or "node_modules" in src.parts:
                    continue
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

        # Key source files (main.py, app.py, server.py, index.ts, etc.)
        for pattern in [
            "*/main.py", "*/app.py", "*/server.py", "main.py", "app.py",
            "*/src/app/layout.tsx", "*/src/app/layout.jsx",
            "*/src/app/page.tsx", "*/src/index.ts", "*/src/index.js",
        ]:
            for f in result.app_dir.glob(pattern):
                if ".git" not in f.parts and "node_modules" not in f.parts:
                    targets.append(f)

        # Dependency files
        for pattern in ["*/requirements.txt", "requirements.txt", "*/package.json"]:
            for f in result.app_dir.glob(pattern):
                if ".git" not in f.parts and "node_modules" not in f.parts:
                    targets.append(f)

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
                if len(content) > 10000:
                    content = content[:10000] + "\n... (truncated)"
                result.file_contents[rel] = content
            except (OSError, UnicodeDecodeError):
                pass

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitize directory name for use as extension name."""
        name = name.lower().strip()
        name = re.sub(r"[^a-z0-9-]", "-", name)
        name = re.sub(r"-+", "-", name)
        name = name.strip("-")
        return name or "my-extension"
