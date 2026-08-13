"""In-memory Docker Compose transformation for deployment."""

from __future__ import annotations

import copy
import os
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

# Reuse the validator's bind-mount detection so the "stripped at deploy"
# info message ComposeValidator emits stays in sync with what the
# transformer actually strips (ENG-4956).
from kamiwaza_extensions.validators.compose import is_bind_mount

_IMAGE_BASENAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9._/]{0,126}[a-z0-9])?$")

# Which command is asking for a build ref. The two have opposite
# destination policies for a registry-qualified declared ``image:``, and
# conflating them is what ENG-8626 fixed:
#
# - ``publish`` — the declared namespace IS the destination. An extension
#   may publish beneath an unconventional namespace (Omniparse, ENG-4909);
#   preserve it verbatim so catalog refs match the pushed images.
# - ``dev`` — the declared namespace is the image's *published identity*,
#   not where this cluster pulls from. A service with ``build:`` is one
#   ``kz-ext dev`` is building right now, so it must land in the resolved
#   cluster dev registry; honoring the declared ``ghcr.io`` host pushes an
#   owned dev image to the org registry and deploys a ref the cluster
#   can't pull.
#
# Required (no default) at every entry point: a call site that forgets it
# should fail loudly rather than silently inherit publish semantics —
# exactly the regression ENG-8626 was.
Purpose = Literal["dev", "publish"]


def _tag_separator_index(ref: str) -> int:
    """Return the index of *ref*'s tag-separating colon, or -1 if untagged.

    Two colons can appear before the tag: a registry port
    (``localhost:5000/foo``) and a Compose default-value placeholder
    (``foo:${IMAGE_TAG:-local}``). The port is disambiguated by the last
    ``/``; the placeholder has to win over ``rfind`` because the ``:`` in
    ``:-`` sits *after* the real tag separator.

    Single source of truth for ``_replace_image_tag`` and
    ``_split_image_ref`` — they disagreed once, and the malformed ref that
    produced (``foo:${IMAGE_TAG:2.0.0``) reached the K8s PATCH path.
    """
    last_slash = ref.rfind("/")
    placeholder = ref.find(":${", last_slash + 1)
    if placeholder >= 0:
        return placeholder
    last_colon = ref.rfind(":")
    return last_colon if last_colon > last_slash else -1


def _replace_image_tag(image_ref: str, new_tag: str) -> str:
    """Return *image_ref* with its tag (and any digest) replaced by *new_tag*.

    The namespace (registry + repo path) is preserved verbatim. Handles
    refs that include a registry port (``localhost:5000/foo:tag``) or a
    Compose default-value placeholder (``foo:${IMAGE_TAG:-local}``), and
    strips any ``@sha256:...`` suffix before re-tagging.
    """
    ref = image_ref.split("@", 1)[0]
    separator = _tag_separator_index(ref)
    if separator >= 0:
        ref = ref[:separator]
    return f"{ref}:{new_tag}"


def _looks_registry_qualified(ref: str) -> bool:
    """Return True when *ref*'s first slash-separated component is a registry host.

    Mirrors docker's rule for distinguishing a registry-qualified ref
    (``ghcr.io/...``, ``localhost:5000/...``) from a Docker Hub
    namespace shortcut (``foo/bar``, ``redis``). Without this guard
    ``docker push my-org/api:1.0`` silently goes to Docker Hub while
    the cluster registry expects the same path — guaranteed
    ImagePullBackOff for extensions that use unqualified short refs.

    Predicate: the substring before the first ``/`` must contain ``.``
    or ``:``, or be exactly ``localhost``. Bare repo names (no ``/``)
    are Docker Hub by definition and return False. IPv6 host literals
    like ``[::1]/foo`` and ``[::1]:5000/foo`` are matched via the
    ``:`` rule.
    """
    if "/" not in ref:
        return False
    first, _, _ = ref.partition("/")
    return "." in first or ":" in first or first == "localhost"


