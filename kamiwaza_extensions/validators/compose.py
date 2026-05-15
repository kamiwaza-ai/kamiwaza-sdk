"""Validation for docker-compose.yml files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import yaml

from kamiwaza_extensions.validators.result import ValidationResult

MISSING_RESOURCE_LIMITS_TEXT = "no resource limits defined"


def is_missing_resource_limits_warning(message: str) -> bool:
    """Return True when *message* is the compose missing-limits warning."""
    return MISSING_RESOURCE_LIMITS_TEXT in message.lower()


class ComposeValidator:
    """Validates docker-compose.yml for deployment compatibility."""

    def validate(self, compose_path: Path, ext_dir: Path) -> ValidationResult:
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

        for svc_name, svc_config in services.items():
            if not isinstance(svc_config, dict):
                continue

            # Host port bindings
            ports = svc_config.get("ports", [])
            for port in ports:
                port_str = str(port)
                if ":" in port_str:
                    warnings.append(f"Service '{svc_name}': host port binding '{port_str}' — may conflict in deployment")

            # Bind mounts
            volumes = svc_config.get("volumes", [])
            for vol in volumes:
                if _is_bind_mount(vol):
                    info.append(
                        f"Service '{svc_name}': bind mount '{_format_volume(vol)}' "
                        "— local-dev only (stripped at deploy)"
                    )

            # Missing resource limits
            deploy = svc_config.get("deploy", {})
            if isinstance(deploy, dict):
                resources = deploy.get("resources", {})
                if not resources or not isinstance(resources, dict) or not resources.get("limits"):
                    info.append(
                        f"Service '{svc_name}': {MISSING_RESOURCE_LIMITS_TEXT} "
                        "— defaults will be applied at deploy"
                    )
            else:
                info.append(
                    f"Service '{svc_name}': {MISSING_RESOURCE_LIMITS_TEXT} "
                    "— defaults will be applied at deploy"
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


def _is_bind_mount(volume: object) -> bool:
    if isinstance(volume, dict):
        volume_type = volume.get("type")
        source = volume.get("source") or volume.get("src")
        if volume_type == "bind":
            return True
        return bool(source and _looks_like_host_path(str(source)))

    if not isinstance(volume, str):
        return False

    if re.match(r"^[A-Za-z]:[\\/]", volume):
        return True
    if volume.startswith(("./", "../", "/", "~")):
        return True
    if ":./" in volume or ":../" in volume:
        return True
    if ":" not in volume:
        return False
    source, _, _ = volume.partition(":")
    return _looks_like_host_path(source)


def _looks_like_host_path(source: str) -> bool:
    return (
        source.startswith(("/", "./", "../", "~"))
        or source in {".", ".."}
        or bool(re.match(r"^[A-Za-z]:[\\/]", source))
    )


def _format_volume(volume: object) -> str:
    if isinstance(volume, dict):
        source = volume.get("source") or volume.get("src") or ""
        target = (
            volume.get("target") or volume.get("destination") or volume.get("dst") or ""
        )
        if source or target:
            return f"{source}:{target}".rstrip(":")
    return str(volume)
