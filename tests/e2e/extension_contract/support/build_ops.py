from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from .common import EXTENSION_FIXTURES_ROOT, deployment_env_overrides, env_flag


@dataclass(frozen=True)
class _BuildTarget:
    image: str
    context: Path
    dockerfile: Path


def run_repo_command(harness: Any, command: list[str]) -> None:
    timeout_seconds = _command_timeout_seconds()
    result = subprocess.run(  # noqa: S603
        command,
        cwd=EXTENSION_FIXTURES_ROOT,
        env=_command_env(harness),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        details = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        pytest.fail(f"Command failed ({' '.join(command)}):\n{details}")


def _command_timeout_seconds() -> int:
    raw_value = os.getenv("LIVE_EXTENSION_REPO_COMMAND_TIMEOUT", "600")
    try:
        parsed = int(raw_value)
    except ValueError:
        return 600
    return max(1, min(parsed, 3600))


def build_extension(harness: Any, contract: Any) -> None:
    if not contract.build_before_deploy and not env_flag("LIVE_EXTENSION_BUILD_EXTENSIONS", default=False):
        return
    extension_root = _extension_root(contract)
    services = _compose_service_build_targets(extension_root / "docker-compose.yml", extension_root)
    if not services:
        pytest.fail(f"No buildable services with image tags found in {extension_root}/docker-compose.yml")
    for service_name, target in services.items():
        if not target.dockerfile.exists():
            pytest.fail(f"Missing Dockerfile for {contract.extension_name} service {service_name}: {target.dockerfile}")
        run_repo_command(harness, ["docker", "build", "-t", target.image, "-f", str(target.dockerfile), str(target.context)])
    _align_built_images_to_template(harness, contract)


def push_app_template(harness: Any, contract: Any) -> None:
    extension_root = _extension_root(contract)
    payload = _build_template_payload(extension_root, contract)
    list_params = {"template_type": payload.get("template_type") or "app"}
    templates = harness.client.get("/apps/app_templates", params=list_params)
    existing = next(
        (t for t in templates if (t.get("name") if isinstance(t, dict) else getattr(t, "name", None)) == contract.template_name),
        None,
    )
    if existing is not None:
        template_id = existing.get("id") if isinstance(existing, dict) else getattr(existing, "id", None)
        harness.client.put(f"/apps/app_templates/{template_id}", json=payload)
    else:
        harness.client.post("/apps/app_templates", json=payload)


def _build_template_payload(extension_root: Path, contract: Any) -> dict[str, Any]:
    metadata_path = extension_root / "kamiwaza.json"
    compose_path = extension_root / "docker-compose.appgarden.yml"
    if not metadata_path.exists():
        pytest.fail(f"Missing kamiwaza.json for {contract.extension_name}: {metadata_path}")
    if not compose_path.exists():
        pytest.fail(f"Missing docker-compose.appgarden.yml for {contract.extension_name}: {compose_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    compose_yml = compose_path.read_text(encoding="utf-8")
    payload: dict[str, Any] = {
        "name": metadata.get("name", contract.template_name),
        "version": metadata.get("version", contract.resolved_template_version()),
        "description": metadata.get("description", ""),
        "category": metadata.get("category", "developer-tools"),
        "source_type": metadata.get("source_type", "kamiwaza"),
        "visibility": metadata.get("visibility", "private"),
        "risk_tier": metadata.get("risk_tier", 0),
        "verified": metadata.get("verified", False),
        "kamiwaza_version": metadata.get("kamiwaza_version", ""),
        "env_defaults": metadata.get("env_defaults", {}),
        "compose_yml": compose_yml,
        "template_type": metadata.get("template_type", "app"),
    }
    return {k: v for k, v in payload.items() if v not in (None, "")}


def find_app_template(harness: Any, contract: Any) -> dict[str, Any]:
    expected_version = contract.resolved_template_version()
    for template in harness.client.get("/apps/app_templates", params={"template_type": "app"}):
        if template.get("name") == contract.template_name and str(template.get("version") or "") == expected_version:
            return template
    pytest.fail(f"Template '{contract.template_name}' version '{expected_version}' was not found after push")


def pull_template_images(harness: Any, template_id: str) -> None:
    result = harness.client.post(f"/apps/images/pull/{template_id}")
    if result.get("all_successful") is False:
        failed_images = [entry.get("image") for entry in result.get("results", []) if isinstance(entry, dict) and entry.get("success") is False]
        if env_flag("LIVE_EXTENSION_ALLOW_LOCAL_IMAGE_FALLBACK", default=False) and failed_images and all(image_exists_locally(image) for image in failed_images):
            return
        pytest.fail(f"Template image pull failed for {template_id}: {result}")


def deployment_env_vars(harness: Any, contract: Any) -> dict[str, str]:
    env_vars = {"KAMIWAZA_API_URL": harness.settings.base_url, "KAMIWAZA_VERIFY_SSL": "true" if harness.settings.verify_ssl else "false"}
    if contract.secret_encryption_key_env_var:
        env_vars[contract.secret_encryption_key_env_var] = harness.secret_encryption_key
    # ENG-5956 follow-up — kamiwaza-sdk#134 RE-REVIEW H1: inject the
    # trusted-proxy shared secret so the AppGarden-deployed container's
    # ``trusted_routed_workroom_context()`` can validate routed requests.
    # Read from LIVE_EXTENSION_TRUSTED_PROXY_SECRET (or
    # KAMIWAZA_TRUSTED_PROXY_SECRET) so the harness operator can match
    # whatever the platform's routing layer (Traefik / istio ingress)
    # injects in ``x-kamiwaza-trusted-proxy`` on routed traffic. If unset
    # the trusted-routed path stays fail-closed — which is the correct
    # stance, since direct container traffic must not be trusted.
    trusted_proxy_secret = (
        os.getenv("LIVE_EXTENSION_TRUSTED_PROXY_SECRET", "").strip()
        or os.getenv("KAMIWAZA_TRUSTED_PROXY_SECRET", "").strip()
    )
    if trusted_proxy_secret:
        env_vars["KAMIWAZA_TRUSTED_PROXY_SECRET"] = trusted_proxy_secret
    env_vars.update(deployment_env_overrides())
    return env_vars


def image_exists_locally(image: Any) -> bool:
    if not isinstance(image, str) or not image:
        return False
    docker_path = shutil.which("docker")
    if docker_path is None:
        pytest.fail("Docker CLI is required for live extension image inspection")
    try:
        result = subprocess.run([docker_path, "image", "inspect", image], capture_output=True, text=True, check=False, timeout=30)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _command_env(harness: Any) -> dict[str, str]:
    env = os.environ.copy()
    env["KAMIWAZA_API_URL"] = harness.settings.base_url
    env["KAMIWAZA_VERIFY_SSL"] = "true" if harness.settings.verify_ssl else "false"
    if harness.settings.api_key:
        env["KAMIWAZA_API_KEY"] = harness.settings.api_key
    if harness.settings.username:
        env["KAMIWAZA_USERNAME"] = harness.settings.username
    if harness.settings.password:
        env["KAMIWAZA_PASSWORD"] = harness.settings.password
    return env


def _extension_root(contract: Any) -> Path:
    return EXTENSION_FIXTURES_ROOT / contract.extension_name


def _compose_service_images(compose_path: Path) -> dict[str, str]:
    if not compose_path.exists():
        return {}
    payload = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    services = payload.get("services")
    if not isinstance(services, dict):
        return {}
    return {
        str(name): image.strip()
        for name, service in services.items()
        if isinstance(service, dict) and isinstance((image := service.get("image")), str) and image.strip()
    }


def _compose_service_build_targets(compose_path: Path, extension_root: Path) -> dict[str, _BuildTarget]:
    if not compose_path.exists():
        return {}
    payload = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    services = payload.get("services")
    if not isinstance(services, dict):
        return {}
    targets: dict[str, _BuildTarget] = {}
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        image = service.get("image")
        build = service.get("build")
        if not (isinstance(image, str) and image.strip()):
            continue
        if isinstance(build, str):
            context_rel, dockerfile_rel = build, "Dockerfile"
        elif isinstance(build, dict):
            context_rel = str(build.get("context", "."))
            dockerfile_rel = str(build.get("dockerfile", "Dockerfile"))
        else:
            continue
        context = (extension_root / context_rel).resolve()
        dockerfile = (context / dockerfile_rel).resolve()
        targets[str(name)] = _BuildTarget(image=image.strip(), context=context, dockerfile=dockerfile)
    return targets


def _align_built_images_to_template(harness: Any, contract: Any) -> None:
    extension_root = _extension_root(contract)
    dev_images = _compose_service_images(extension_root / "docker-compose.yml")
    template_images = _compose_service_images(extension_root / "docker-compose.appgarden.yml")
    for service_name, template_image in template_images.items():
        dev_image = dev_images.get(service_name)
        if dev_image and dev_image != template_image:
            if not image_exists_locally(dev_image):
                pytest.fail(f"Local build for {contract.extension_name} did not produce expected image {dev_image} needed for template tag {template_image}")
            run_repo_command(harness, ["docker", "tag", dev_image, template_image])
