"""Validation for kamiwaza.json metadata files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field

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

# Dockerfile ARGs that pin third-party runtime / base-image versions and
# intentionally don't track the extension's own semver. Excluded from drift
# checks so a clean Dockerfile doesn't emit noisy warnings.
_RUNTIME_VERSION_ARGS = frozenset({
    "NODE_VERSION",
    "PYTHON_VERSION",
    "ALPINE_VERSION",
    "DEBIAN_VERSION",
    "UBUNTU_VERSION",
    "GO_VERSION",
    "BUN_VERSION",
    "RUBY_VERSION",
    "RUST_VERSION",
    "JAVA_VERSION",
    "BASE_IMAGE_VERSION",
})


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
    # Override for the image-ref basename when the bake target / pushed
    # image basename diverges from ``name``. Consumed by
    # ``_canonical_build_ref``'s legacy-fallback synthesis.
    image_basename: Optional[str] = Field(default=None, min_length=1)
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

        # services.<name>.healthCheck shape (ENG-4832).
        service_errors, service_warnings = _check_services_block(data.get("services"))
        errors.extend(service_errors)
        warnings.extend(service_warnings)

        return ValidationResult(passed=len(errors) == 0, errors=errors, warnings=warnings)


_PROBE_SHAPES = ("httpGet", "tcpSocket", "exec", "grpc")
_HEALTHCHECK_KEYS = frozenset(
    {
        *_PROBE_SHAPES,
        "initialDelaySeconds",
        "timeoutSeconds",
        "periodSeconds",
        "successThreshold",
        "failureThreshold",
        "terminationGracePeriodSeconds",
    }
)
_PROBE_KEYS = {
    "httpGet": frozenset({"path", "port", "host", "scheme", "httpHeaders"}),
    "tcpSocket": frozenset({"port", "host"}),
    "exec": frozenset({"command"}),
    "grpc": frozenset({"port", "service"}),
}
_NAMED_PORT_RE = re.compile(r"^[a-z]([-a-z0-9]{0,13}[a-z0-9])?$")


def _check_services_block(services: Any) -> tuple[List[str], List[str]]:
    """Validate the optional ``services`` block in kamiwaza.json (ENG-4832).

    The block is a map of compose service names to per-service overrides.
    Today the only override we read is ``healthCheck``; this checker keeps
    structural mistakes (wrong probe shape, missing port, two shapes at
    once) from reaching the platform CR API as opaque 500s.
    """
    errors: List[str] = []
    warnings: List[str] = []
    if services is None:
        return errors, warnings
    if not isinstance(services, dict):
        errors.append("services must be a JSON object keyed by service name")
        return errors, warnings
    for svc_name, block in services.items():
        if not isinstance(block, dict):
            errors.append(f"services.{svc_name} must be a JSON object")
            continue
        health = block.get("healthCheck")
        if health is None:
            continue
        health_errors, health_warnings = _check_healthcheck_shape(svc_name, health)
        errors.extend(health_errors)
        warnings.extend(health_warnings)
    return errors, warnings


def _check_healthcheck_shape(svc_name: str, health: Any) -> tuple[List[str], List[str]]:
    """Mirror K8s' "exactly one probe shape" rule for a healthCheck block."""
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(health, dict):
        errors.append(f"services.{svc_name}.healthCheck must be a JSON object")
        return errors, warnings
    unknown_health_keys = sorted(set(health) - _HEALTHCHECK_KEYS)
    for key in unknown_health_keys:
        warnings.append(
            f"services.{svc_name}.healthCheck has unknown field '{key}'"
        )
    declared = [name for name in _PROBE_SHAPES if name in health]
    if not declared:
        errors.append(
            f"services.{svc_name}.healthCheck must declare one of: "
            f"{', '.join(_PROBE_SHAPES)}"
        )
        return errors, warnings
    if len(declared) > 1:
        errors.append(
            f"services.{svc_name}.healthCheck declares multiple probe shapes "
            f"({', '.join(declared)}); pick exactly one"
        )
        return errors, warnings
    shape = declared[0]
    value = health[shape]
    if not isinstance(value, dict):
        errors.append(
            f"services.{svc_name}.healthCheck.{shape} must be a JSON object"
        )
        return errors, warnings
    unknown_probe_keys = sorted(set(value) - _PROBE_KEYS[shape])
    for key in unknown_probe_keys:
        warnings.append(
            f"services.{svc_name}.healthCheck.{shape} has unknown field '{key}'"
        )
    if shape in {"httpGet", "tcpSocket", "grpc"}:
        errors.extend(_check_probe_port(svc_name, shape, value))
    elif shape == "exec":
        command = value.get("command")
        if not isinstance(command, list) or not command:
            errors.append(
                f"services.{svc_name}.healthCheck.exec.command must be a non-empty list"
            )
    return errors, warnings


def _check_probe_port(svc_name: str, shape: str, value: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if "port" not in value:
        errors.append(f"services.{svc_name}.healthCheck.{shape} must include 'port'")
        return errors
    port = value["port"]
    if isinstance(port, bool):
        errors.append(
            f"services.{svc_name}.healthCheck.{shape}.port must be an integer "
            "1-65535 or a valid named port"
        )
        return errors
    if isinstance(port, int):
        if not 1 <= port <= 65535:
            errors.append(
                f"services.{svc_name}.healthCheck.{shape}.port must be between 1 and 65535"
            )
        return errors
    if isinstance(port, str):
        if port.isdigit():
            number = int(port)
            if not 1 <= number <= 65535:
                errors.append(
                    f"services.{svc_name}.healthCheck.{shape}.port must be between 1 and 65535"
                )
            return errors
        if not _NAMED_PORT_RE.match(port):
            errors.append(
                f"services.{svc_name}.healthCheck.{shape}.port must be an integer "
                "1-65535 or a valid named port"
            )
        return errors
    errors.append(
        f"services.{svc_name}.healthCheck.{shape}.port must be an integer "
        "1-65535 or a valid named port"
    )
    return errors


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
            name = match.group("name")
            value = match.group("value")
            # Skip well-known third-party runtime ARGs — they pin
            # interpreter/base-image versions that intentionally don't
            # track the extension's own semver, so any "drift" against
            # the manifest is noise.
            if name in _RUNTIME_VERSION_ARGS:
                continue
            if value != version and _looks_like_semver(value):
                warnings.append(
                    f"Version drift: Dockerfile ARG {name}='{value}' "
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

    # package.json — root and any first-level subdir (e.g. frontend/),
    # mirroring what `kz-ext bump` propagates so the validator can spot
    # the same drift the bumper handles.
    for pkg in _candidate_package_jsons(ext_dir):
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        pkg_version = data.get("version")
        if isinstance(pkg_version, str) and pkg_version != version:
            try:
                rel = pkg.relative_to(ext_dir)
            except ValueError:
                rel = pkg
            warnings.append(
                f"Version drift: {rel} version='{pkg_version}' "
                f"but kamiwaza.json version='{version}'"
            )

    return warnings


def _candidate_package_jsons(ext_dir: Path) -> List[Path]:
    candidates: List[Path] = []
    root = ext_dir / "package.json"
    if root.exists():
        candidates.append(root)
    try:
        children = sorted(ext_dir.iterdir())
    except OSError:
        return candidates
    for child in children:
        if (
            not child.is_dir()
            or child.name in {"node_modules", ".git"}
            or child.name.startswith(".")
        ):
            continue
        nested = child / "package.json"
        if nested.exists():
            candidates.append(nested)
    return candidates


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
