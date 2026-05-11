"""In-memory Docker Compose transformation for deployment."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple


# Compose ``${VAR:-default}`` (use default if unset OR empty) and the
# ``${VAR-default}`` form (use default only if unset). For our purposes
# both collapse to the literal default — there's no host process between
# us and Kubernetes, so the var is always "unset" by the time the pod
# starts. ``${VAR:?error}`` and bare ``${VAR}`` aren't matched (no safe
# default → drop downstream).
_DEFAULT_SUB_RE = re.compile(
    r"^\$\{([A-Za-z_][A-Za-z0-9_]*):?-([^}]*)\}$"
)

# Env var names that should be left to the platform's ConfigMap envFrom
# injection (operator writes the cluster-internal value; an explicit
# ``env`` entry in the deployment would shadow it). Compose dev defaults
# like ``${KAMIWAZA_API_URL:-http://host.docker.internal:7777/api}``
# point to laptop-only addresses that don't resolve in-cluster, so we
# drop them rather than ship a broken default.
_PLATFORM_INJECTED_PREFIX = "KAMIWAZA_"


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

        return svc

    def resolve_env_placeholders(
        self,
        compose_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Collapse compose ``${VAR}`` env-value placeholders in place.

        Apply when the consumer of the transformed compose will NOT
        perform its own variable substitution — e.g. shipping the
        compose straight to a Kubernetes API. K8s reads env values as
        literal strings, so an unresolved ``${VAR:-default}`` reaches
        the pod verbatim.

        Rules (per env var):
        - ``${VAR:-default}`` / ``${VAR-default}`` for non-platform
          keys → collapsed to the literal ``default``.
        - ``${KAMIWAZA_*:-default}`` → dropped. The kamiwaza-extension
          operator injects these via ConfigMap envFrom; an explicit
          env entry would shadow the cluster-internal value.
        - ``${VAR}`` (no default) and ``${VAR:?error}`` (required) →
          dropped. No safe value to ship.
        - Plain values pass through unchanged.

        Skip this step when the destination DOES perform install-time
        substitution (e.g. a catalog template consumed by the platform
        installer that holds user-supplied ``required_env_vars``).
        """
        out = copy.deepcopy(compose_data)
        for svc in (out.get("services") or {}).values():
            if "environment" in svc:
                svc["environment"] = _resolve_shell_refs(svc["environment"])
        return out


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _resolve_shell_refs(env: Any) -> Any:
    """Resolve compose ``${VAR:-default}`` substitutions, drop unresolvable.

    Two rules:

    1. ``${VAR:-default}`` where ``VAR`` does NOT start with
       ``KAMIWAZA_`` → resolve to ``default``. The host env isn't
       consulted (we're nowhere near a docker-compose run); compose
       semantics for "VAR is unset" simply use the default. The cluster
       deployment then carries the literal default through to the pod
       — and ``detect_service_url_rewrites`` (called by
       ``PayloadBuilder``) emits a ``service-ref-rewrites`` annotation
       so the operator can swap cross-service hostnames at deploy time.

    2. ``${KAMIWAZA_*:-default}`` → drop. The kamiwaza-extension
       operator injects these via ConfigMap envFrom; an explicit env
       entry would shadow the cluster-internal value. Compose defaults
       point to laptop-only addresses (``host.docker.internal:7777``)
       that don't resolve in-cluster anyway.

    3. ``${VAR}`` (no default) and ``${VAR:?error}`` (required) → drop.
       No safe value to ship.

    Plain values without ``${`` pass through unchanged.
    """
    if isinstance(env, dict):
        out: Dict[str, Any] = {}
        for k, v in env.items():
            if not isinstance(v, str) or "${" not in v:
                out[k] = v
                continue
            resolved = _resolve_default_substitution(k, v)
            if resolved is not None:
                out[k] = resolved
        return out
    if isinstance(env, list):
        out_list: List[Any] = []
        for entry in env:
            if not _entry_has_shell_ref(entry):
                out_list.append(entry)
                continue
            resolved = _resolve_list_entry(entry)
            if resolved is not None:
                out_list.append(resolved)
        return out_list
    return env


def _resolve_default_substitution(key: str, value: str) -> Optional[str]:
    """Return ``default`` from ``${VAR:-default}`` for non-platform keys.

    Returns None when:
    - the value isn't a single ``${VAR(:-)default}`` substitution
    - the key is platform-injected (``KAMIWAZA_*``) — let envFrom win
    """
    if key.startswith(_PLATFORM_INJECTED_PREFIX):
        return None
    m = _DEFAULT_SUB_RE.match(value.strip())
    if not m:
        return None
    return m.group(2)


