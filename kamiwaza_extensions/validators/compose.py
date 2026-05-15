"""Validation for docker-compose.yml files."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import yaml

from kamiwaza_extensions.validators.result import ValidationResult
from kamiwaza_extensions.volume_utils import looks_like_host_path

MISSING_RESOURCE_LIMITS_TEXT = "no resource limits defined"


def is_missing_resource_limits_warning(message: str) -> bool:
    """Return True when *message* is the compose missing-limits warning."""
    return MISSING_RESOURCE_LIMITS_TEXT in message.lower()


class ComposeValidator:
    """Validates docker-compose.yml for deployment compatibility."""

    def validate(self, compose_path: Path, ext_dir: Path) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []

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
                if _is_bind_mount(vol):
                    formatted = _format_volume(vol)
                    if has_build:
                        warnings.append(
                            f"Service '{svc_name}': bind mount '{formatted}' "
                            "will be stripped at deploy (local-dev only). "
                            "Use a named volume for persistence, or 'develop.watch' "
                            "for hot-reload."
                        )
                    else:
                        errors.append(
                            f"Service '{svc_name}': bind mount '{formatted}' "
                            "is not supported in deployment; use a named volume "
                            "like 'data:/path' or write to /tmp. Note: named "
                            "volumes deploy as emptyDir, so data is lost on pod "
                            "restart and not shared across services."
                        )
                    continue
                source = _named_volume_source(vol)
                if source is not None:
                    named_volume_users[source].append(svc_name)

            # Missing resource limits
            deploy = svc_config.get("deploy", {})
            if isinstance(deploy, dict):
                resources = deploy.get("resources", {})
                if not resources or not isinstance(resources, dict) or not resources.get("limits"):
                    warnings.append(f"Service '{svc_name}': {MISSING_RESOURCE_LIMITS_TEXT}")
            else:
                warnings.append(f"Service '{svc_name}': {MISSING_RESOURCE_LIMITS_TEXT}")

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

        return ValidationResult(passed=len(errors) == 0, errors=errors, warnings=warnings)


def _is_bind_mount(volume: object) -> bool:
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
