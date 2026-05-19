"""Generate and merge catalog JSON entries for the Kamiwaza extension registry.

Each catalog file (``apps.json``, ``tools.json``) is a bare JSON array of
entry objects.  This module handles building individual entries from
extension metadata and transformed compose data, rewriting image tags for
the target deployment stage, and merging entries into an existing catalog
with version-constraint-aware conflict resolution.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version


# Stage suffixes applied to image tags whose registry prefix matches.
_STAGE_SUFFIXES = {
    "prod": "",
    "stage": "-stage",
    "dev": "-dev",
}

# Lazy-initialized probe versions for constraint overlap detection.
# Built on first use to avoid ~50ms import-time cost.
_PROBE_VERSIONS: Optional[List[Version]] = None


def _get_probe_versions() -> List[Version]:
    """Return (and lazily create) synthetic versions 0.0.0–29.19.9."""
    global _PROBE_VERSIONS
    if _PROBE_VERSIONS is None:
        _PROBE_VERSIONS = [
            Version(f"{major}.{minor}.{patch}")
            for major in range(30)
            for minor in range(20)
            for patch in range(10)
        ]
    return _PROBE_VERSIONS


class RegistryBuilder:
    """Build catalog entries and merge them into registry arrays."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_entry(
        self,
        metadata: Dict[str, Any],
        transformed_compose: Dict[str, Any],
        registry: str,
        version: str,
        stage: str = "prod",
        revision: Optional[str] = None,
        digest_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Generate a catalog entry dict from *metadata* and *transformed_compose*.

        Args:
            metadata: Parsed ``kamiwaza.json`` contents.
            transformed_compose: Compose dict already processed by
                ``ComposeTransformer`` (which is the canonical source of
                image-tag rewriting — services with a ``build`` context
                are tagged with publish's revision; services without one
                keep their declared image ref verbatim).
            registry: Docker registry prefix (e.g. ``"kamiwazaai"``).
            version: Semver version string for this release.
            stage: One of ``"prod"``, ``"stage"``, ``"dev"``, or any custom name.
                Currently unused in the body — ``ComposeTransformer`` already
                applied the stage-derived tag to buildable services. Kept on
                the signature for call-site compatibility with prior callers
                of ``RegistryBuilder``.
            revision: Optional revision identifier. When provided, included
                as a top-level ``revision`` field on the entry; consumed by
                ``CatalogDedupGuard`` to make CI re-publishes idempotent.
            digest_map: Optional mapping of rewritten image ref
                (``"<registry>/<ext>-<svc>:<tag>"``) to its OCI manifest
                digest (``"sha256:..."``). When provided, matching service
                ``image`` fields in the rendered ``compose_yml`` and the
                ``docker_images`` list are rewritten to ``ref@digest`` for
                immutable identity (ENG-4370). Refs not in the map (e.g.
                pass-through external/postgres images, prebuilt-internal
                images) are left untouched.

        Returns:
            A dict matching the Kamiwaza catalog entry schema.
        """
        if digest_map:
            transformed_compose = _apply_digests(transformed_compose, digest_map)

        # sort_keys=False preserves service key order from the source compose.
        # Downstream consumers infer primary-service selection from the order
        # services appear in compose, so alphabetizing here silently flips
        # which service the platform routes to.
        compose_yml = yaml.dump(
            transformed_compose, default_flow_style=False, sort_keys=False
        )
        docker_images = self.extract_docker_images(transformed_compose)

        extra_images = metadata.get("extra_docker_images") or []
        if extra_images:
            # Apply the same digest-pinning rule to extras so a service
            # ref that's redundantly listed in `extra_docker_images`
            # collapses against its already-pinned compose copy during
            # dedup, instead of leaking an unpinned duplicate.
            #
            # Match is exact-string against the post-stage-suffix ref
            # (e.g. `<reg>/<ext>-<svc>:<version>-dev`); a pre-suffix
            # entry like `<reg>/<ext>-<svc>:<version>` won't collapse.
            # Author the entry to match what compose carries after
            # transform.
            if digest_map:
                extra_images = [
                    f"{img}@{digest_map[img]}"
                    if img in digest_map and "@" not in img
                    else img
                    for img in extra_images
                ]
            docker_images = list(dict.fromkeys(docker_images + extra_images))

        # Source kamiwaza.json is the catalog contract: every top-level
        # field the developer authored reaches the catalog entry. The
        # platform's _update_template_from_remote
        # (kamiwaza/serving/garden/apps/templates.py) reads
        # env_defaults, required_env_vars, capabilities, display_name,
        # strip_path_prefix, and friends via `.get(field, default)` —
        # any field a slim entry omits silently degrades to {} / [] /
        # None on the platform side, breaking required-env validation,
        # env-default injection, and UI metadata. Curating the entry
        # down to a known-fields subset has bitten us before; don't.
        entry: Dict[str, Any] = copy.deepcopy(metadata)
        entry["name"] = metadata.get("name", "")
        entry["version"] = version
        entry.setdefault("description", "")
        entry.setdefault("source_type", "kamiwaza")
        entry.setdefault("visibility", "public")
        entry["compose_yml"] = compose_yml
        entry["docker_images"] = docker_images

        # `revision` is owned exclusively by the publish-time parameter,
        # never by source kamiwaza.json. Pop first so a stale value in
        # metadata (or a catalog entry re-fed as metadata) can't leak
        # through and trip CatalogDedupGuard with a revision that was
        # never used to tag the images.
        entry.pop("revision", None)
        if revision is not None:
            entry["revision"] = revision

        if entry.get("preview_image"):
            entry["preview_image"] = _normalize_preview_image(entry["preview_image"])

        return entry

    def merge_into_registry(
        self,
        entry: Dict[str, Any],
        existing_entries: List[Dict[str, Any]],
        force: bool = False,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Merge *entry* into *existing_entries*.

        Returns:
            A 2-tuple of ``(merged_list, action)`` where *action* is one of
            ``"insert"``, ``"replace"``, or ``"reject"``.

        Raises:
            ValueError: When the merge is rejected (constraint overlap or
                duplicate version without *force*).
        """
        name = entry.get("name", "")
        entry_constraint = entry.get("kamiwaza_version")
        entry_version = Version(entry["version"])

        # Find all existing entries with the same name.
        matches: List[Tuple[int, Dict[str, Any]]] = [
            (i, e) for i, e in enumerate(existing_entries) if e.get("name") == name
        ]

        if not matches:
            return existing_entries + [entry], "insert"

        # v2 merge: constraint-aware
        if entry_constraint:
            return self._merge_with_constraints(
                entry, entry_constraint, entry_version, matches,
                existing_entries, force,
            )

        # v1 merge: no constraints, single entry per name.
        # Defensively handle multiple matches: find the highest existing
        # version among them, apply the same accept/reject logic, and then
        # remove *all* matched indices before appending the new entry.
        best_version = max(Version(m["version"]) for _, m in matches)

        if entry_version > best_version:
            matched_indices = {i for i, _ in matches}
            result = [
                e for i, e in enumerate(existing_entries)
                if i not in matched_indices
            ]
            result.append(entry)
            return result, "replace"

        if entry_version == best_version:
            if force:
                matched_indices = {i for i, _ in matches}
                result = [
                    e for i, e in enumerate(existing_entries)
                    if i not in matched_indices
                ]
                result.append(entry)
                return result, "replace"
            # Surface the existing entry's revision when it carries one, so
            # callers passing --revision against a previously-revisioned
            # entry get a clear "you'd be replacing rev X with rev Y" message
            # rather than a flat "already exists". The catalog still holds one
            # entry per (name, semver) per the design — different revisions of
            # the same semver are mutually exclusive (§4.2.5).
            existing_revs = sorted({
                str(m.get("revision")) for _, m in matches
                if m.get("revision") is not None
            })
            new_rev = entry.get("revision")
            if existing_revs:
                rev_msg = (
                    f" at revision {existing_revs[0]!r}"
                    if len(existing_revs) == 1
                    else f" (revisions: {existing_revs})"
                )
                if new_rev is not None and new_rev not in existing_revs:
                    rev_msg += f"; this publish carries revision {new_rev!r}"
            else:
                rev_msg = ""
            raise ValueError(
                f"Entry '{name}' version {entry_version} already exists{rev_msg}. "
                "Use force=True to overwrite."
            )

        raise ValueError(
            f"Entry '{name}' has newer version {best_version} in registry "
            f"(attempted {entry_version})."
        )

    def transform_image_tags(
        self,
        compose_yml: str,
        registry: str,
        version: str,
        stage: str,
        extension_name: str = "",
    ) -> str:
        """Rewrite image tags in a compose YAML string for the target stage.

        Only images matching ``{registry}/{extension_name}-*`` are
        transformed.  Shared org images (``{registry}/other-thing:2.0``)
        and external images (postgres, redis) are left unchanged.

        If *extension_name* is empty, falls back to matching all images
        with the *registry* prefix (legacy behaviour).

        Refs of the form ``image:tag@sha256:<digest>`` keep the digest;
        only the tag portion is rewritten. Digest-only refs of the form
        ``image@sha256:<digest>`` (no tag) are left untouched — the
        repo-path char class excludes ``@`` so the regex can't capture
        ``@sha256`` as part of the service name and treat the hex as a
        tag.
        """
        suffix = _STAGE_SUFFIXES.get(stage, f"-{stage}")
        new_tag = f"{version}{suffix}"
        escaped_reg = re.escape(registry)

        if extension_name:
            escaped_ext = re.escape(extension_name)
            # Only match: registry/extension-name-service:tag[@sha256:...]
            pattern = re.compile(
                rf"(image:\s*){escaped_reg}/({escaped_ext}-[^:\s@]+)"
                rf":([^\s@]+)(@sha256:[a-f0-9]{{64}})?"
            )
            return pattern.sub(
                rf"\g<1>{registry}/\2:{new_tag}\g<4>", compose_yml,
            )

        # Fallback: match any image with the registry prefix
        pattern = re.compile(
            rf"(image:\s*){escaped_reg}(/[^:\s@]+):([^\s@]+)(@sha256:[a-f0-9]{{64}})?"
        )
        return pattern.sub(rf"\g<1>{registry}\2:{new_tag}\g<4>", compose_yml)

    def extract_docker_images(
        self, compose_data_or_yml: Any,
    ) -> List[str]:
        """Extract all service image references from compose data.

        Accepts either a compose dict or a YAML string.  When a dict is
        provided, images are read directly from ``services[*].image``
        fields — avoiding false positives from environment variables or
        comments that happen to contain the word ``image:``.

        Returns a deduplicated list preserving first-occurrence order.
        """
        # Dict path — preferred
        if isinstance(compose_data_or_yml, dict):
            images: List[str] = []
            seen: set[str] = set()
            for _svc_name, svc in (compose_data_or_yml.get("services") or {}).items():
                img = svc.get("image")
                if img and img not in seen:
                    seen.add(img)
                    images.append(img)
            return images

        # String fallback (legacy callers)
        compose_yml = str(compose_data_or_yml)
        pattern = re.compile(r"^\s+image:\s*(.+)", re.MULTILINE)
        images = []
        seen = set()
        for match in pattern.finditer(compose_yml):
            img = match.group(1).strip()
            if img and img not in seen:
                images.append(img)
                seen.add(img)
        return images

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _merge_with_constraints(
        self,
        entry: Dict[str, Any],
        entry_constraint: str,
        entry_version: Version,
        matches: List[Tuple[int, Dict[str, Any]]],
        existing_entries: List[Dict[str, Any]],
        force: bool,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Handle v2 merge where entries carry ``kamiwaza_version`` constraints.

        Iterates *all* same-name matches, classifies each relationship,
        and collects indices to replace.  Rejects if any match is a subset
        or partial overlap.
        """
        entry_spec = SpecifierSet(entry_constraint)

        # Indices to remove (equal, superset, or unconstrained matches).
        replace_indices: List[int] = []

        for idx, existing in matches:
            existing_constraint = existing.get("kamiwaza_version")
            if not existing_constraint:
                # Existing entry has no constraint -- treat as universal.
                replace_indices.append(idx)
                continue

            existing_spec = SpecifierSet(existing_constraint)
            relationship = _constraint_relationship(entry_spec, existing_spec)

            if relationship == "disjoint":
                continue  # No conflict with this existing entry.

            if relationship == "equal" or relationship == "superset":
                replace_indices.append(idx)
                continue

            # subset or partial overlap -- reject
            raise ValueError(
                f"Entry '{entry.get('name')}' constraint '{entry_constraint}' "
                f"overlaps with existing constraint '{existing_constraint}'. "
                "Constraints must be disjoint or identical."
            )

        if not replace_indices:
            # No overlap with any existing entry -- insert alongside.
            return existing_entries + [entry], "insert"

        # Validate version against the highest version being replaced.
        best_version = max(
            Version(existing_entries[i]["version"]) for i in replace_indices
        )
        if entry_version < best_version:
            raise ValueError(
                f"Entry '{entry.get('name')}' has newer version {best_version} "
                f"in registry (attempted {entry_version})."
            )
        if entry_version == best_version and not force:
            raise ValueError(
                f"Entry '{entry.get('name')}' version {entry_version} with "
                f"constraint '{entry.get('kamiwaza_version')}' already exists. "
                "Use force=True to overwrite."
            )

        # Remove all replaced indices and append the new entry.
        indices_to_remove = set(replace_indices)
        result = [
            e for i, e in enumerate(existing_entries)
            if i not in indices_to_remove
        ]
        result.append(entry)
        return result, "replace"


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _apply_digests(
    compose: Dict[str, Any], digest_map: Dict[str, str],
) -> Dict[str, Any]:
    """Return a deep copy of *compose* with image refs digest-pinned.

    For each ``services[*].image`` whose value matches a key in
    *digest_map* and does not already carry a ``@`` digest suffix,
    rewrite the value to ``"<image>@<digest>"``. Other refs (external
    pass-through, prebuilt-internal, already-digest-pinned) are left
    untouched. Caller's *compose* dict is not mutated.
    """
    result = copy.deepcopy(compose)
    for svc in (result.get("services") or {}).values():
        img = svc.get("image")
        if img and "@" not in img and img in digest_map:
            svc["image"] = f"{img}@{digest_map[img]}"
    return result


def _normalize_preview_image(path: str) -> str:
    """Ensure preview image path has ``images/`` prefix."""
    if path.startswith("images/"):
        return path
    # Strip leading "./" or "/" prefix (not lstrip which strips characters).
    stripped = path
    while stripped.startswith("./"):
        stripped = stripped[2:]
    stripped = stripped.lstrip("/")
    if stripped.startswith("images/"):
        return stripped
    return f"images/{stripped}"


def _constraint_relationship(
    a: SpecifierSet, b: SpecifierSet,
) -> str:
    """Classify the relationship between two specifier sets.

    Returns one of: ``"equal"``, ``"disjoint"``, ``"superset"``,
    ``"subset"``, ``"overlap"``.

    Uses a pragmatic probe-based approach: test a range of synthetic
    versions against both specifier sets and classify based on the
    overlap of satisfied versions.
    """
    probes = _get_probe_versions()
    a_matches = {v for v in probes if v in a}
    b_matches = {v for v in probes if v in b}

    # If either specifier matched nothing in our probe range, we cannot
    # reliably classify.  Treat as overlap (safe — forces manual review).
    if not a_matches or not b_matches:
        return "overlap"

    if a_matches == b_matches:
        return "equal"

    intersection = a_matches & b_matches
    if not intersection:
        return "disjoint"

    if b_matches <= a_matches:
        return "superset"  # a is a superset of b

    if a_matches <= b_matches:
        return "subset"  # a is a subset of b

    return "overlap"