def _split_image_ref(image_ref: str) -> Tuple[Optional[str], str, str]:
    """Split *image_ref* into ``(registry, repository, tag)``.

    Mirrors ``_replace_image_tag``'s tag detection (last ``:`` past the
    last ``/``) and ``_looks_registry_qualified``'s registry-host rule.
    Strips any ``@sha256:...`` digest before splitting. Tag defaults to
    ``latest`` when the ref carries no explicit tag.

    Used by the K8s PATCH path so the operator updates registry +
    repository + tag together. Pre-fix kz-ext would PATCH only the tag,
    leaving the CR's image field pointing at the original repository —
    after this PR builds at the canonical (possibly different)
    repository, that mismatch would produce ImagePullBackOff.
    """
    ref = image_ref.split("@", 1)[0]
    separator = _tag_separator_index(ref)
    if separator >= 0:
        namespace = ref[:separator]
        tag = ref[separator + 1 :]
    else:
        namespace = ref
        tag = "latest"
    if _looks_registry_qualified(namespace):
        registry, _, repository = namespace.partition("/")
    else:
        registry = None
        repository = namespace
    return registry, repository, tag


def _repo_part(image_ref: str) -> str:
    """Return ``registry/repository`` from *image_ref*, stripping tag and digest.

    Returns the bare ``repository`` for unqualified refs (no registry host).
    Built on ``_split_image_ref`` so registry-host detection and tag-vs-port
    disambiguation stay consistent with the rest of the ref-handling helpers.

    Used by ``_retag_appgarden_compose`` to identify sibling services whose
    ``image:`` field points at the same registry repo as a service this
    publish actually built — those siblings consume the same retagged +
    digest-pinned ref instead of their source-authored tag.
    """
    registry, repository, _ = _split_image_ref(image_ref)
    return f"{registry}/{repository}" if registry else repository


def compute_canonical_refs(
    source_services: Optional[Dict[str, Any]],
    *,
    purpose: Purpose,
    registry: str,
    extension_name: str,
    revision_tag: str,
    appgarden_services: Optional[Dict[str, Any]] = None,
    image_basename: Optional[str] = None,
) -> Dict[str, str]:
    """Canonical image refs for every buildable service, keyed by service name.

    Single source of truth for the publish and dev pipelines: build,
    push, digest resolution, and the K8s/catalog image refs all read
    from this map so they can't drift. *purpose* selects the destination
    policy — see :data:`Purpose` and ``_canonical_build_ref``.

    Service-image precedence (per service):
    1. ``appgarden_services[name]`` when the key is present (matches
       ``_retag_appgarden_compose``'s behavior — uses presence, not
       truthiness, so an empty mapping still falls through to legacy
       via ``_canonical_build_ref`` rather than reading from source).
    2. Source-compose entry otherwise.

    Profile-gated services (ENG-8626):
    - ``publish`` skips them (matches ``buildable_services`` in
      ``run_publish``) so profile-only helpers don't leak into the push
      list under ``--no-build``. They publish via ``extra_docker_images``.
    - ``dev`` includes them. ``ImageBuilder`` builds every ``build:``
      service regardless of profiles, so omitting them here left the
      builder to synthesize its own legacy ref — which is how Kaizen's
      profiled ``agent`` ended up in the dev registry while its
      non-profiled siblings went to GHCR. Dev owns every image it
      builds, so every image it builds belongs in this map. Callers that
      must not *push* profiled services (``--no-build``) filter on
      profiles themselves; that is a push-policy question, not an
      identity one.

    ``image_basename`` (when present and non-empty) overrides
    ``extension_name`` for the legacy ``{registry}/{basename}-{svc}:{tag}``
    synthesis — needed when the bake target / pushed image basename
    diverges from the kamiwaza.json ``name``.
    """
    appgarden = appgarden_services or {}
    out: Dict[str, str] = {}
    for name, svc in (source_services or {}).items():
        if "build" not in svc:
            continue
        if purpose == "publish" and svc.get("profiles"):
            continue
        # Presence-based: a service that is *declared* in the appgarden
        # compose, even as an empty mapping, is owned by the appgarden
        # path. Truthiness here would silently fall back to source on
        # `services.foo: {}` while _retag_appgarden_compose would write
        # the legacy fallback — recreating the very mismatch this
        # PR fixes.
        lookup = appgarden[name] if name in appgarden else svc
        out[name] = _canonical_build_ref(
            lookup,
            name,
            purpose=purpose,
            registry=registry,
            fallback_extension_name=extension_name,
            revision_tag=revision_tag,
            fallback_image_basename=image_basename,
        )
    return out


