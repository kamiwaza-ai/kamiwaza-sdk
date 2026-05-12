"""Validation for kamiwaza.json metadata files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator

from kamiwaza_extensions import __version__
from kamiwaza_extensions.validators.result import ValidationResult


# Semver regex: X.Y.Z with optional pre-release
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# Valid image extensions for preview_image
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}


class KamiwazaMetadata(BaseModel):
    """Pydantic model for kamiwaza.json schema validation."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1)
    version: str
    source_type: Literal["kamiwaza", "public", "user_repo"]
    visibility: Literal["public", "private", "team"]
    description: str = Field(..., min_length=1)
    risk_tier: Literal[0, 1, 2]
    verified: bool

    # Optional fields
    tags: Optional[List[str]] = None
    env_defaults: Optional[Dict[str, str]] = None
    required_env_vars: Optional[List[str]] = None
    preview_image: Optional[str] = None
    kamiwaza_version: Optional[str] = None
    kz_ext_version: Optional[str] = None
    category: Optional[str] = None
    preferred_model_type: Optional[str] = None
    strip_path_prefix: Optional[bool] = None
    # ENG-3890 — stamped by scaffolder, consumed by `kz-ext update` to pick
    # the right TemplateManifest. Optional so existing scaffolds (created
    # before M2) load cleanly; ``update`` requires --bootstrap if missing.
    template_version: Optional[str] = None
    template_shape: Optional[Literal["app", "tool", "service"]] = None
    # PR-86 C4 / option (b) — relative_path → "sha256:<hex>" map of
    # preserve_if_modified file hashes at last write time. ``kz-ext update``
    # consults this to detect "clean since record" files and silently sweep
    # them forward instead of conflict-prompting.
    template_file_hashes: Optional[Dict[str, str]] = None


class MetadataValidator:
    """Validates kamiwaza.json files."""

    def validate(self, path: Path) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []

        # Load JSON
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            return ValidationResult(passed=False, errors=[f"Invalid JSON: {exc}"])
        except FileNotFoundError:
            return ValidationResult(passed=False, errors=[f"File not found: {path}"])

        if not isinstance(data, dict):
            return ValidationResult(passed=False, errors=["kamiwaza.json must be a JSON object"])

        # Validate with Pydantic
        try:
            metadata = KamiwazaMetadata(**data)
        except Exception as exc:
            errors.append(f"Schema validation failed: {exc}")
            return ValidationResult(passed=False, errors=errors)

        # Version format
        if not _SEMVER_RE.match(metadata.version):
            errors.append(f"Invalid version format '{metadata.version}' — must be semver (e.g., 1.0.0)")

        # Naming conventions
        name = metadata.name
        ext_type = data.get("type")
        if ext_type == "tool" and not (name.startswith("tool-") or name.startswith("mcp-")):
            warnings.append(f"Tool extension name '{name}' should start with 'tool-' or 'mcp-'")
        if ext_type == "service" and not name.startswith("service-"):
            warnings.append(f"Service extension name '{name}' should start with 'service-'")

        # Version range fields
        for range_field in ("kamiwaza_version", "kz_ext_version"):
            value = getattr(metadata, range_field, None)
            if value is not None:
                if not _is_valid_specifier_set(value):
                    errors.append(f"Invalid {range_field} range '{value}' — use semver ranges like '>=1.0.0,<2.0.0'")

        # kz_ext_version compatibility check
        if metadata.kz_ext_version:
            if not _check_version_compat(metadata.kz_ext_version, __version__):
                warnings.append(
                    f"CLI version {__version__} is not compatible with kz_ext_version '{metadata.kz_ext_version}'"
                )

        # preview_image checks
        if metadata.preview_image:
            if not metadata.preview_image.startswith("images/"):
                warnings.append(f"preview_image should be under 'images/' directory, got '{metadata.preview_image}'")
            ext = Path(metadata.preview_image).suffix.lower()
            if ext not in _IMAGE_EXTENSIONS:
                warnings.append(f"preview_image has unexpected extension '{ext}'")
            # Check file exists relative to kamiwaza.json
            image_path = path.parent / metadata.preview_image
            if not image_path.exists():
                warnings.append(f"preview_image file not found: {metadata.preview_image}")

        # Version-drift checks: surface mismatches between kamiwaza.json
        # version and the same version recorded in sibling files. Drift
        # silently breaks publishes (manifest claims 2.1.0, image tag still
        # points at 2.0.14) — warn here so it's visible before deploy.
        warnings.extend(_check_version_drift(path.parent, metadata.version, data.get("image")))

        return ValidationResult(passed=len(errors) == 0, errors=errors, warnings=warnings)


