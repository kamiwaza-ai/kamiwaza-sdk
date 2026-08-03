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

import re
from pathlib import Path
from typing import Any, List, Literal, Optional

ServiceRuntime = Literal["frontend", "backend", "static", "other"]

_PYTHON_IMAGE_TOKENS = ("python",)
_NODE_IMAGE_TOKENS = ("node", "bun")
_STATIC_IMAGE_TOKENS = ("nginx", "caddy", "httpd", "apache")
_PYTHONPATH_ENV_RE = re.compile(
    r"""^\s*ENV\s+
        (?:.*?\s+)?              # allow other ENV pairs before ours
        PYTHONPATH\s*=\s*
        ("?)([^"\s]+)\1          # quoted-or-bare value
    """,
    re.IGNORECASE | re.VERBOSE,
)


def detect_service_runtime(
    _svc_name: str,
    svc_config: dict,
    *,
    extension_dir: Optional[Path] = None,
) -> ServiceRuntime:
    """Classify a service runtime for SDK override purposes.

    Returns one of:
    - ``frontend`` for likely Node/Next-style services
    - ``backend`` for likely Python/backend services
    - ``static`` for generic web servers such as nginx/caddy/httpd
    - ``other`` for a readable Dockerfile with a non-SDK runtime

    When the Dockerfile is available, prefer its final base image over naming
    heuristics so converted static apps do not get a Python SDK overlay.
    """
    dockerfile = _service_dockerfile(svc_config, extension_dir)
    base_image = _read_final_base_image(dockerfile)
    return _classify_runtime_image(base_image) if base_image else "other"


def _detect_build_service_runtime(
    _svc_name: str,
    svc_config: dict,
    *,
    extension_dir: Optional[Path] = None,
) -> ServiceRuntime:
    """Classify a service for build-time SDK overlay insertion.

    Multi-stage frontends often compile in a Node/Bun stage and ship from a
    static final image such as nginx. Those should still receive the
    TypeScript overlay during ``kz-ext dev --sdk-repo`` because the local SDK
    must be installed before the frontend bundle is built.
    """
    dockerfile = _service_dockerfile(svc_config, extension_dir)
    stage_bases = _read_dockerfile_stage_bases(dockerfile)
    if not stage_bases:
        return "other"

    final_runtime = _classify_runtime_image(stage_bases[-1])
    if final_runtime == "static":
        if any(
            _classify_runtime_image(base) == "frontend" for base in stage_bases[:-1]
        ):
            return "frontend"
        return "static"

    return final_runtime


def _contains_token(value: str, tokens: tuple[str, ...]) -> bool:
    value_lower = value.lower()
    return any(token in value_lower for token in tokens)


def _service_dockerfile(
    svc_config: dict,
    extension_dir: Optional[Path],
) -> Optional[Path]:
    if extension_dir is None or "build" not in svc_config:
        return None
    return _resolve_dockerfile(svc_config["build"], extension_dir)


def _classify_runtime_image(image_ref: str) -> ServiceRuntime:
    image_name = _image_basename(image_ref)
    if _contains_token(image_name, _PYTHON_IMAGE_TOKENS):
        return "backend"
    if _contains_token(image_name, _NODE_IMAGE_TOKENS):
        return "frontend"
    if _contains_token(image_name, _STATIC_IMAGE_TOKENS):
        return "static"
    return "other"


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
    content = _read_dockerfile(dockerfile)
    if content is None:
        return []

    stages = _parse_dockerfile_stages(content)
    alias_map = {alias: index for index, (_base, alias) in enumerate(stages) if alias}
    return [
        _resolve_stage_base(stages, alias_map, index) for index in range(len(stages))
    ]


def _read_dockerfile(dockerfile: Optional[Path]) -> Optional[str]:
    if not dockerfile or not dockerfile.is_file():
        return None
    try:
        return dockerfile.read_text()
    except OSError:
        return None


def _parse_dockerfile_stages(content: str) -> List[tuple[str, Optional[str]]]:
    from kamiwaza_extensions.validators.platform_runtime import parse_from_instruction

    stages: List[tuple[str, Optional[str]]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("FROM "):
            continue
        base_ref, alias = parse_from_instruction(stripped)
        if base_ref:
            stages.append((base_ref.lower(), alias.lower() if alias else None))
    return stages


def _resolve_stage_base(
    stages: List[tuple[str, Optional[str]]],
    alias_map: dict[str, int],
    index: int,
    seen: Optional[set[str]] = None,
) -> str:
    base_ref = stages[index][0]
    alias_index = alias_map.get(base_ref)
    seen = seen or set()
    if alias_index is None or base_ref in seen:
        return base_ref
    seen.add(base_ref)
    return _resolve_stage_base(stages, alias_map, alias_index, seen)


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
    content = _read_dockerfile(dockerfile)
    if content is None:
        return None

    lines = content.splitlines()
    runtime_start = _final_stage_body_start(lines)
    return _last_runtime_pythonpath(lines[runtime_start:])


def _final_stage_body_start(lines: List[str]) -> int:
    runtime_start = 0
    for index, line in enumerate(lines):
        if line.strip().upper().startswith("FROM "):
            runtime_start = index + 1
    return runtime_start


def _last_runtime_pythonpath(lines: List[str]) -> Optional[str]:
    last_value: Optional[str] = None
    for line in lines:
        match = _PYTHONPATH_ENV_RE.match(line)
        if match:
            last_value = match.group(2)
    return last_value


def _image_basename(image_ref: str) -> str:
    ref = image_ref.split("@", 1)[0]
    name = ref.rsplit("/", 1)[-1]
    return name.split(":", 1)[0].lower()
