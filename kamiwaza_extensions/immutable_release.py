"""Artifact validation at the immutable catalog publication boundary."""

from __future__ import annotations

import re
from typing import Any

import yaml

_DIGEST_REF = re.compile(r"[^\s@$]+@sha256:[0-9a-f]{64}")


def _pinned_image(value: Any) -> str:
    if not isinstance(value, str) or not _DIGEST_REF.fullmatch(value):
        raise ValueError(f"Immutable release requires a digest-pinned image reference: {value!r}")
    return value


def _inventory(entry: dict, field: str) -> set[str]:
    values = entry.get(field, [])
    if not isinstance(values, list):
        raise ValueError(f"Immutable release {field} must be an array")
    return {_pinned_image(value) for value in values}


def validate_artifacts(entry: dict) -> None:
    """Require exact images, including author-declared dynamic-spawn artifacts.

    Application code can download undeclared assets; this validates the declared
    release contract, not arbitrary behavior of the application image.
    """
    compose = entry.get("compose_yml")
    if not isinstance(compose, str):
        raise ValueError("Immutable release requires inline compose_yml")
    try:
        document = yaml.safe_load(compose)
    except yaml.YAMLError as exc:
        raise ValueError("Immutable release compose_yml is invalid YAML") from exc
    if not isinstance(document, dict):
        raise ValueError("Immutable release compose_yml must be a mapping")
    services = document.get("services")
    if not isinstance(services, dict) or not services:
        raise ValueError("Immutable release requires Compose services")
    actual = {_service_image(service) for service in services.values()}
    if actual != _inventory(entry, "docker_images"):
        raise ValueError("Immutable release docker_images must match Compose service images")
    _inventory(entry, "extra_docker_images")


def _service_image(service: Any) -> str:
    if not isinstance(service, dict):
        raise ValueError("Immutable release Compose service must be a mapping")
    if "build" in service:
        raise ValueError("Immutable release cannot contain runtime Compose build instructions")
    return _pinned_image(service.get("image"))