def _canonical_build_ref(
    service: Optional[Dict[str, Any]],
    svc_name: str,
    *,
    purpose: Purpose,
    registry: str,
    fallback_extension_name: str,
    revision_tag: str,
    fallback_image_basename: Optional[str] = None,
) -> str:
    """Return the canonical registry image ref for a buildable service.

    Reads ``image`` from *service*. Behavior depends on *purpose* when
    the declared ref names an explicit registry host (``ghcr.io/...``,
    ``localhost:5000/...``):

    - ``publish`` — namespace preserved verbatim, only the tag is
      rewritten to *revision_tag*. The declared namespace is where this
      image actually gets published (ENG-4909).
    - ``dev`` — the registry host is replaced with *registry* (the
      resolved cluster dev registry) and the declared **repository path
      is preserved**, so ``ghcr.io/org/images/api:1.0`` builds and
      pushes as ``{registry}/org/images/api:{revision_tag}``. Keeping the
      repository path (rather than flattening to the legacy
      ``{basename}-{svc}`` form) keeps two declared repos that share a
      basename distinct, and makes the rewrite idempotent (ENG-8626).

    Unqualified refs (``api:latest``, ``my-org/api:1.0``) and
    missing/empty declarations fall back — under *both* purposes — to the
    legacy ``{registry}/{basename}-{svc_name}:{revision_tag}`` form.
    Those would otherwise route ``docker push`` to Docker Hub while the
    cluster registry expects the rewritten path.

    ``fallback_image_basename`` (when truthy) overrides
    ``fallback_extension_name`` as the ``{basename}`` segment. This
    lets a repo whose bake target / pushed image basename diverges
    from its kamiwaza.json ``name`` (e.g. ``name=workroom-manager`` but
    images pushed as ``outcome-d563-workroom-manager-<svc>``) declare
    the override via ``image_basename`` in kamiwaza.json without
    touching the manifest's primary identifier. It applies only to the
    fallback form; a qualified declared ref already carries its identity.

    Shared by ``ComposeTransformer.transform_service``,
    ``_retag_appgarden_compose``, ``ImageBuilder.build``, and
    ``run_publish``'s ``published_refs`` derivation so every site
    that picks a build ref agrees on where the image lives.
    """
    declared = (service or {}).get("image")
    if isinstance(declared, str):
        stripped = declared.strip()
        if stripped and _looks_registry_qualified(stripped):
            if purpose == "publish":
                return _replace_image_tag(stripped, revision_tag)
            # dev: keep the repository identity, relocate the host.
            _declared_registry, repository, _tag = _split_image_ref(stripped)
            return f"{registry}/{repository}:{revision_tag}"
    # Strip whitespace so a malformed-but-truthy override (e.g. "   "
    # — caller forgot to normalize) doesn't synthesize a broken
    # `{registry}/   -svc:tag`. ExtensionDetector + MetadataValidator
    # both normalize blank to None at their layers, but this function
    # is also called directly from tests and downstream call sites
    # that may bypass those normalizers.
    basename = _fallback_image_basename(
        fallback_extension_name,
        fallback_image_basename=fallback_image_basename,
    )
    return f"{registry}/{basename}-{svc_name}:{revision_tag}"


def _fallback_image_basename(
    extension_name: str, *, fallback_image_basename: Optional[str] = None
) -> str:
    """Return a safe basename for legacy fallback image refs."""

    raw = (fallback_image_basename or "").strip() or extension_name
    if _IMAGE_BASENAME_RE.match(raw) and ".." not in raw:
        return raw

    basename = raw.strip().lower()
    basename = re.sub(r"[^a-z0-9._/-]+", "-", basename)
    basename = re.sub(r"-{2,}", "-", basename)
    basename = re.sub(r"\.{2,}", ".", basename)
    basename = basename.strip("-._/")
    basename = basename[:128].strip("-._/")
    return basename or "extension"


