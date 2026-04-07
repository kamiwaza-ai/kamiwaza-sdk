"""Generate and merge catalog JSON entries for the Kamiwaza extension registry.

Each catalog file (``apps.json``, ``tools.json``) is a bare JSON array of
entry objects.  This module handles building individual entries from
extension metadata and transformed compose data, rewriting image tags for
the target deployment stage, and merging entries into an existing catalog
with version-constraint-aware conflict resolution.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version


# Stage suffixes applied to image tags whose registry prefix matches.
_STAGE_SUFFIXES = {
    "prod": "",
    "stage": "-stage",
    "dev": "-dev",
}

# Synthetic versions used to probe specifier overlap.  We test a broad
# range so that "pragmatic disjointness" works for real-world Kamiwaza
# version constraints.  Covers 0.0.0 through 19.9.2 (600 versions).
_PROBE_VERSIONS = [
    Version(f"{major}.{minor}.{patch}")
    for major in range(20)
    for minor in range(10)
    for patch in range(3)
]


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
    ) -> Dict[str, Any]:
        """Generate a catalog entry dict from *metadata* and *transformed_compose*.

        Args:
            metadata: Parsed ``kamiwaza.json`` contents.
            transformed_compose: Compose dict already processed by
                ``ComposeTransformer``.
            registry: Docker registry prefix (e.g. ``"kamiwazaai"``).
            version: Semver version string for this release.
            stage: One of ``"prod"``, ``"stage"``, ``"dev"``.

        Returns:
            A dict matching the Kamiwaza catalog entry schema.
        """
        compose_yml = yaml.dump(transformed_compose, default_flow_style=False)
        compose_yml = self.transform_image_tags(compose_yml, registry, version, stage)
        # Parse back to dict for reliable image extraction (avoids env var false positives)
        tagged_compose = yaml.safe_load(compose_yml) or {}
        docker_images = self.extract_docker_images(tagged_compose)

        extra_images = metadata.get("extra_docker_images") or []
        if extra_images:
            docker_images = list(dict.fromkeys(docker_images + extra_images))

        entry: Dict[str, Any] = {
            "name": metadata.get("name", ""),
            "version": version,
            "description": metadata.get("description", ""),
            "source_type": metadata.get("source_type", "kamiwaza"),
            "visibility": metadata.get("visibility", "public"),
            "compose_yml": compose_yml,
            "docker_images": docker_images,
        }

        # Optional fields -- only include when present in metadata.
        kamiwaza_version = metadata.get("kamiwaza_version")
        if kamiwaza_version:
            entry["kamiwaza_version"] = kamiwaza_version

        preview_image = metadata.get("preview_image")
        if preview_image:
            entry["preview_image"] = _normalize_preview_image(preview_image)

        risk_tier = metadata.get("risk_tier")
        if risk_tier is not None:
            entry["risk_tier"] = risk_tier

        for optional_key in ("tags", "category", "verified"):
            val = metadata.get(optional_key)
            if val is not None:
                entry[optional_key] = val

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
            raise ValueError(
                f"Entry '{name}' version {entry_version} already exists. "
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
    ) -> str:
        """Rewrite image tags in a compose YAML string for the target stage.

        Only images whose prefix matches *registry* are transformed.
        External images (postgres, redis, etc.) pass through unchanged.

        Tag format per stage:
        - ``prod``: ``{registry}/{name}:{version}``
        - ``stage``: ``{registry}/{name}:{version}-stage``
        - ``dev``: ``{registry}/{name}:{version}-dev``
        - Custom stages (e.g., ``staging``): ``{registry}/{name}:{version}-staging``
        """
        suffix = _STAGE_SUFFIXES.get(stage, f"-{stage}")
        escaped = re.escape(registry)
        # Match image lines: "image: registry/name:tag"
        # Capture the registry/name portion, then replace the tag.
        pattern = re.compile(
            rf"(image:\s*){escaped}(/[^:\s]+):([^\s]+)"
        )
        new_tag = f"{version}{suffix}"
        return pattern.sub(rf"\g<1>{registry}\2:{new_tag}", compose_yml)

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
    a_matches = {v for v in _PROBE_VERSIONS if v in a}
    b_matches = {v for v in _PROBE_VERSIONS if v in b}

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
