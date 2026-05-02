"""Local-dev compose override: bind-mount the SDK and route imports
shell-free.

Both mechanisms are deliberately shell-free so the override works
against runtime images that lack ``/bin/sh`` (Chainguard distroless,
scratch-based images): see ENG-4413.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from kamiwaza_extensions.sdk_override.classification import detect_service_runtime
from kamiwaza_extensions.sdk_override.spec import SdkOverrideSpec

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
