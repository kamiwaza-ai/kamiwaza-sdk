"""In-memory Docker Compose transformation for deployment."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional


# Default resource limits by service pattern
_RESOURCE_DEFAULTS: List[tuple[re.Pattern, Dict[str, str]]] = [
    (re.compile(r"postgres|mysql|mariadb", re.I), {"cpus": "0.5", "memory": "512M"}),
    (re.compile(r"redis|valkey", re.I), {"cpus": "0.25", "memory": "256M"}),
    (re.compile(r"frontend|nginx|caddy", re.I), {"cpus": "0.5", "memory": "512M"}),
]
_DEFAULT_LIMITS = {"cpus": "1.0", "memory": "1G"}


class ComposeTransformer:
    """Transform a local-dev compose dict into a deployment-ready dict.

    All operations are pure (no I/O).  The caller provides the parsed
    compose dict and receives a new dict suitable for building a
    ``CreateExtension`` payload.
    """

    def transform(
        self,
        compose_data: Dict[str, Any],
        extension_name: str,
        revision_tag: str,
        registry: str,
    ) -> Dict[str, Any]:
        """Return a deployment-ready copy of *compose_data*.

        Transformations applied per-service:
        1. Strip host port bindings
        2. Strip bind mounts (keep named volumes)
        3. Remove ``build`` contexts
        4. Add / update ``image`` fields with *registry*/*revision_tag*
        5. Add resource limits if missing
        6. Remove ``extra_hosts``, ``container_name``, ``networks`` keys
        """
        out = copy.deepcopy(compose_data)

        # Drop services that have a profiles key (local-only services)
        services = out.get("services") or {}
        profiled = [name for name, svc in services.items() if svc.get("profiles")]
        for name in profiled:
            del services[name]

        for svc_name, svc in services.items():
            out["services"][svc_name] = self.transform_service(
                svc,
                svc_name,
                extension_name,
                revision_tag,
                registry,
            )

        # Remove top-level networks (platform manages networking)
        out.pop("networks", None)

        return out

    def transform_service(
        self,
        service: Dict[str, Any],
        service_name: str,
        extension_name: str,
        revision_tag: str,
        registry: str,
    ) -> Dict[str, Any]:
        svc = copy.deepcopy(service)

        # 1. Strip host port bindings
        if "ports" in svc:
            svc["ports"] = _strip_host_ports(svc["ports"])

        # 2. Strip bind mounts, keep named volumes
        if "volumes" in svc:
            svc["volumes"] = _strip_bind_mounts(svc["volumes"])
            if not svc["volumes"]:
                del svc["volumes"]

        # 3 & 4. Remove build context, ensure image field
        had_build = "build" in svc
        svc.pop("build", None)

        if had_build and "image" not in svc:
            svc["image"] = f"{registry}/{extension_name}-{service_name}:{revision_tag}"
        elif had_build and "image" in svc:
            # Use consistent registry/extension-service:tag format (matches image builder)
            svc["image"] = f"{registry}/{extension_name}-{service_name}:{revision_tag}"
        elif "image" in svc and not _is_external_image(svc["image"], extension_name):
            svc["image"] = _update_tag(svc["image"], revision_tag)
        # External images (postgres, redis, etc.) are left unchanged.

        # 5. Add resource limits if missing
        _ensure_resource_limits(svc)

        # 6. Remove deployment-incompatible keys
        svc.pop("extra_hosts", None)
        svc.pop("container_name", None)
        svc.pop("networks", None)

        return svc


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _strip_host_ports(ports: List[Any]) -> List[str]:
    """``"3000:3000"`` -> ``"3000"``; ``"8080:3000/tcp"`` -> ``"3000/tcp"``."""
    result = []
    for port in ports:
        s = str(port)
        if ":" in s:
            # Take the part after the last colon (container port + optional protocol)
            container_part = s.rsplit(":", 1)[1]
            result.append(container_part)
        else:
            result.append(s)
    return result


def _strip_bind_mounts(volumes: List[Any]) -> List[str]:
    """Keep named volumes, remove bind mounts (``./``, ``../``, absolute paths)."""
    kept = []
    for vol in volumes:
        if isinstance(vol, dict):
            # Long-form volume — check type
            if vol.get("type") == "volume":
                kept.append(vol)
            # bind / tmpfs types are stripped
            continue
        s = str(vol)
        if s.startswith("/") or s.startswith("./") or s.startswith("../"):
            continue
        if ":" in s:
            host_part = s.split(":", 1)[0]
            if host_part.startswith("/") or host_part.startswith("./") or host_part.startswith("../"):
                continue
        kept.append(s)
    return kept


def _update_tag(image: str, new_tag: str) -> str:
    """Replace the tag portion of an image reference."""
    # Don't rewrite digest references
    if "@sha256:" in image:
        return image
    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon > last_slash:
        return image[:last_colon] + ":" + new_tag
    return image + ":" + new_tag


def _is_external_image(image: str, extension_name: str) -> bool:
    """Return True for images that are NOT part of this extension (e.g., postgres)."""
    lower = image.lower()
    # Common external images
    if any(ext in lower for ext in ("postgres", "mysql", "mariadb", "redis", "valkey",
                                     "mongo", "rabbitmq", "elasticsearch", "milvus",
                                     "nginx", "memcached", "minio")):
        return True
    # If image doesn't contain the extension name, treat as external
    if extension_name.lower() not in lower:
        return True
    return False


def _ensure_resource_limits(svc: Dict[str, Any]) -> None:
    """Add default resource limits if not already specified."""
    deploy = svc.setdefault("deploy", {})
    resources = deploy.setdefault("resources", {})
    if "limits" in resources:
        return

    # Determine defaults from image name or service content
    hint = svc.get("image", "")
    for pattern, limits in _RESOURCE_DEFAULTS:
        if pattern.search(hint):
            resources["limits"] = dict(limits)
            return
    resources["limits"] = dict(_DEFAULT_LIMITS)