# A single Compose substitution. The fallback is greedy so nested defaults
# are parsed after ``_compose_substitution_end`` finds the balanced expression.
_COMPOSE_SUB_RE = re.compile(
    r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])(.*))?\}$",
    re.DOTALL,
)
_COMPOSE_BRACE_RE = re.compile(r"[{}]")
_MAX_COMPOSE_SUBSTITUTION_DEPTH = 100

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
    (
        re.compile(r"postgres|mysql|mariadb", re.I),
        {
            "limits": {"cpus": "0.5", "memory": "512M"},
        },
    ),
    (
        re.compile(r"redis|valkey", re.I),
        {
            "limits": {"cpus": "0.25", "memory": "256M"},
        },
    ),
    (
        re.compile(r"frontend|nginx|caddy", re.I),
        {
            "limits": {"cpus": "2.0", "memory": "1G"},
            "reservations": {"cpus": "0.25", "memory": "256M"},
        },
    ),
]
_DEFAULT_LIMITS: Dict[str, Dict[str, str]] = {
    "limits": {"cpus": "1.0", "memory": "1G"},
}


class ComposeTransformer:
    """Transform a local-dev compose dict into a deployment-ready dict.

    The caller provides the parsed compose dict and receives a new dict
    suitable for building a ``CreateExtension`` payload. Transformations are
    pure; ``resolve_env_placeholders`` additionally reads the process
    environment without modifying it.
    """

    def transform(
        self,
        compose_data: Dict[str, Any],
        extension_name: str,
        revision_tag: str,
        registry: str,
        *,
        purpose: Purpose,
        image_basename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return a deployment-ready copy of *compose_data*.

        Transformations applied per-service:
        1. Strip host port bindings
        2. Strip bind mounts (keep named volumes)
        3. Remove ``build`` contexts
        4. Add / update ``image`` fields with *registry*/*revision_tag*
        5. Add resource limits if missing
        6. Remove ``extra_hosts``, ``container_name``, ``networks`` keys

        *purpose* selects the image-destination policy for buildable
        services — see :data:`Purpose`. It must match the purpose the
        caller passed to ``compute_canonical_refs``, or the deployed
        ``image:`` would name a ref the build/push side never produced.

        ``image_basename`` (when present) overrides ``extension_name`` as
        the prefix in the legacy ``{registry}/{basename}-{svc}:{tag}``
        fallback synthesis; see ``_canonical_build_ref`` for the
        precedence rules.

        Env-value ``${VAR}`` placeholders pass through unchanged. Callers
        shipping the result to a destination that does NOT perform its
        own variable substitution (e.g. a Kubernetes API) must additionally
        call :meth:`resolve_env_placeholders`.
        """
        out = copy.deepcopy(compose_data)

        # Drop services that have a profiles key (local-only services).
        # Note this is about what gets *deployed*: dev still builds and
        # pushes profiled image-only services (Kaizen's agent, launched
        # dynamically via AGENT_SERVER_IMAGE rather than by the operator),
        # so they appear in the dev canonical-ref map but never here.
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
                purpose=purpose,
                image_basename=image_basename,
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
        *,
        purpose: Purpose,
        image_basename: Optional[str] = None,
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

        if had_build:
            # Under publish, the declared image's namespace is the
            # canonical record of where this build's image lives, and
            # publish owns only the tag (stage suffix or --revision SHA).
            # Under dev, the image is one we're building for this cluster,
            # so it is relocated into the dev registry (ENG-8626). Either
            # way, fall back to the legacy {ext}-{svc} convention when no
            # image is declared (auto-generated image fields).
            svc["image"] = _canonical_build_ref(
                svc,
                service_name,
                purpose=purpose,
                registry=registry,
                fallback_extension_name=extension_name,
                revision_tag=revision_tag,
                fallback_image_basename=image_basename,
            )
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
        """Return a copy of *compose_data* with ``${VAR}`` env-value
        placeholders collapsed.

        Apply when the consumer of the transformed compose will NOT
        perform its own variable substitution — e.g. shipping the
        compose straight to a Kubernetes API. K8s reads env values as
        literal strings, so an unresolved ``${VAR:-default}`` reaches
        the pod verbatim.

        Rules (per env var):
        - Host environment values are used with Compose's supported braced
          substitution semantics.
        - ``${VAR:-default}`` / ``${VAR-default}`` for non-platform
          keys → recursively collapsed to a host value or literal default.
        - ``${VAR:+alternate}`` / ``${VAR+alternate}`` → the alternate when
          the corresponding non-empty/set condition is satisfied, else empty.
        - Placeholder-bearing entries whose key starts with ``KAMIWAZA_`` →
          dropped. The kamiwaza-extension operator injects these via ConfigMap
          envFrom; an explicit resolved entry would shadow the cluster-internal
          value.
        - Unset ``${VAR}`` and unsatisfied required forms are dropped.
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

    Three rules:

    1. Non-platform placeholders use the current process environment and
       Compose unset/empty semantics for ``:-``, ``-``, ``:?``, ``?``,
       ``:+``, and ``+``. Embedded substitutions and nested defaults are
       resolved recursively. The cluster deployment carries the resolved
       value through to the pod
       — and ``detect_service_url_rewrites`` (called by
       ``PayloadBuilder``) emits a ``service-ref-rewrites`` annotation
       so the operator can swap cross-service hostnames at deploy time.

    2. Placeholder-bearing entries whose key starts with ``KAMIWAZA_`` → drop.
       The kamiwaza-extension operator injects these via ConfigMap envFrom; an
       explicit resolved entry would shadow the cluster-internal value.
       Compose defaults point to laptop-only addresses
       (``host.docker.internal:7777``) that don't resolve in-cluster anyway.

    3. Unset bare substitutions and unsatisfied required forms are dropped.

    Values without supported substitutions or ``$$`` escapes pass unchanged.
    """
    if isinstance(env, dict):
        out: Dict[str, Any] = {}
        for k, v in env.items():
            if not isinstance(v, str) or "$" not in v:
                out[k] = v
                continue
            resolved = _resolve_env_value(k, v)
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


def _resolve_env_value(key: str, value: str) -> Optional[str]:
    """Resolve Compose substitutions in one environment value."""
    is_platform_key = key.startswith(_PLATFORM_INJECTED_PREFIX)
    if is_platform_key and _has_braced_substitution(value):
        return None
    return _resolve_compose_value(value)


def _has_braced_substitution(value: str) -> bool:
    """Whether *value* contains an unescaped ``${...}`` opener."""
    cursor = 0
    while (dollar := value.find("$", cursor)) >= 0:
        if value.startswith("$$", dollar):
            cursor = dollar + 2
            continue
        if value.startswith("${", dollar):
            return True
        cursor = dollar + 1
    return False


def _resolve_compose_value(value: str, depth: int = 0) -> Optional[str]:
    """Resolve braced expressions while honoring Compose's ``$$`` escape."""
    if depth >= _MAX_COMPOSE_SUBSTITUTION_DEPTH:
        return None
    resolved: List[str] = []
    cursor = 0
    while cursor < len(value):
        dollar = value.find("$", cursor)
        if dollar < 0:
            resolved.append(value[cursor:])
            break
        resolved.append(value[cursor:dollar])
        if value.startswith("$$", dollar):
            resolved.append("$")
            cursor = dollar + 2
            continue
        if not value.startswith("${", dollar):
            resolved.append("$")
            cursor = dollar + 1
            continue
        end = _compose_substitution_end(value, dollar)
        if end is None:
            return None
        substitution = _resolve_compose_substitution(value[dollar : end + 1], depth)
        if substitution is None:
            return None
        resolved.append(substitution)
        cursor = end + 1
    return "".join(resolved)


def _compose_substitution_end(value: str, start: int) -> Optional[int]:
    """Find the closing brace paired with the ``${`` at *start*."""
    depth = 1
    for brace in _COMPOSE_BRACE_RE.finditer(value, start + 2):
        depth += 1 if brace.group() == "{" else -1
        if depth == 0:
            return brace.start()
    # Compose's greedy fallback accepts an unmatched literal ``{`` inside a
    # default. In that case its final ``}`` still closes the substitution.
    final_brace = value.rfind("}", start + 2)
    return final_brace if final_brace >= 0 else None


def _resolve_compose_substitution(value: str, depth: int = 0) -> Optional[str]:
    """Resolve one full substitution using Compose environment semantics."""
    match = _COMPOSE_SUB_RE.match(value)
    if match is None:
        return None
    name, operator, fallback = match.groups()
    is_set = name in os.environ
    host_value = os.environ.get(name, "")
    if operator is None:
        return host_value if is_set else None
    if operator in (":-", ":?") and host_value:
        return host_value
    if operator in ("-", "?") and is_set:
        return host_value
    if operator == ":+":
        return _resolve_compose_value(fallback, depth + 1) if host_value else ""
    if operator == "+":
        return _resolve_compose_value(fallback, depth + 1) if is_set else ""
    if operator in (":?", "?"):
        return None
    return _resolve_compose_value(fallback, depth + 1)


def _resolve_list_entry(entry: Any) -> Optional[Any]:
    """Resolve one string or name/value dict environment-list entry."""
    if isinstance(entry, dict):
        return _resolve_dict_list_entry(entry)
    if not isinstance(entry, str) or "=" not in entry:
        return None
    key, value = entry.split("=", 1)
    resolved = _resolve_env_value(key, value)
    if resolved is None:
        return None
    return f"{key}={resolved}"


def _resolve_dict_list_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve a Kubernetes-style ``name``/``value`` environment entry."""
    name = entry.get("name")
    value = entry.get("value")
    if name is None or not isinstance(value, str):
        return None
    resolved = _resolve_env_value(str(name), value)
    if resolved is None:
        return None
    out = dict(entry)
    out["value"] = resolved
    return out


def _entry_has_shell_ref(entry: Any) -> bool:
    if isinstance(entry, str) and "=" in entry:
        return "$" in entry.split("=", 1)[1]
    if isinstance(entry, dict) and "name" in entry:
        value = entry.get("value")
        return isinstance(value, str) and "$" in value
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


def _strip_host_ports(ports: List[Any]) -> List[Any]:
    """``"3000:3000"`` -> ``"3000"``; ``"8080:3000/tcp"`` -> ``"3000/tcp"``.

    Long-form (compose-spec dict) entries are preserved verbatim except
    for ``published`` / ``host_ip``, which would otherwise re-introduce a
    host port binding through the back door. ENG-5954: long-form is the
    only way to carry L7 protocol hints (``name`` + ``app_protocol``)
    through to the K8s Service.
    """
    result: List[Any] = []
    for port in ports:
        if isinstance(port, dict):
            cleaned = {
                k: v for k, v in port.items() if k not in ("published", "host_ip")
            }
            if cleaned.get("target") is not None:
                result.append(cleaned)
            continue
        s = str(port)
        if ":" in s:
            # Take the part after the last colon (container port + optional protocol)
            container_part = s.rsplit(":", 1)[1]
            result.append(container_part)
        else:
            result.append(s)
    return result


def _strip_bind_mounts(volumes: List[Any]) -> List[str]:
    """Keep named volumes, remove bind mounts.

    String bind-mount detection is delegated to the validator's
    ``is_bind_mount`` so every host-path form ``ComposeValidator``
    flags as "stripped at deploy" (absolute/relative paths, ``~``, and
    Windows drive letters) is actually stripped here. See ENG-4956.
    """
    kept: List[Any] = []
    for vol in volumes:
        if isinstance(vol, dict):
            # Long-form volume — keep named volumes; bind/tmpfs are stripped.
            if vol.get("type") == "volume":
                kept.append(vol)
            continue
        s = str(vol)
        if is_bind_mount(s):
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
