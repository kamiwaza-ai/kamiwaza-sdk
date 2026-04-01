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

        return ValidationResult(passed=len(errors) == 0, errors=errors, warnings=warnings)


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