def _resolve_list_entry(entry: Any) -> Optional[str]:
    """Apply ``_resolve_default_substitution`` to ``KEY=value`` list entries."""
    if not isinstance(entry, str) or "=" not in entry:
        return None
    key, value = entry.split("=", 1)
    resolved = _resolve_default_substitution(key, value)
    if resolved is None:
        return None
    return f"{key}={resolved}"


def _entry_has_shell_ref(entry: Any) -> bool:
    if isinstance(entry, str) and "=" in entry:
        return "${" in entry.split("=", 1)[1]
    if isinstance(entry, dict):
        return "${" in str(entry.get("value", ""))
    return False


# ------------------------------------------------------------------
# Cross-service URL detection (for ``service-ref-rewrites`` annotation)
# ------------------------------------------------------------------


# Captures a ``http(s)://<host>`` reference. The trailing lookahead
# requires the host to be terminated by a port (``:``), path (``/``),
# query (``?``), fragment (``#``), or end-of-string — so ``http://api``
# and ``http://api:8000/path`` match a sibling named ``api``, but
# ``http://api.openai.com/v1`` does NOT (the ``.`` is not a valid
# host-terminator). ``\b`` was previously used here but treats ``.``
# as a word boundary, which falsely rewrites external URLs sharing a
# leading subdomain with a sibling service name (iter-8 review repro:
# sibling ``api`` would hijack ``api.openai.com``).
_URL_HOST_RE = re.compile(
    r"(?P<scheme>https?://)(?P<host>[A-Za-z][A-Za-z0-9_-]*)(?=[:/?#]|$)"
)


def detect_service_url_rewrites(
    transformed_services: Dict[str, Any],
    dev_name: str,
) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Detect cross-service URL references in env values.

    Compose-style cross-service URLs (``http://backend:8000``) work in
    docker-compose because compose creates a DNS alias for each service
    short name. In Kubernetes the operator prefixes service names with
    the deployment ID (``my-app-dev-abc-backend``), so the bare alias
    doesn't resolve.

    This function walks each transformed service's env and finds values
    referencing a SIBLING service by its compose short name. The
    returned map is consumed by ``PayloadBuilder`` and serialized into
    the ``extensions.kamiwaza.io/service-ref-rewrites`` annotation; the
    operator reads that annotation and rewrites the env value at deploy
    time:

        {
          "<service_name>": {
            "<env_key>": {
              "from": "http://backend:8000",
              "to":   "http://my-app-dev-abc-backend:8000"
            }
          }
        }

    Self-references and references to non-sibling hostnames are
    ignored.
    """
    sibling_names = set(transformed_services.keys())
    rewrites: Dict[str, Dict[str, Dict[str, str]]] = {}

    for svc_name, svc in transformed_services.items():
        env = svc.get("environment")
        if not env:
            continue
        for key, value in _iter_env_entries(env):
            new_value = _rewrite_url_hosts(value, sibling_names, svc_name, dev_name)
            if new_value is None or new_value == value:
                continue
            rewrites.setdefault(svc_name, {})[key] = {
                "from": value,
                "to": new_value,
            }
    return rewrites


def _iter_env_entries(env: Any) -> List[Tuple[str, str]]:
    """Yield ``(key, value)`` pairs from either env shape."""
    out: List[Tuple[str, str]] = []
    if isinstance(env, dict):
        for k, v in env.items():
            if isinstance(v, (str, int, float, bool)):
                out.append((str(k), str(v)))
    elif isinstance(env, list):
        for entry in env:
            if isinstance(entry, str) and "=" in entry:
                k, v = entry.split("=", 1)
                out.append((k, v))
            elif isinstance(entry, dict) and "name" in entry and "value" in entry:
                out.append((str(entry["name"]), str(entry["value"])))
    return out


def _rewrite_url_hosts(
    value: str,
    sibling_names: set,
    self_name: str,
    dev_name: str,
) -> Optional[str]:
    """Rewrite each ``http(s)://<sibling>`` host in *value* to the
    deployment-prefixed K8s service name. Returns the rewritten value
    or None when there's nothing to rewrite."""

    def _sub(match: re.Match) -> str:
        host = match.group("host")
        if host == self_name or host not in sibling_names:
            return match.group(0)
        return f"{match.group('scheme')}{dev_name}-{host}"

    new_value = _URL_HOST_RE.sub(_sub, value)
    return new_value if new_value != value else None


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
