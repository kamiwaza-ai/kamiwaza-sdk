"""Validation for docker-compose.yml files."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import yaml

from kamiwaza_extensions.validators.result import ValidationResult
from kamiwaza_extensions.volume_utils import looks_like_host_path

MISSING_RESOURCE_LIMITS_TEXT = "no resource limits defined"


def is_missing_resource_limits_finding(message: str) -> bool:
    """Return True when *message* is the compose missing-limits finding.

    As of ENG-4956 this finding is emitted on the ``info`` channel when
    the generic ``ComposeTransformer`` will run (deploy applies
    defaults), and as a ``warning`` otherwise; conversion still treats
    it as blocking — see ``ConversionAgent`` for why. The match is
    channel-agnostic, so callers should scan ``warnings + info``.
    """
    return MISSING_RESOURCE_LIMITS_TEXT in message.lower()


# Deprecated alias: ``is_missing_resource_limits_warning`` was the
# pre-ENG-4956 name (the finding was always a warning then). Kept so
# external importers don't break; prefer the ``_finding`` name.
is_missing_resource_limits_warning = is_missing_resource_limits_finding


class ComposeValidator:
    """Validates docker-compose.yml for deployment compatibility."""

    def validate(
        self,
        compose_path: Path,
        ext_dir: Path,
        *,
        transformer_handled: bool = True,
    ) -> ValidationResult:
        """Validate a compose file for deployment compatibility.

        ``transformer_handled`` controls how findings that the generic
        ``ComposeTransformer`` would normally fix are reported. When
        True (the default — ``kz-ext validate`` and publishes that go
        through the generic transform), bind mounts and missing
        resource limits are emitted on the ``info`` channel because
        deploy strips/backfills them. When False — e.g. publishing an
        authored ``docker-compose.appgarden.yml``, which bypasses the
        transformer — the same findings are emitted as actionable
        warnings because no deploy-time stripping or defaulting occurs.
        """
        errors: List[str] = []
        warnings: List[str] = []
        info: List[str] = []

        try:
            with compose_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            return ValidationResult(passed=False, errors=[f"Invalid YAML: {exc}"])
        except FileNotFoundError:
            return ValidationResult(passed=False, errors=[f"File not found: {compose_path}"])

        if not isinstance(data, dict):
            return ValidationResult(passed=False, errors=["Compose file must be a YAML mapping"])

        services = data.get("services", {})
        if not services:
            warnings.append("No services defined in compose file")
            return ValidationResult(passed=True, warnings=warnings)

        # ENG-4834: named volumes back to ``emptyDir`` at deploy. emptyDir
        # is Pod-scoped, so a single named volume referenced by >1 service
        # does NOT share data at runtime — each pod gets its own scratch
        # dir. Track usage across services and warn after the per-service
        # pass below so the user knows the contract before deploy.
        named_volume_users: Dict[str, List[str]] = defaultdict(list)

        for svc_name, svc_config in services.items():
            if not isinstance(svc_config, dict):
                continue

            # Host port bindings
            ports = svc_config.get("ports", [])
            for port in ports:
                port_str = str(port)
                if ":" in port_str:
                    warnings.append(f"Service '{svc_name}': host port binding '{port_str}' — may conflict in deployment")

            # Bind mounts and tmpfs. Bind mounts in a service with a
            # ``build:`` context are a normal local-dev hot-reload pattern
            # (the file lives in the build context and the running image
            # already has it baked in); ``ComposeTransformer`` strips
            # these at deploy time. For services without ``build:`` —
            # prebuilt images — a bind mount has no transform path and
            # the deployed pod would silently miss the files, which is
            # the failure mode this validator exists to prevent.
            has_build = "build" in svc_config
            for vol in svc_config.get("volumes") or []:
                if _is_tmpfs_mount(vol):
                    errors.append(
                        f"Service '{svc_name}': tmpfs mount '{_format_volume(vol)}' "
                        "is not supported in deployment; write to /tmp directly "
                        "or use a named volume like 'data:/path'"
                    )
                    continue
                if is_bind_mount(vol):
                    formatted = _format_volume(vol)
                    if not has_build:
                        errors.append(
                            f"Service '{svc_name}': bind mount '{formatted}' "
                            "is not supported in deployment; use a named volume "
                            "like 'data:/path' or write to /tmp. Note: named "
                            "volumes deploy as emptyDir, so data is lost on pod "
                            "restart and not shared across services."
                        )
                    elif transformer_handled:
                        # ENG-4956: a build-service bind mount is the local-dev
                        # hot-reload pattern ComposeTransformer strips at deploy,
                        # so a fresh scaffold reports it as info, not a warning.
                        info.append(
                            f"Service '{svc_name}': bind mount '{formatted}' "
                            "— local-dev only (stripped at deploy). Use a named "
                            "volume for persistence, or 'develop.watch' for hot-reload."
                        )
                    else:
                        # Publish path that bypasses ComposeTransformer (an
                        # authored appgarden compose): the bind mount is NOT
                        # stripped, so surface it as an actionable warning.
                        warnings.append(
                            f"Service '{svc_name}': bind mount '{formatted}' "
                            "— ships to the catalog as-is "
                            "(this publish path does not strip bind mounts)"
                        )
                    continue
                source = _named_volume_source(vol)
                if source is not None:
                    named_volume_users[source].append(svc_name)

            # Service-level ``tmpfs:`` key — a distinct compose field from
            # ``volumes:``. ``ComposeTransformer`` only strips long-form
            # tmpfs entries that appear inside ``volumes:``, so a service
            # declaring the more common top-level ``tmpfs: [...]`` slips
            # past the transformer and silently loses the mount at deploy.
            tmpfs = svc_config.get("tmpfs")
            if tmpfs:
                entries = tmpfs if isinstance(tmpfs, list) else [tmpfs]
                for entry in entries:
                    errors.append(
                        f"Service '{svc_name}': tmpfs mount '{entry}' is not "
                        "supported in deployment; write to /tmp directly or "
                        "use a named volume like 'data:/path'"
                    )

            # Missing resource limits
            deploy = svc_config.get("deploy", {})
            resources = deploy.get("resources", {}) if isinstance(deploy, dict) else {}

            # ENG-5426: ``deploy.resources.requests`` is a Kubernetes term, not
            # a Docker Compose key — Compose's schema only knows ``limits`` and
            # ``reservations``. Authors writing ``requests:`` out of K8s habit
            # have it silently dropped from the compose→catalog→CR pipeline,
            # which then ships limits-only and lets Kubernetes default the
            # request to equal the limit — silently over-reserving (ENG-5424).
            # Fail-fast here so the typo never reaches deploy; do NOT auto-map
            # to ``reservations``, because silently rewriting the author's
            # intent is the failure class we're eliminating.
            if isinstance(resources, dict) and "requests" in resources:
                errors.append(
                    f"Service '{svc_name}': "
                    "deploy.resources.requests is not a valid Docker Compose key "
                    "(only `limits` and `reservations` are valid). "
                    "Did you mean `reservations`? "
                    "`reservations` is the Compose term that maps to "
                    "Kubernetes `requests` at deploy."
                )

            if not isinstance(resources, dict) or not resources.get("limits"):
                finding = f"Service '{svc_name}': {MISSING_RESOURCE_LIMITS_TEXT}"
                if transformer_handled:
                    info.append(f"{finding} — defaults will be applied at deploy")
                else:
                    warnings.append(
                        f"{finding} — add explicit limits "
                        "(this publish path does not apply deploy-time defaults)"
                    )

            # Explicit container_name
            if "container_name" in svc_config:
                warnings.append(f"Service '{svc_name}': explicit container_name — platform manages naming")

            # Build section — check Dockerfiles exist
            if "build" in svc_config:
                build = svc_config["build"]
                if isinstance(build, dict):
                    context = build.get("context", ".")
                    dockerfile = build.get("dockerfile", "Dockerfile")
                    dockerfile_path = ext_dir / context / dockerfile
                    if not dockerfile_path.exists():
                        errors.append(f"Service '{svc_name}': Dockerfile not found at {context}/{dockerfile}")
                elif isinstance(build, str):
                    dockerfile_path = ext_dir / build / "Dockerfile"
                    if not dockerfile_path.exists():
                        errors.append(f"Service '{svc_name}': Dockerfile not found at {build}/Dockerfile")

        for source, users in named_volume_users.items():
            if len(users) > 1:
                joined = ", ".join(sorted(set(users)))
                warnings.append(
                    f"Named volume '{source}' referenced by multiple services "
                    f"({joined}) — emptyDir is pod-scoped, so each service "
                    "gets its own copy and data is not shared at runtime."
                )

        # Custom networks
        networks = data.get("networks", {})
        if networks:
            warnings.append("Custom networks defined — platform manages networking")

        return ValidationResult(
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            info=info,
        )


def is_bind_mount(volume: object) -> bool:
    """Return True when *volume* is a host bind mount (not a named volume).

    Public so ``ComposeTransformer`` can share the exact detection the
    validator uses, keeping "stripped at deploy" findings in sync with
    what the transformer actually strips.
    """
    if isinstance(volume, dict):
        volume_type = volume.get("type")
        source = volume.get("source") or volume.get("src")
        if volume_type == "bind":
            return True
        if volume_type in {"tmpfs", "volume"}:
            return False
        return bool(source and looks_like_host_path(str(source)))

    if not isinstance(volume, str):
        return False

    if looks_like_host_path(volume):
        return True
    if ":./" in volume or ":../" in volume:
        return True
    if ":" not in volume:
        return False
    source, _, _ = volume.partition(":")
    return looks_like_host_path(source)


def _is_tmpfs_mount(volume: object) -> bool:
    if isinstance(volume, dict):
        return volume.get("type") == "tmpfs"
    return False


def _named_volume_source(volume: object) -> str | None:
    """Return the source name for a true named compose volume, else None."""
    if isinstance(volume, dict):
        if volume.get("type") not in (None, "volume"):
            return None
        source = volume.get("source") or volume.get("src")
        if not source:
            return None
        source_str = str(source)
        if looks_like_host_path(source_str):
            return None
        return source_str

    if not isinstance(volume, str) or ":" not in volume:
        return None
    source, _, _ = volume.partition(":")
    if not source or looks_like_host_path(source):
        return None
    return source


def _format_volume(volume: object) -> str:
    if isinstance(volume, dict):
        source = volume.get("source") or volume.get("src") or ""
        target = (
            volume.get("target") or volume.get("destination") or volume.get("dst") or ""
        )
        if source or target:
            return f"{source}:{target}".rstrip(":")
    return str(volume)
