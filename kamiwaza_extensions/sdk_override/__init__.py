"""SDK override — resolve local kamiwaza-sdk source for dev builds.

Module map:

- ``spec`` — config model, resolution, validation, dist-staleness,
  TS build helper, diagnostics.
- ``classification`` — service detectors (frontend/backend/static),
  Dockerfile stage parsing.
- ``compose`` — local-dev compose override (shell-free env-var +
  bind-mount injection — works on Chainguard distroless runtime images,
  see ENG-4413).
- ``build`` — build-time Dockerfile overlays for both ``dev local
  --sdk-repo`` (per-service patches) and cluster deploy
  (``generate_build_overrides`` → ``apply_build_overlay``).

Public re-exports below preserve the historical
``from kamiwaza_extensions.sdk_override import X`` import paths.
"""

from __future__ import annotations

from kamiwaza_extensions.sdk_override.build import (
    BuildOverride,
    apply_build_overlay,
    generate_build_overrides,
    generate_local_build_dockerfile_patches,
)
from kamiwaza_extensions.sdk_override.classification import (
    detect_service_runtime,
    detect_service_type,
)
from kamiwaza_extensions.sdk_override.compose import (
    generate_compose_override,
)
from kamiwaza_extensions.sdk_override.spec import (
    SdkOverrideSpec,
    ValidationResult,
    build_typescript_lib,
    check_buildkit_available,
    is_typescript_dist_stale,
    print_override_diagnostics,
    resolve_sdk_override,
    validate_sdk_override,
)

__all__ = [
    # spec
    "SdkOverrideSpec",
    "ValidationResult",
    "build_typescript_lib",
    "check_buildkit_available",
    "is_typescript_dist_stale",
    "print_override_diagnostics",
    "resolve_sdk_override",
    "validate_sdk_override",
    # classification
    "detect_service_runtime",
    "detect_service_type",
    # compose
    "generate_compose_override",
    # build
    "BuildOverride",
    "apply_build_overlay",
    "generate_build_overrides",
    "generate_local_build_dockerfile_patches",
]
