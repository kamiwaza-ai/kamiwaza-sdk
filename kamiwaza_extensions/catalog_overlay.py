"""Local catalog overlay client — ENG-6802.

``kz-ext dev`` publishes the in-development build as a catalog overlay
("shadow") on the connected cluster so NEW workrooms launched via the
workroom manager pick up the dev build instead of the upstream catalog
entry. The overlay is written through the cluster's own authenticated API
(``PUT /apps/app_templates/catalog/overlay/{name}``); existing digest-pinned
workrooms are unaffected.

HARD INVARIANT (ENG-6802): the dev path — this module plus
``commands/dev.py`` — must never import ``catalog_publisher``,
``profile_manager``, or ``boto3``. The overlay can only target the
connected cluster's API; there is structurally no code path by which
``kz-ext dev`` can write a shared R2 catalog (dev-info/stage-info/info),
even with a misconfigured publish profile. Enforced by
``tests/unit/extensions/test_catalog_overlay.py::TestImportInvariant``.
"""

from __future__ import annotations

import copy
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import yaml

OVERLAY_PATH = "/apps/app_templates/catalog/overlay"

# Matches the platform's app_templates.version column (String(40)).
MAX_VERSION_LENGTH = 40

_BRANCH_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def get_git_branch(cwd: Optional[str] = None) -> Optional[str]:
    """Return the current git branch name, or None outside a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    # Detached HEAD reports the literal string "HEAD" — not a branch.
    return branch if branch and branch != "HEAD" else None


def build_overlay_version(
    base_version: str,
    *,
    branch: Optional[str],
    sha: Optional[str],
    dirty: bool,
) -> str:
    """Shadow version: ``{base}-dev.{branch_slug}.{sha7|dirty}``.

    Distinguishable from any catalog version (shadow, don't impersonate),
    and intentionally NOT PEP440-parseable — the platform's version
    comparison treats unparseable versions as never-replaceable, a second
    guard against catalog syncs clobbering the shadow.
    """
    sha_part = "dirty" if dirty else (sha or "nogit")[:7]
    slug = _BRANCH_SLUG_RE.sub("-", (branch or "nobranch").lower()).strip("-")

    # Fit within the platform's 40-char version column. The branch slug is
    # the elastic part; the sha (the uniqueness component) is never cut, so
    # for pathologically long base versions the base itself is trimmed.
    overhead = len("-dev.") + 1 + len(".") + len(sha_part)  # 1 = min slug char
    base = base_version[: max(MAX_VERSION_LENGTH - overhead, 1)]
    slug_budget = MAX_VERSION_LENGTH - len(base) - len("-dev..") - len(sha_part)
    slug = slug[: max(slug_budget, 1)].strip("-") or "x"
    return f"{base}-dev.{slug}.{sha_part}"


def build_overlay_entry(
    *,
    version: str,
    transformed_compose: Dict[str, Any],
    canonical_refs: Dict[str, str],
    push_ref_map: Optional[Dict[str, str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    git_sha: Optional[str] = None,
    git_branch: Optional[str] = None,
    dirty: bool = False,
    resolve_digest: Optional[Callable[[str], str]] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Build the overlay entry payload from the dev deploy's own artifacts.

    The compose is the SAME transformed compose ``run_dev_remote`` deploys
    — its image refs are the exact in-cluster-resolvable refs running pods
    pull — with each built service ref digest-pinned to match the catalog
    format. Digest resolution queries the registry via the host-reachable
    push ref (``push_ref_map``) but pins the digest onto the canonical
    in-cluster ref. Degrades to tag-only refs with a warning when the
    digest cannot be resolved; the revision tag is unique per build, so
    the entry stays unambiguous.
    """
    compose = copy.deepcopy(transformed_compose)
    services = compose.get("services") or {}
    push_ref_map = push_ref_map or {}

    for service_name, canonical_ref in canonical_refs.items():
        svc = services.get(service_name)
        if not isinstance(svc, dict) or svc.get("image") != canonical_ref:
            continue
        if resolve_digest is None:
            continue
        lookup_ref = push_ref_map.get(canonical_ref, canonical_ref)
        try:
            digest = resolve_digest(lookup_ref)
        except Exception as exc:
            if warn is not None:
                warn(
                    f"could not resolve digest for {lookup_ref}: {exc} — "
                    "overlay entry will use the tag-only ref"
                )
            continue
        svc["image"] = f"{canonical_ref}@{digest}"

    metadata = metadata or {}
    entry: Dict[str, Any] = {
        "version": version,
        "compose_yml": yaml.safe_dump(compose, sort_keys=False),
        "shadow": {
            "git_sha": git_sha,
            "git_branch": git_branch,
            "dirty": dirty,
        },
    }
    env_defaults = metadata.get("env_defaults")
    if isinstance(env_defaults, dict):
        # JSON-faithful string coercion: booleans render lowercase, as a
        # JSON-serialized published catalog entry would carry them.
        entry["env_defaults"] = {
            k: (str(v).lower() if isinstance(v, bool) else str(v))
            for k, v in env_defaults.items()
        }
    if isinstance(metadata.get("env_metadata"), dict):
        entry["env_metadata"] = metadata["env_metadata"]
    # Platform-consumed catalog metadata: forward everything kamiwaza.json
    # declares so the shadow template behaves like a published catalog entry
    # would (required_env_vars gates launch env, strip_path_prefix shapes
    # routing, etc.). Presence-based: an explicit empty list is a deliberate
    # clear and must reach the platform; only ABSENT keys keep the
    # pre-shadow row values.
    for key, expected_type in (
        ("display_name", str),
        ("description", str),
        ("category", str),
        ("author", str),
        ("license", str),
        ("homepage", str),
        ("image", str),
        ("kamiwaza_version", str),
        ("preferred_model_type", str),
        ("preferred_model_name", str),
        ("tags", list),
        ("capabilities", list),
        ("required_env_vars", list),
        ("strip_path_prefix", bool),
        ("fail_if_model_type_unavailable", bool),
        ("fail_if_model_name_unavailable", bool),
    ):
        value = metadata.get(key)
        if isinstance(value, expected_type):
            entry[key] = value
    ext_type = metadata.get("type") or metadata.get("template_type")
    if isinstance(ext_type, str) and ext_type in ("app", "tool", "service"):
        entry["template_type"] = ext_type
    return entry


def publish_overlay(client: Any, name: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """PUT the overlay entry. Returns the apply response dict."""
    response = client.put(f"{OVERLAY_PATH}/{quote(name, safe='')}", json=entry)
    return response if isinstance(response, dict) else {}


def remove_overlay(client: Any, name: str) -> Dict[str, Any]:
    """DELETE the overlay entry. Returns the remove response dict."""
    response = client.delete(f"{OVERLAY_PATH}/{quote(name, safe='')}")
    return response if isinstance(response, dict) else {}


def list_overlays(client: Any) -> List[Dict[str, Any]]:
    """GET all active overlays on the connected cluster."""
    response = client.get(OVERLAY_PATH)
    return response if isinstance(response, list) else []
