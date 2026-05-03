"""Local-dev compose override: bind-mount the SDK and route imports
shell-free.

Both mechanisms are deliberately shell-free so the override works
against runtime images that lack ``/bin/sh`` (Chainguard distroless,
scratch-based images): see ENG-4413.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from kamiwaza_extensions.sdk_override.classification import (
    _resolve_dockerfile,
    detect_service_runtime,
    read_runtime_pythonpath,
)
from kamiwaza_extensions.sdk_override.spec import SdkOverrideSpec

# In-container path the SDK repo is bind-mounted at. Adding this to
# PYTHONPATH lets ``import kamiwaza_extensions_lib`` resolve to the
# local checkout without rebuilding the image.
_SDK_BIND_TARGET = "/sdk"

# Optional escape hatch for src-layout apps whose runtime image bakes
# in a PYTHONPATH (e.g. ``ENV PYTHONPATH=/app/src``). The compose
# override unconditionally sets PYTHONPATH which overwrites the
# image-baked value. Set this env var on the host before launching
# ``dev local --sdk-repo`` to append additional colon-separated paths
# AFTER ``/sdk`` so the image's import paths continue to resolve. The
# SDK still wins because it appears first in PYTHONPATH order.
_PYTHONPATH_APPEND_ENV = "KZ_SDK_PYTHONPATH_APPEND"

# In-container path the TypeScript SDK package gets bind-mounted to.
# Shadowing ``/app/node_modules/@kamiwaza-ai/extensions-lib`` with the
# SDK repo's package directory lets the existing app code resolve the
# local sources via standard Node module resolution. No npm install
# at runtime, no shell required — works on Chainguard distroless
# runtime images that lack ``/bin/sh`` and ``npm``.
_TS_LIB_PACKAGE_DIR = "kamiwaza-ai-extensions-lib"
_TS_LIB_NODE_MODULES_TARGET = "/app/node_modules/@kamiwaza-ai/extensions-lib"


def generate_compose_override(
    spec: SdkOverrideSpec,
    compose_data: dict,
    extension_dir: Optional[Path] = None,
) -> dict:
    """Generate a compose override dict for local SDK development.

    Surfaces the local SDK to each service via shell-free mechanisms
    so the override works against any runtime image — including
    Chainguard distroless variants (no ``/bin/sh``, ``apt``, or ``npm``
    in the runtime stage). See ``_python_override`` /
    ``_typescript_override`` for the per-service-type mechanisms.

    Only overrides services that have a ``build`` key (pre-built
    images like redis/postgres are skipped).
    """
    override_services: dict = {}
    services = compose_data.get("services", {})

    for svc_name, svc_config in services.items():
        if "build" not in svc_config:
            continue
        svc_type = detect_service_runtime(svc_name, svc_config, extension_dir=extension_dir)
        # For Python services, sniff the Dockerfile's runtime-stage
        # ENV PYTHONPATH so we can preserve src-layout apps whose
        # images bake their own import paths (e.g.
        # ``ENV PYTHONPATH=/app/src``). None when no Dockerfile
        # available or no PYTHONPATH baked.
        baked_pythonpath: Optional[str] = None
        if svc_type == "backend" and extension_dir is not None:
            df = _resolve_dockerfile(svc_config["build"], extension_dir)
            baked_pythonpath = read_runtime_pythonpath(df)
        svc_override = _override_for(
            svc_type, spec, baked_pythonpath=baked_pythonpath
        )
        if svc_override:
            override_services[svc_name] = svc_override

    return {"services": override_services}


def _override_for(
    svc_type: str,
    spec: SdkOverrideSpec,
    *,
    baked_pythonpath: Optional[str] = None,
) -> dict:
    """Return the per-service override dict for ``svc_type``, or {} if
    no override applies (skipped service, or runtime-lib disabled)."""
    if svc_type == "backend" and spec.python:
        return _python_override(spec, baked_pythonpath=baked_pythonpath)
    if svc_type == "frontend" and spec.typescript:
        return _typescript_override(spec)
    return {}


def _python_override(
    spec: SdkOverrideSpec, *, baked_pythonpath: Optional[str] = None
) -> dict:
    """Bind-mount SDK repo at /sdk and route imports via ``PYTHONPATH``.

    PYTHONPATH composition (in order, first wins for ``import``):

    1. ``/sdk`` — the bind-mounted SDK source (always first so
       ``import kamiwaza_extensions_lib`` resolves locally).
    2. ``baked_pythonpath`` — extracted from the runtime stage's
       ``ENV PYTHONPATH=...`` declaration in the service's Dockerfile,
       if any. Preserves src-layout apps whose images bake
       ``ENV PYTHONPATH=/app/src``.
    3. ``KZ_SDK_PYTHONPATH_APPEND`` — host-side env var for paths
       beyond what's in the Dockerfile (rarely needed now that the
       Dockerfile's PYTHONPATH is preserved automatically; kept as an
       escape hatch for unusual layouts).

    The compose ``environment`` value sets PYTHONPATH wholesale —
    docker compose doesn't expand container env vars in the
    ``environment:`` field, so we synthesize the final value
    ourselves. The Dockerfile's existing entrypoint runs unmodified
    and inherits the synthesized value.
    """
    parts = [_SDK_BIND_TARGET]
    if baked_pythonpath:
        parts.append(baked_pythonpath)
    extra = os.environ.get(_PYTHONPATH_APPEND_ENV, "").strip()
    if extra:
        parts.append(extra)
    pythonpath = ":".join(parts)
    return {
        "volumes": [
            {
                "type": "bind",
                "source": str(spec.sdk_repo),
                "target": _SDK_BIND_TARGET,
                "read_only": True,
            }
        ],
        "environment": {"PYTHONPATH": pythonpath},
    }


def _typescript_override(spec: SdkOverrideSpec) -> dict:
    """Bind-mount the SDK's TS package directly into node_modules.

    Shadowing ``/app/node_modules/@kamiwaza-ai/extensions-lib`` lets
    standard Node module resolution pick up the local source — no
    runtime install, no shell required.
    """
    ts_pkg_source = spec.sdk_repo / _TS_LIB_PACKAGE_DIR
    return {
        "volumes": [
            {
                "type": "bind",
                "source": str(ts_pkg_source),
                "target": _TS_LIB_NODE_MODULES_TARGET,
                "read_only": True,
            }
        ],
    }
