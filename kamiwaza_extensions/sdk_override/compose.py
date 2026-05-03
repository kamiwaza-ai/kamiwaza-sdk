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

from kamiwaza_extensions.sdk_override.classification import detect_service_runtime
from kamiwaza_extensions.sdk_override.spec import SdkOverrideSpec

# In-container path the SDK repo is bind-mounted at. Adding this to
# PYTHONPATH lets ``import kamiwaza_extensions_lib`` resolve to the
# local checkout without rebuilding the image.
_SDK_BIND_TARGET = "/sdk"

# Optional escape hatch for src-layout apps whose runtime image bakes
# in a PYTHONPATH (e.g. ``ENV PYTHONPATH=/app/src``). The compose
# override unconditionally overwrites the env var, so without this
# hatch a ``--sdk-repo`` run on such an image would lose access to
# ``/app/src``. Set the env var on the host before launching ``dev
# local`` to prepend additional colon-separated paths after ``/sdk``.
_PYTHONPATH_PREPEND_ENV = "KZ_SDK_PYTHONPATH_PREPEND"

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
        svc_override = _override_for(svc_type, spec)
        if svc_override:
            override_services[svc_name] = svc_override

    return {"services": override_services}


def _override_for(svc_type: str, spec: SdkOverrideSpec) -> dict:
    """Return the per-service override dict for ``svc_type``, or {} if
    no override applies (skipped service, or runtime-lib disabled)."""
    if svc_type == "backend" and spec.python:
        return _python_override(spec)
    if svc_type == "frontend" and spec.typescript:
        return _typescript_override(spec)
    return {}


def _python_override(spec: SdkOverrideSpec) -> dict:
    """Bind-mount SDK repo at /sdk and set ``PYTHONPATH=/sdk``.

    The existing Dockerfile entrypoint runs unmodified and inherits
    the env var. PYTHONPATH overwrites any image-baked value — that is
    intentional for ``--sdk-repo`` mode (developers asking to use the
    local SDK want the local SDK to win unconditionally).

    For src-layout apps whose runtime image bakes a PYTHONPATH
    declaration (e.g. ``ENV PYTHONPATH=/app/src``), set
    ``KZ_SDK_PYTHONPATH_PREPEND`` on the host to append those paths
    after ``/sdk`` so imports continue to resolve. Multiple paths can
    be colon-separated. The SDK takes precedence so its
    ``import kamiwaza_extensions_lib`` resolves to the local checkout.
    """
    extra = os.environ.get(_PYTHONPATH_PREPEND_ENV, "").strip()
    pythonpath = f"{_SDK_BIND_TARGET}:{extra}" if extra else _SDK_BIND_TARGET
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
