"""Immutable image preparation for remote development deployments."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import typer
from rich.console import Console

from kamiwaza_extensions.image_pusher import ImagePushError
from kamiwaza_extensions.registry_resolution import build_push_ref_map


@dataclass(frozen=True, slots=True)
class DevImagePinRequest:
    transformed: dict[str, Any]
    catalog_compose: dict[str, Any]
    image_refs: list[str]
    image_registry: str
    push_registry: str
    skip: bool
    console: Console
    resolve_digest: Callable[[str], str]


def resolve_pushed_image_digests(
    image_refs: list[str],
    push_ref_map: dict[str, str],
    resolve_digest: Callable[[str], str],
) -> dict[str, str]:
    """Resolve every pushed tag through its host-reachable registry ref."""
    return {
        image_ref: resolve_digest(push_ref_map.get(image_ref, image_ref))
        for image_ref in dict.fromkeys(image_refs)
    }


def _pin_environment_image_ref(value: Any, pins: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    for image_ref, digest in sorted(pins.items(), key=lambda item: -len(item[0])):
        pinned_ref = f"{image_ref}@{digest}"
        if value == image_ref:
            return pinned_ref
        default = f":-{image_ref}}}"
        if default in value:
            return value.replace(default, f":-{pinned_ref}}}")
        assignment = f"={image_ref}"
        if value.endswith(assignment):
            return f"{value[: -len(assignment)]}={pinned_ref}"
    return value


def _pin_service_image_refs(service: object, pins: dict[str, str]) -> None:
    if not isinstance(service, dict):
        return
    image_ref = service.get("image")
    if image_ref in pins:
        service["image"] = f"{image_ref}@{pins[image_ref]}"
    environment = service.get("environment")
    if isinstance(environment, dict):
        service["environment"] = {
            key: _pin_environment_image_ref(value, pins)
            for key, value in environment.items()
        }
    elif isinstance(environment, list):
        service["environment"] = [
            _pin_environment_image_ref(value, pins) for value in environment
        ]


def pin_compose_image_refs(
    compose_data: dict[str, Any], pins: dict[str, str]
) -> dict[str, Any]:
    """Return compose with pushed services and dynamic image env refs pinned."""
    pinned = copy.deepcopy(compose_data)
    for service in (pinned.get("services") or {}).values():
        _pin_service_image_refs(service, pins)
    return pinned


def pin_dev_deployment_images(
    request: DevImagePinRequest,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pin pushed workload and catalog refs, or preserve explicit no-push refs."""
    if request.skip or not request.image_refs:
        return request.transformed, request.catalog_compose
    push_ref_map = build_push_ref_map(
        request.image_refs,
        image_registry=request.image_registry,
        push_registry=request.push_registry,
    )
    try:
        pins = resolve_pushed_image_digests(
            request.image_refs,
            push_ref_map,
            request.resolve_digest,
        )
    except ImagePushError as exc:
        request.console.print(f"[red]Error:[/red] Could not pin pushed images: {exc}")
        raise typer.Exit(code=1) from exc
    return (
        pin_compose_image_refs(request.transformed, pins),
        pin_compose_image_refs(request.catalog_compose, pins),
    )
