"""Publish-profile management for kz-ext."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Profile names must be safe for use as filenames
_PROFILE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Env var → PublishProfile field mapping
_ENV_OVERRIDES: Dict[str, str] = {
    "KZ_PUBLISH_REGISTRY": "registry",
    "KZ_PUBLISH_CATALOG_ENDPOINT": "catalog_endpoint",
    "KZ_PUBLISH_CATALOG_BUCKET": "catalog_bucket",
    "KZ_PUBLISH_CATALOG_PREFIX": "catalog_prefix",
    "KZ_PUBLISH_CATALOG_CREDENTIALS": "catalog_credentials",
}


@dataclass
class PublishProfile:
    name: str  # Profile identifier (e.g., "dev", "prod")
    registry: str  # Docker registry URL (e.g., "ghcr.io/my-org")
    catalog_endpoint: str  # S3-compatible endpoint URL
    catalog_bucket: str  # Bucket name for catalog JSON
    catalog_credentials: str  # Credential spec: "aws-profile:<name>", "env", "sso"
    catalog_prefix: str = ""  # Key prefix within bucket
    created_at: Optional[str] = None  # ISO timestamp


def _validate_profile_name(name: str) -> None:
    """Validate profile name is safe for filesystem paths."""
    if not _PROFILE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid profile name '{name}'. "
            "Must contain only letters, digits, hyphens, and underscores."
        )


def _secure_dir(path: Path) -> None:
    """Create directory with 700 permissions."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, stat.S_IRWXU)  # 700
    except OSError:
        import warnings
        warnings.warn(
            f"Could not set 700 permissions on {path} — "
            "credential files may be readable by other users",
            stacklevel=2,
        )


def _secure_write(path: Path, data: dict) -> None:
    """Atomically write JSON with 600 permissions.

    Permissions are set on the temp file *before* the rename so the file
    is never world-readable, even briefly.
    """
    _secure_dir(path.parent)
    tmp_path = path.with_suffix(".tmp")
    # Open with restrictive mode from the start (0o600).
    # os.fdopen takes ownership of the fd — do NOT close fd manually after.
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp_path.replace(path)


def _load_profile_file(path: Path) -> Optional[PublishProfile]:
    """Load a single profile JSON file, returning None on error."""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        profile = PublishProfile(**data)
        # Validate required fields are non-empty
        for field in ("name", "registry", "catalog_endpoint", "catalog_bucket", "catalog_credentials"):
            if not getattr(profile, field, None):
                return None
        return profile
    except (json.JSONDecodeError, OSError, TypeError):
        return None


class ProfileManager:
    """Manages publish profiles for kz-ext.

    Storage layout::

        ~/.kamiwaza/profiles/{name}.json          # User-level
        {extension-repo}/.kz-ext/profiles/{name}.json  # Repo-level
    """

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.config_dir = config_dir or Path.home() / ".kamiwaza"
        self._profiles_dir = self.config_dir / "profiles"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_profile(
        self,
        profile: PublishProfile,
        repo_level: bool = False,
        extension_dir: Optional[Path] = None,
    ) -> Path:
        """Write profile to disk. Returns path written."""
        _validate_profile_name(profile.name)

        if repo_level:
            if extension_dir is None:
                raise ValueError("extension_dir is required for repo-level profiles")
            profiles_dir = Path(extension_dir) / ".kz-ext" / "profiles"
        else:
            profiles_dir = self._profiles_dir

        # Stamp created_at if not set
        if profile.created_at is None:
            profile.created_at = datetime.now(timezone.utc).isoformat()

        target = profiles_dir / f"{profile.name}.json"
        _secure_write(target, asdict(profile))
        return target

    def get_profile(
        self, name: str, extension_dir: Optional[Path] = None
    ) -> PublishProfile:
        """Load profile by name (repo-level first, then user-level).

        Raises ValueError if not found.
        """
        _validate_profile_name(name)

        # 1. Repo-level
        if extension_dir is not None:
            repo_path = Path(extension_dir) / ".kz-ext" / "profiles" / f"{name}.json"
            profile = _load_profile_file(repo_path)
            if profile is not None:
                return profile

        # 2. User-level
        user_path = self._profiles_dir / f"{name}.json"
        profile = _load_profile_file(user_path)
        if profile is not None:
            return profile

        raise ValueError(f"Profile '{name}' not found")

    def list_profiles(
        self, extension_dir: Optional[Path] = None
    ) -> List[PublishProfile]:
        """List all available profiles (merged: repo-level wins by name)."""
        profiles: Dict[str, PublishProfile] = {}

        # User-level first (will be overridden by repo-level)
        self._collect_profiles(self._profiles_dir, profiles)

        # Repo-level overrides
        if extension_dir is not None:
            repo_dir = Path(extension_dir) / ".kz-ext" / "profiles"
            self._collect_profiles(repo_dir, profiles)

        return list(profiles.values())

    def list_profiles_with_source(
        self, extension_dir: Optional[Path] = None
    ) -> List[Tuple[PublishProfile, str]]:
        """List all profiles with source indicator ('user' or 'repo').

        Repo-level profiles override user-level by name.
        """
        user_profiles: Dict[str, PublishProfile] = {}
        repo_profiles: Dict[str, PublishProfile] = {}

        self._collect_profiles(self._profiles_dir, user_profiles)

        if extension_dir is not None:
            repo_dir = Path(extension_dir) / ".kz-ext" / "profiles"
            self._collect_profiles(repo_dir, repo_profiles)

        merged: Dict[str, Tuple[PublishProfile, str]] = {}
        for name, profile in user_profiles.items():
            merged[name] = (profile, "user")
        for name, profile in repo_profiles.items():
            merged[name] = (profile, "repo")

        return list(merged.values())

    def delete_profile(
        self,
        name: str,
        repo_level: bool = False,
        extension_dir: Optional[Path] = None,
    ) -> None:
        """Delete a profile. Raises ValueError if not found."""
        _validate_profile_name(name)

        if repo_level:
            if extension_dir is None:
                raise ValueError("extension_dir is required for repo-level profiles")
            target = Path(extension_dir) / ".kz-ext" / "profiles" / f"{name}.json"
        else:
            target = self._profiles_dir / f"{name}.json"

        if not target.exists():
            raise ValueError(f"Profile '{name}' not found")

        target.unlink()

    def resolve_profile(
        self, name: str, extension_dir: Optional[Path] = None
    ) -> PublishProfile:
        """Load profile and apply env var overrides."""
        profile = self.get_profile(name, extension_dir=extension_dir)

        for env_var, field_name in _ENV_OVERRIDES.items():
            value = os.environ.get(env_var)
            if value is not None:
                setattr(profile, field_name, value)

        return profile

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _collect_profiles(
        self, profiles_dir: Path, out: Dict[str, PublishProfile]
    ) -> None:
        """Scan a profiles directory and add/override entries in *out*."""
        if not profiles_dir.is_dir():
            return
        for path in profiles_dir.iterdir():
            if path.suffix != ".json":
                continue
            profile = _load_profile_file(path)
            if profile is not None:
                out[profile.name] = profile
