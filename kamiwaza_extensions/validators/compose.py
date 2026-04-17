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
                vol_str = str(vol)
                if isinstance(vol, str) and (":./" in vol_str or vol_str.startswith("./") or vol_str.startswith("../") or re.match(r"^/[^$]", vol_str)):
                    warnings.append(f"Service '{svc_name}': bind mount '{vol_str}' — not available in deployment")

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

        # Custom networks
        networks = data.get("networks", {})
        if networks:
            warnings.append("Custom networks defined — platform manages networking")

        return ValidationResult(passed=len(errors) == 0, errors=errors, warnings=warnings)
