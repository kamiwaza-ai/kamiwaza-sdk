"""Service classification — figure out if a compose service is a Python
backend, a Node frontend, or a static web server.

Two passes:

- ``detect_service_runtime`` (single-stage view) — what does the
  *runtime* image look like? An ``nginx`` runtime is "static" even if a
  Node stage built the bundle. Used by the runtime SDK overlay (which
  only injects into the actual runtime).
- ``_detect_build_service_runtime`` (multi-stage view) — does *any*
  stage smell like Node? A multi-stage frontend that compiles in Node
  and ships from nginx is still a "frontend" for build-time SDK
  injection because the build stage is the one that needs the local
  TypeScript lib.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional


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

    # Check ports — long-form dict carries the container port under
    # ``target``. Mirrors ``_should_use_node_frontend_probe``.
    for port_spec in svc_config.get("ports", []):
        if isinstance(port_spec, dict):
            target = port_spec.get("target")
            try:
                container_port = int(target) if target is not None else None
            except (ValueError, TypeError):
                container_port = None
            if container_port in (3000, 3001):
                return "frontend"
            continue
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


def read_runtime_pythonpath(dockerfile: Optional[Path]) -> Optional[str]:
    """Extract the runtime stage's ``ENV PYTHONPATH`` value, if any.

    Returns the literal value of the LAST ``ENV PYTHONPATH=...``
    declaration in the FINAL Dockerfile stage (i.e., the runtime
    stage of a multi-stage build, or the only stage of a single-stage
    Dockerfile). Returns ``None`` when no PYTHONPATH is baked or the
    Dockerfile is unreadable.

    Used by ``compose._python_override`` to preserve image-baked
    ``PYTHONPATH`` entries (src-layout apps with ``ENV
    PYTHONPATH=/app/src`` would otherwise lose access to their own
    modules under ``--sdk-repo``). The SDK is prepended so its
    ``import kamiwaza_extensions_lib`` wins over any same-name in the
    image's PYTHONPATH.
    """
    import re

    if not dockerfile or not dockerfile.is_file():
        return None
    try:
        content = dockerfile.read_text()
    except OSError:
        return None

    lines = content.splitlines()
    # Locate the start of the final stage by finding the last FROM.
    runtime_start = 0
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("FROM "):
            runtime_start = i + 1

    # Match ENV PYTHONPATH=value or ENV PYTHONPATH=value [other=...].
    # Handle quoted and unquoted values, and the legacy
    # ``ENV PYTHONPATH /val`` (space-separated) form.
    env_kv = re.compile(
        r"""^\s*ENV\s+
            (?:.*?\s+)?              # allow other ENV pairs before ours
            PYTHONPATH
            (?:\s*=\s*|\s+)          # = or space (legacy)
            ("?)([^"\s]+)\1          # quoted-or-bare value
        """,
        re.IGNORECASE | re.VERBOSE,
    )
    last_value: Optional[str] = None
    for line in lines[runtime_start:]:
        m = env_kv.match(line)
        if m:
            last_value = m.group(2)
    return last_value


def _image_basename(image_ref: str) -> str:
    ref = image_ref.split("@", 1)[0]
    name = ref.rsplit("/", 1)[-1]
    return name.split(":", 1)[0].lower()
