"""Tests for the ProfileManager module."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from kamiwaza_extensions.profile_manager import ProfileManager, PublishProfile

pytestmark = pytest.mark.unit


def _make_profile(**overrides) -> PublishProfile:
    """Create a PublishProfile with reasonable defaults."""
    defaults = dict(
        name="dev",
        registry="ghcr.io/my-org",
        catalog_endpoint="https://s3.example.com",
        catalog_bucket="my-catalog",
        catalog_credentials="env",
    )
    defaults.update(overrides)
    return PublishProfile(**defaults)


# ------------------------------------------------------------------
# save_profile + get_profile round-trip
# ------------------------------------------------------------------


class TestSaveAndGetProfile:
    def test_round_trip_user_level(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        profile = _make_profile(name="dev")

        saved = mgr.save_profile(profile)
        assert saved.exists()

        loaded = mgr.get_profile("dev")
        assert loaded.name == "dev"
        assert loaded.registry == "ghcr.io/my-org"
        assert loaded.catalog_endpoint == "https://s3.example.com"
        assert loaded.catalog_bucket == "my-catalog"
        assert loaded.catalog_credentials == "env"
        assert loaded.created_at is not None

    def test_round_trip_repo_level(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        ext_dir = tmp_path / "my-extension"
        ext_dir.mkdir()

        profile = _make_profile(name="staging")
        saved = mgr.save_profile(profile, repo_level=True, extension_dir=ext_dir)
        assert saved.exists()
        assert ".kz-ext" in str(saved)

        loaded = mgr.get_profile("staging", extension_dir=ext_dir)
        assert loaded.name == "staging"

    def test_created_at_stamped_on_save(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        profile = _make_profile(name="ts-test")
        assert profile.created_at is None

        mgr.save_profile(profile)
        loaded = mgr.get_profile("ts-test")
        assert loaded.created_at is not None

    def test_save_repo_level_requires_extension_dir(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        profile = _make_profile()
        with pytest.raises(ValueError, match="extension_dir is required"):
            mgr.save_profile(profile, repo_level=True, extension_dir=None)


# ------------------------------------------------------------------
# Profile name validation
# ------------------------------------------------------------------


class TestProfileNameValidation:
    @pytest.mark.parametrize("bad_name", [
        "has space",
        "has/slash",
        "has.dot",
        "has@at",
        "../traversal",
        "",
    ])
    def test_rejects_invalid_names(self, tmp_path: Path, bad_name: str):
        mgr = ProfileManager(config_dir=tmp_path)
        profile = PublishProfile(
            name=bad_name,
            registry="r",
            catalog_endpoint="e",
            catalog_bucket="b",
            catalog_credentials="env",
        )
        with pytest.raises(ValueError, match="Invalid profile name"):
            mgr.save_profile(profile)

    @pytest.mark.parametrize("good_name", [
        "dev",
        "prod",
        "my-profile",
        "profile_v2",
        "UPPER",
        "mix123-_abc",
    ])
    def test_accepts_valid_names(self, tmp_path: Path, good_name: str):
        mgr = ProfileManager(config_dir=tmp_path)
        profile = _make_profile(name=good_name)
        saved = mgr.save_profile(profile)
        assert saved.exists()


# ------------------------------------------------------------------
# Resolution order: repo-level overrides user-level
# ------------------------------------------------------------------


class TestResolutionOrder:
    def test_repo_level_overrides_user_level(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()

        # Save at user level
        user_profile = _make_profile(name="shared", registry="user-registry")
        mgr.save_profile(user_profile, repo_level=False)

        # Save at repo level with different registry
        repo_profile = _make_profile(name="shared", registry="repo-registry")
        mgr.save_profile(repo_profile, repo_level=True, extension_dir=ext_dir)

        # Get should return repo-level
        loaded = mgr.get_profile("shared", extension_dir=ext_dir)
        assert loaded.registry == "repo-registry"

    def test_falls_back_to_user_level(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()

        user_profile = _make_profile(name="only-user", registry="user-registry")
        mgr.save_profile(user_profile, repo_level=False)

        loaded = mgr.get_profile("only-user", extension_dir=ext_dir)
        assert loaded.registry == "user-registry"


# ------------------------------------------------------------------
# list_profiles merges user + repo level (repo wins)
# ------------------------------------------------------------------


class TestListProfiles:
    def test_lists_user_profiles(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        mgr.save_profile(_make_profile(name="dev"))
        mgr.save_profile(_make_profile(name="prod"))

        profiles = mgr.list_profiles()
        names = {p.name for p in profiles}
        assert names == {"dev", "prod"}

    def test_merges_user_and_repo(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()

        mgr.save_profile(_make_profile(name="dev", registry="user-reg"))
        mgr.save_profile(_make_profile(name="staging", registry="user-staging"))
        mgr.save_profile(
            _make_profile(name="dev", registry="repo-reg"),
            repo_level=True,
            extension_dir=ext_dir,
        )

        profiles = mgr.list_profiles(extension_dir=ext_dir)
        by_name = {p.name: p for p in profiles}
        assert "dev" in by_name
        assert "staging" in by_name
        # Repo wins for 'dev'
        assert by_name["dev"].registry == "repo-reg"
        # User-only 'staging' still present
        assert by_name["staging"].registry == "user-staging"

    def test_list_empty(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        assert mgr.list_profiles() == []


# ------------------------------------------------------------------
# delete_profile
# ------------------------------------------------------------------


class TestDeleteProfile:
    def test_delete_user_profile(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        mgr.save_profile(_make_profile(name="doomed"))
        assert mgr.get_profile("doomed") is not None

        mgr.delete_profile("doomed")
        with pytest.raises(ValueError, match="not found"):
            mgr.get_profile("doomed")

    def test_delete_repo_profile(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()

        mgr.save_profile(
            _make_profile(name="doomed"),
            repo_level=True,
            extension_dir=ext_dir,
        )
        mgr.delete_profile("doomed", repo_level=True, extension_dir=ext_dir)

        with pytest.raises(ValueError, match="not found"):
            mgr.get_profile("doomed", extension_dir=ext_dir)

    def test_delete_nonexistent_raises(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        with pytest.raises(ValueError, match="not found"):
            mgr.delete_profile("nope")

    def test_delete_repo_level_requires_extension_dir(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        with pytest.raises(ValueError, match="extension_dir is required"):
            mgr.delete_profile("x", repo_level=True, extension_dir=None)


# ------------------------------------------------------------------
# resolve_profile applies env var overrides
# ------------------------------------------------------------------


class TestResolveProfile:
    def test_env_var_overrides_registry(self, tmp_path: Path, monkeypatch):
        mgr = ProfileManager(config_dir=tmp_path)
        mgr.save_profile(_make_profile(name="dev", registry="orig"))

        monkeypatch.setenv("KZ_PUBLISH_REGISTRY", "override-registry")

        resolved = mgr.resolve_profile("dev")
        assert resolved.registry == "override-registry"

    def test_env_var_overrides_catalog_endpoint(self, tmp_path: Path, monkeypatch):
        mgr = ProfileManager(config_dir=tmp_path)
        mgr.save_profile(_make_profile(name="dev"))

        monkeypatch.setenv("KZ_PUBLISH_CATALOG_ENDPOINT", "https://override.endpoint")

        resolved = mgr.resolve_profile("dev")
        assert resolved.catalog_endpoint == "https://override.endpoint"

    def test_env_var_overrides_catalog_bucket(self, tmp_path: Path, monkeypatch):
        mgr = ProfileManager(config_dir=tmp_path)
        mgr.save_profile(_make_profile(name="dev"))

        monkeypatch.setenv("KZ_PUBLISH_CATALOG_BUCKET", "override-bucket")

        resolved = mgr.resolve_profile("dev")
        assert resolved.catalog_bucket == "override-bucket"

    def test_env_var_overrides_catalog_prefix(self, tmp_path: Path, monkeypatch):
        mgr = ProfileManager(config_dir=tmp_path)
        mgr.save_profile(_make_profile(name="dev"))

        monkeypatch.setenv("KZ_PUBLISH_CATALOG_PREFIX", "v3/")

        resolved = mgr.resolve_profile("dev")
        assert resolved.catalog_prefix == "v3/"

    def test_env_var_overrides_credentials(self, tmp_path: Path, monkeypatch):
        mgr = ProfileManager(config_dir=tmp_path)
        mgr.save_profile(_make_profile(name="dev"))

        monkeypatch.setenv("KZ_PUBLISH_CATALOG_CREDENTIALS", "aws-profile:prod")

        resolved = mgr.resolve_profile("dev")
        assert resolved.catalog_credentials == "aws-profile:prod"

    def test_multiple_env_overrides(self, tmp_path: Path, monkeypatch):
        mgr = ProfileManager(config_dir=tmp_path)
        mgr.save_profile(_make_profile(name="dev"))

        monkeypatch.setenv("KZ_PUBLISH_REGISTRY", "new-reg")
        monkeypatch.setenv("KZ_PUBLISH_CATALOG_BUCKET", "new-bucket")

        resolved = mgr.resolve_profile("dev")
        assert resolved.registry == "new-reg"
        assert resolved.catalog_bucket == "new-bucket"
        # Non-overridden fields unchanged
        assert resolved.catalog_endpoint == "https://s3.example.com"

    def test_no_env_returns_original(self, tmp_path: Path, monkeypatch):
        mgr = ProfileManager(config_dir=tmp_path)
        mgr.save_profile(_make_profile(name="dev"))

        # Ensure none of the override vars are set
        for var in [
            "KZ_PUBLISH_REGISTRY",
            "KZ_PUBLISH_CATALOG_ENDPOINT",
            "KZ_PUBLISH_CATALOG_BUCKET",
            "KZ_PUBLISH_CATALOG_PREFIX",
            "KZ_PUBLISH_CATALOG_CREDENTIALS",
        ]:
            monkeypatch.delenv(var, raising=False)

        resolved = mgr.resolve_profile("dev")
        assert resolved.registry == "ghcr.io/my-org"

    def test_resolve_nonexistent_raises(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        with pytest.raises(ValueError, match="not found"):
            mgr.resolve_profile("missing")


# ------------------------------------------------------------------
# File permissions
# ------------------------------------------------------------------


class TestFilePermissions:
    def test_saved_file_has_600_permissions(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        saved = mgr.save_profile(_make_profile(name="secure"))

        mode = saved.stat().st_mode
        # Check owner read/write, no group/other
        assert mode & stat.S_IRUSR  # owner read
        assert mode & stat.S_IWUSR  # owner write
        assert not (mode & stat.S_IRGRP)  # no group read
        assert not (mode & stat.S_IWGRP)  # no group write
        assert not (mode & stat.S_IROTH)  # no other read
        assert not (mode & stat.S_IWOTH)  # no other write


# ------------------------------------------------------------------
# Corrupt / missing profile files
# ------------------------------------------------------------------


class TestCorruptAndMissingFiles:
    def test_get_missing_profile_raises(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        with pytest.raises(ValueError, match="not found"):
            mgr.get_profile("nonexistent")

    def test_corrupt_json_file_skipped(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir(parents=True)

        # Write invalid JSON
        (profiles_dir / "corrupt.json").write_text("NOT VALID JSON {{{")

        # Should not appear in list
        profiles = mgr.list_profiles()
        assert len(profiles) == 0

    def test_corrupt_json_get_returns_next_level(self, tmp_path: Path):
        """Corrupt repo-level file should fall back to user-level."""
        mgr = ProfileManager(config_dir=tmp_path)
        ext_dir = tmp_path / "ext"
        ext_dir.mkdir()

        # Save valid user-level
        mgr.save_profile(_make_profile(name="fallback", registry="user-reg"))

        # Write corrupt repo-level
        repo_profiles = ext_dir / ".kz-ext" / "profiles"
        repo_profiles.mkdir(parents=True)
        (repo_profiles / "fallback.json").write_text("{not json")

        loaded = mgr.get_profile("fallback", extension_dir=ext_dir)
        assert loaded.registry == "user-reg"

    def test_non_dict_json_skipped(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir(parents=True)

        # Write a JSON array instead of object
        (profiles_dir / "bad.json").write_text('["not", "a", "dict"]')

        profiles = mgr.list_profiles()
        assert len(profiles) == 0

    def test_non_json_files_ignored(self, tmp_path: Path):
        mgr = ProfileManager(config_dir=tmp_path)
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir(parents=True)

        # Write a .txt file -- should be skipped
        (profiles_dir / "readme.txt").write_text("some notes")

        # Also save a valid profile
        mgr.save_profile(_make_profile(name="real"))

        profiles = mgr.list_profiles()
        assert len(profiles) == 1
        assert profiles[0].name == "real"
