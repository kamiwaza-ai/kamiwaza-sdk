"""Rewrite image refs embedded in compose env values for ``kz-ext dev``.

The dev compose transform rewrites service ``image:`` fields to the
dev-built ref, but image refs that live *inside* env values are a
parallel surface it doesn't touch. Kaizen's backend spawns agent
sandbox pods dynamically from ``AGENT_SERVER_IMAGE`` (and the sandbox
controller gates them against ``SANDBOX_ALLOWED_IMAGE_PREFIXES``); the
compose defaults point at the *released* agent tag, which ``kz-ext dev``
never builds or pushes, so the sandbox pod ImagePullBackOffs (ENG-7110).

This is the dev analog of ENG-5260's publish-side
``registry_builder._apply_env_image_rewrites``. Two differences from the
publish path:

1. Dev rewrites to the **built ref** (where ``ImageBuilder`` actually
   pushed the image), not a digest-pin — the dev tag is unique per build.
2. It must align the ``SANDBOX_ALLOWED_IMAGE_PREFIXES`` allowlist in
   lockstep, because the agent is built into the cluster dev registry
   rather than the ``ghcr.io`` namespace the source whitelist names. The
   platform's ``image_rewrite.py`` encodes the same env-name coupling
   (``IMAGE_ENV_NAMES`` / ``IMAGE_PREFIX_ENV_NAMES``) for the
   offline-relocation install type; this is the dev-tooling equivalent
   for normal (non-relocation) dev clusters.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

from kamiwaza_extensions.compose_transformer import _repo_part

# Env vars whose value is a single container image reference (possibly
# wrapped in a ``${NAME:-default}`` shell default). Mirrors the platform's
# kamiwaza.serving.garden.extensions.image_rewrite.IMAGE_ENV_NAMES.
IMAGE_ENV_NAMES = frozenset({"AGENT_SERVER_IMAGE"})
# Env vars whose value is a comma-separated list of image *prefixes* (not
# full refs) — the sandbox controller's allowlist. Must be aligned in
# lockstep with AGENT_SERVER_IMAGE or the controller rejects the agent.
IMAGE_PREFIX_ENV_NAMES = frozenset({"SANDBOX_ALLOWED_IMAGE_PREFIXES"})

# A value that is exactly one ``${VAR:-default}`` / ``${VAR-default}``
# substitution. Group 1 = ``${VAR:-`` / ``${VAR-``, group 2 = default,
# group 3 = ``}``. ``[^}]+`` keeps a digest's ``@sha256:...`` inside the
# default. Mirrors registry_builder._ENV_DEFAULT_SUB_RE.
_DEFAULT_SUB_RE = re.compile(r"\A(\$\{[A-Za-z_][A-Za-z0-9_]*:?-)([^}]+)(\})\Z")


def build_image_ref_map(
    source_services: Optional[Dict[str, Any]],
    canonical_refs: Dict[str, str],
) -> Dict[str, str]:
    """Map each build-context service's declared image repo to its built ref.

    Keyed by ``registry/repository`` (tag/digest stripped) so an env ref
    that pins a *different* tag than the source ``image:`` field (e.g.
    ``AGENT_SERVER_IMAGE`` defaulting to the released ``:2.0.2`` while the
    service declares the same repo) still matches.

    The built ref is read straight from *canonical_refs*, which under
    ``purpose="dev"`` covers every build-context service — profiled
    ``image-only`` ones (the agent) included. This function used to
    re-derive a legacy ``{registry}/{basename}-{name}:{tag}`` fallback for
    exactly those profiled services, because they were absent from the map;
    that made it a fourth site that had to stay in lockstep with
    ``ImageBuilder``, and it is why the agent landed on a different
    repository path than its siblings (ENG-8626). A build service missing
    from the map is skipped rather than assigned an invented ref: the env
    ref must equal what was actually built and pushed, and a ref nobody
    built is worse than no rewrite at all.
    """
    out: Dict[str, str] = {}
    for name, svc in (source_services or {}).items():
        if not isinstance(svc, dict) or "build" not in svc:
            continue
        declared = svc.get("image")
        if not isinstance(declared, str) or not declared.strip():
            continue
        built = canonical_refs.get(name)
        if not built:
            continue
        out[_repo_part(declared.strip())] = built
    return out


def rewrite_env_image_refs(
    compose: Dict[str, Any],
    ref_map: Dict[str, str],
) -> Dict[str, Any]:
    """Return a copy of *compose* with image-bearing env values rewritten.

    Walks every service's ``environment`` (dict and ``KEY=value`` list
    shapes) and, keyed on env name:

    - ``AGENT_SERVER_IMAGE`` (an image ref, bare or ``${VAR:-default}``):
      rewrite the ref to its built ref when its repo is in *ref_map*.
    - ``SANDBOX_ALLOWED_IMAGE_PREFIXES`` (a CSV of repo prefixes): append
      the built repo prefix for each entry that names a built repo,
      preserving the originals.

    A no-op when *ref_map* is empty. The caller's dict is not mutated.
    """
    out = copy.deepcopy(compose)
    if not ref_map:
        return out
    for svc in (out.get("services") or {}).values():
        if not isinstance(svc, dict):
            continue
        env = svc.get("environment")
        if isinstance(env, dict):
            for key, val in list(env.items()):
                if isinstance(val, str):
                    env[key] = _rewrite_env_value(key, val, ref_map)
        elif isinstance(env, list):
            for i, entry in enumerate(env):
                if not isinstance(entry, str) or "=" not in entry:
                    continue
                key, val = entry.split("=", 1)
                new_val = _rewrite_env_value(key.strip(), val, ref_map)
                if new_val != val:
                    env[i] = f"{key}={new_val}"
    return out


def _rewrite_env_value(name: str, value: str, ref_map: Dict[str, str]) -> str:
    """Dispatch an env value to the rewriter for its env name."""
    if name in IMAGE_ENV_NAMES:
        return _rewrite_image_ref_value(value, ref_map)
    if name in IMAGE_PREFIX_ENV_NAMES:
        return _rewrite_prefix_csv_value(value, ref_map)
    return value


def _rewrite_image_ref_value(value: str, ref_map: Dict[str, str]) -> str:
    """Rewrite a single image-ref env value (bare or ``${VAR:-default}``).

    The ``${VAR:-default}`` form is preserved so a runtime override still
    wins; only the literal default is rewritten.
    """
    match = _DEFAULT_SUB_RE.match(value.strip())
    if match:
        prefix, default, brace = match.group(1), match.group(2), match.group(3)
        new_ref = _rewrite_ref(default.strip(), ref_map)
        if new_ref == default.strip():
            return value
        return f"{prefix}{new_ref}{brace}"
    return _rewrite_ref(value.strip(), ref_map)


def _rewrite_ref(ref: str, ref_map: Dict[str, str]) -> str:
    """Return the built ref for *ref*'s repo, or *ref* unchanged."""
    if not ref:
        return ref
    return ref_map.get(_repo_part(ref), ref)


