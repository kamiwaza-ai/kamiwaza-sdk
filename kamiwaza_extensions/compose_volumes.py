"""Translate Docker Compose named volumes to extension service pod specs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from kamiwaza_extensions.volume_utils import looks_like_host_path

_DNS_LABEL_RE = re.compile(r"[^a-z0-9-]+")
_OPERATOR_VOLUME_NAMES = frozenset({"tmp", "data"})


@dataclass(frozen=True)
class ServiceVolumeSpec:
    """Volumes and mounts owned by one service pod template."""

    volumes: List[Dict[str, Any]]
    mounts: List[Dict[str, Any]]


def build_service_volume_specs(
    transformed: Dict[str, Any],
) -> Dict[str, ServiceVolumeSpec]:
    """Build service-scoped K8s volume specs from transformed Compose."""
    specs: Dict[str, ServiceVolumeSpec] = {}
    for service_name, service in (transformed.get("services") or {}).items():
        if not isinstance(service, dict):
            continue
        spec = _build_service_volume_spec(service)
        if spec.volumes or spec.mounts:
            specs[service_name] = spec
    return specs


def _build_service_volume_spec(service: Dict[str, Any]) -> ServiceVolumeSpec:
    volumes: List[Dict[str, Any]] = []
    mounts: List[Dict[str, Any]] = []
    source_to_name: Dict[str, str] = {}
    used_names = set(_OPERATOR_VOLUME_NAMES)
    persistence_mount = _persistence_mount_path(service)

    for raw_volume in service.get("volumes", []) or []:
        parsed = _parse_named_volume_mount(raw_volume)
        if parsed is None:
            continue
        source, target, read_only = parsed
        if _covered_by_persistence(target, persistence_mount):
            continue

        volume_name = source_to_name.get(source)
        if volume_name is None:
            volume_name = _unique_k8s_volume_name(source, used_names)
            source_to_name[source] = volume_name
            volumes.append({"name": volume_name, "emptyDir": {}})
        mounts.append(_volume_mount(volume_name, target, read_only))

    return ServiceVolumeSpec(volumes=volumes, mounts=mounts)


def _covered_by_persistence(target: str, persistence_mount: str) -> bool:
    """Is this Compose target already served by the service's PVC mount?

    Exact equality is not enough: the stock Postgres layout declares
    ``pgdata:/var/lib/postgresql/data`` under a ``/var/lib/postgresql``
    persistence mount, and an emptyDir at the nested path would shadow the
    PVC subtree the volume exists to persist.
    """
    if not persistence_mount:
        return False
    normalized = target.rstrip("/") or "/"
    return normalized == persistence_mount or normalized.startswith(
        persistence_mount + "/"
    )


def _persistence_mount_path(service: Dict[str, Any]) -> str:
    """Return the service's declared persistence mountPath, if it has one.

    Raises:
        ValueError: When persistence is enabled without a mountPath. The
            operator provisions the PVC but mounts it nowhere, so translating
            the Compose volumes would emit an emptyDir at the path the author
            meant to persist — silently, and with the PVC left dangling.
    """
    extension = service.get("x-kamiwaza")
    if not isinstance(extension, dict):
        return ""
    persistence = extension.get("persistence")
    if not isinstance(persistence, dict) or persistence.get("enabled") is not True:
        return ""
    mount_path = str(persistence.get("mountPath", "")).strip().rstrip("/")
    if not mount_path:
        raise ValueError(
            "x-kamiwaza.persistence.enabled is true but mountPath is missing. "
            "Set mountPath to the directory the PVC should back."
        )
    return mount_path


def _volume_mount(name: str, target: str, read_only: bool) -> Dict[str, Any]:
    mount: Dict[str, Any] = {"name": name, "mountPath": target}
    if read_only:
        mount["readOnly"] = True
    return mount


def _parse_named_volume_mount(raw_volume: Any) -> Optional[tuple[str, str, bool]]:
    """Return ``(source, target, read_only)`` for a named Compose volume."""
    if isinstance(raw_volume, dict):
        return _parse_long_volume_mount(raw_volume)
    return _parse_short_volume_mount(raw_volume)


def _parse_short_volume_mount(
    raw_volume: Any,
) -> Optional[tuple[str, str, bool]]:
    if not isinstance(raw_volume, str):
        return None
    parts = raw_volume.split(":")
    if len(parts) < 2:
        return None
    source, target = parts[0], parts[1]
    if not _valid_named_mount(source, target):
        return None
    return source, target, _short_mount_is_read_only(parts)


def _short_mount_is_read_only(parts: List[str]) -> bool:
    modes = ",".join(parts[2:]).split(",") if len(parts) > 2 else []
    return any(mode.strip().lower() == "ro" for mode in modes)


def _parse_long_volume_mount(
    raw_volume: Dict[str, Any],
) -> Optional[tuple[str, str, bool]]:
    values = _long_volume_values(raw_volume)
    if values is None:
        return None

    source_str, target_str = values
    if not _valid_named_mount(source_str, target_str):
        return None
    read_only = bool(raw_volume.get("read_only") or raw_volume.get("readOnly"))
    return source_str, target_str, read_only


def _long_volume_values(
    raw_volume: Dict[str, Any],
) -> Optional[tuple[str, str]]:
    if raw_volume.get("type", "volume") != "volume":
        return None
    source = _first_value(raw_volume, "source", "src")
    target = _first_value(raw_volume, "target", "destination", "dst")
    if source is None or target is None:
        return None
    return str(source), str(target)


def _first_value(values: Dict[str, Any], *keys: str) -> Any:
    return next((values[key] for key in keys if values.get(key)), None)


def _valid_named_mount(source: str, target: str) -> bool:
    if not source or not target.startswith("/"):
        return False
    return not looks_like_host_path(source)


def _unique_k8s_volume_name(source: str, used_names: set[str]) -> str:
    base = _DNS_LABEL_RE.sub("-", source.lower()).strip("-")
    base = base[:63].strip("-") or "volume"
    name = base
    counter = 2
    while name in used_names:
        suffix = f"-{counter}"
        name = f"{base[: 63 - len(suffix)].rstrip('-')}{suffix}"
        counter += 1
    used_names.add(name)
    return name