def _check_version_drift(
    ext_dir: Path, version: str, manifest_image: Optional[Any]
) -> List[str]:
    # Import locally to share the canonical image-ref parser with the bump
    # command — keeps the updater and the drift detector from diverging on
    # what counts as a "tag" (registry ports, digest suffixes, etc.).
    from kamiwaza_extensions.commands.bump import (
        _split_image_ref,
        extension_image_repo,
    )
    from kamiwaza_extensions.constants import ALL_COMPOSE_FILENAMES

    warnings: List[str] = []
    ext_repo = extension_image_repo(manifest_image)

    # kamiwaza.json image tag
    if isinstance(manifest_image, str):
        _, tag, _ = _split_image_ref(manifest_image)
        if tag is not None and tag != version and _looks_like_semver(tag):
            warnings.append(
                f"Version drift: kamiwaza.json version='{version}' but image tag='{tag}'"
            )

    image_re = re.compile(
        r"""^\s*image\s*:\s*['"]?(?P<ref>\S+?)['"]?\s*(?:\#.*)?$""",
        re.MULTILINE,
    )
    for name in ALL_COMPOSE_FILENAMES:
        compose = ext_dir / name
        if not compose.exists():
            continue
        try:
            content = compose.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in image_re.finditer(content):
            repo, tag, _ = _split_image_ref(match.group("ref"))
            # Only flag images that belong to the extension. Without a
            # manifest image repo we fall back to "semver tag != manifest
            # version" alone, which keeps drift detection useful for
            # manifests that omit `image` but risks noise for unrelated
            # third-party services (e.g. redis:7.2.4); scoping eliminates
            # that noise when the manifest declares its repo.
            if tag is None or tag == version or not _looks_like_semver(tag):
                continue
            if ext_repo is not None and repo != ext_repo:
                continue
            warnings.append(
                f"Version drift: {name} has image tag='{tag}' but kamiwaza.json version='{version}'"
            )
            break  # one warning per compose file is enough signal

    # Dockerfile *_VERSION ARG defaults
    dockerfile = ext_dir / "Dockerfile"
    if dockerfile.exists():
        try:
            content = dockerfile.read_text(encoding="utf-8")
        except OSError:
            content = ""
        arg_re = re.compile(
            r"""^\s*ARG\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*_VERSION)\s*=\s*["']?(?P<value>[^\s"']+)["']?""",
            re.MULTILINE,
        )
        for match in arg_re.finditer(content):
            value = match.group("value")
            if value != version and _looks_like_semver(value):
                warnings.append(
                    f"Version drift: Dockerfile ARG {match.group('name')}='{value}' "
                    f"but kamiwaza.json version='{version}'"
                )

    # pyproject.toml [project] version — scan only inside the [project]
    # table to survive arrays (e.g. `classifiers = [...]`) before the
    # version line.
    pyproject = ext_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
        except OSError:
            content = ""
        pyproject_version = _find_pyproject_version(content)
        if pyproject_version is not None and pyproject_version != version:
            warnings.append(
                f"Version drift: pyproject.toml version='{pyproject_version}' "
                f"but kamiwaza.json version='{version}'"
            )

    return warnings


def _find_pyproject_version(text: str) -> Optional[str]:
    from kamiwaza_extensions.commands.bump import _find_project_table_span

    span = _find_project_table_span(text)
    if span is None:
        return None
    start, end = span
    match = re.search(
        r"""(?m)^version\s*=\s*["'](?P<value>[^"']+)["']""",
        text[start:end],
    )
    return match.group("value") if match else None


def _looks_like_semver(value: str) -> bool:
    return bool(_SEMVER_RE.match(value))


def _is_valid_specifier_set(value: str) -> bool:
    try:
        SpecifierSet(value)
        return True
    except InvalidSpecifier:
        return False


def _check_version_compat(specifier_str: str, version_str: str) -> bool:
    try:
        spec = SpecifierSet(specifier_str)
        ver = Version(version_str)
        return ver in spec
    except (InvalidSpecifier, InvalidVersion):
        return False