def _rewrite_prefix_csv_value(value: str, ref_map: Dict[str, str]) -> str:
    """Append built repo prefixes to an allowlist CSV, in lockstep.

    Handles both the bare CSV (K8s payload, post ``resolve_env_placeholders``)
    and the ``${VAR:-csv}`` form (catalog overlay, placeholders preserved) —
    the same two shapes :func:`_rewrite_image_ref_value` handles. Without the
    ``${VAR:-default}`` unwrap, a templated allowlist would be CSV-split with
    the ``${…:-`` prefix / trailing ``}`` still attached, no entry would match
    *ref_map*, and the lockstep append would silently drop — while
    ``AGENT_SERVER_IMAGE`` (which IS unwrapped) still gets rewritten, defeating
    the lockstep invariant for any extension that templates the allowlist.
    """
    match = _DEFAULT_SUB_RE.match(value.strip())
    if match:
        prefix, default, brace = match.group(1), match.group(2), match.group(3)
        new_csv = _append_built_prefixes(default, ref_map)
        if new_csv == default:
            return value
        return f"{prefix}{new_csv}{brace}"
    return _append_built_prefixes(value, ref_map)


def _append_built_prefixes(csv: str, ref_map: Dict[str, str]) -> str:
    """Append built repo prefixes to a bare allowlist CSV.

    Each entry that exactly names a built repo gets that repo's built prefix
    appended (the built ref with its tag stripped). Originals are preserved
    verbatim — some flows may still reference the source path, and an
    over-broad replace could drop a still-valid prefix. Bare namespace
    prefixes (``ghcr.io/openhands/``) name no built repo and pass through,
    matching the platform's full-repo-only relocation rule.
    """
    entries = [part.strip() for part in csv.split(",")]
    existing = set(entries)
    additions: List[str] = []
    for entry in entries:
        built = ref_map.get(entry)
        if built is None:
            continue
        built_prefix = _repo_part(built)
        if built_prefix not in existing and built_prefix not in additions:
            additions.append(built_prefix)
    if not additions:
        return csv
    return ",".join(entries + additions)
