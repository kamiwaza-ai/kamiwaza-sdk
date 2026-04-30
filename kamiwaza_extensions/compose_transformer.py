"""In-memory Docker Compose transformation for deployment."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional


# Default resource limits/requests by service pattern.
# Limits = ceiling (burst during build), requests = reservation (steady state).
_RESOURCE_DEFAULTS: List[tuple[re.Pattern, Dict[str, Dict[str, str]]]] = [
    (re.compile(r"postgres|mysql|mariadb", re.I), {
        "limits": {"cpus": "0.5", "memory": "512M"},
    }),
    (re.compile(r"redis|valkey", re.I), {
        "limits": {"cpus": "0.25", "memory": "256M"},
    }),
    (re.compile(r"frontend|nginx|caddy", re.I), {
        "limits": {"cpus": "2.0", "memory": "1G"},
        "reservations": {"cpus": "0.25", "memory": "256M"},
    }),
]
_DEFAULT_LIMITS: Dict[str, Dict[str, str]] = {
    "limits": {"cpus": "1.0", "memory": "1G"},
}


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
        # Services without a build context — both external (postgres, redis)
        # and prebuilt-internal (e.g. a helper image published from another
        # repo) — keep their declared image ref verbatim. publish only owns
        # tags for what it builds and pushes.

        # 5. Add resource limits if missing
        _ensure_resource_limits(svc)

        # 6. Remove deployment-incompatible keys
        svc.pop("extra_hosts", None)
        svc.pop("container_name", None)
        svc.pop("networks", None)

        # 7. Strip env vars with unexpanded ${} references — these are
        #    docker-compose variable substitutions that don't work in K8s.
        #    The operator injects the real values via ConfigMap.
        if "environment" in svc:
            svc["environment"] = _strip_shell_refs(svc["environment"])

        return svc


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _strip_shell_refs(env: Any) -> Any:
    """Remove env entries whose values contain ``${...}`` references.

    Docker-compose ``${VAR:-default}`` syntax is only resolved by
    docker-compose itself.  In K8s these appear as literal strings and
    either shadow operator-injected values or point at compose services
    that don't exist in the cluster.  Plain values (no ``${``) pass
    through unchanged — those are intentional overrides.
    """
    if isinstance(env, dict):
        return {k: v for k, v in env.items()
                if not (isinstance(v, str) and "${" in v)}
    if isinstance(env, list):
        return [e for e in env if not _entry_has_shell_ref(e)]
    return env


def _entry_has_shell_ref(entry: Any) -> bool:
    if isinstance(entry, str) and "=" in entry:
        return "${" in entry.split("=", 1)[1]
    if isinstance(entry, dict):
        return "${" in str(entry.get("value", ""))
    return False


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


def _ensure_resource_limits(svc: Dict[str, Any]) -> None:
    """Add default resource limits if not already specified."""
    deploy = svc.setdefault("deploy", {})
    resources = deploy.setdefault("resources", {})
    if "limits" in resources:
        return

    # Determine defaults from image name or service content
    hint = svc.get("image", "")
    defaults = _DEFAULT_LIMITS
    for pattern, res_defaults in _RESOURCE_DEFAULTS:
        if pattern.search(hint):
            defaults = res_defaults
            break
    resources["limits"] = dict(defaults["limits"])
    if "reservations" in defaults and "reservations" not in resources:
        resources["reservations"] = dict(defaults["reservations"])
