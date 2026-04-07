"""Tests for kz-ext config publish-profile command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

pytestmark = pytest.mark.unit


def _write_profile(profiles_dir: Path, name: str, **overrides) -> Path:
    """Write a minimal profile JSON for testing."""
    data = {
        "name": name,
        "registry": "ghcr.io/test",
        "catalog_endpoint": "https://s3.example.com",
        "catalog_bucket": "test-bucket",
        "catalog_credentials": "env",
        "catalog_prefix": "",
        "created_at": "2026-01-01T00:00:00Z",
    }
    data.update(overrides)
    profiles_dir.mkdir(parents=True, exist_ok=True)
    path = profiles_dir / f"{name}.json"
    path.write_text(json.dumps(data))
    return path


class TestCreateProfile:
    def test_creates_profile(self, tmp_path):
        from kamiwaza_extensions.commands.config import publish_profile
        from kamiwaza_extensions.profile_manager import ProfileManager

        with patch("kamiwaza_extensions.profile_manager.ProfileManager") as mock_cls:
            mock_mgr = MagicMock()
            mock_mgr.save_profile.return_value = tmp_path / "dev.json"
            mock_cls.return_value = mock_mgr

            publish_profile(
                name="dev",
                registry="ghcr.io/org",
                catalog_endpoint="https://s3.example.com",
                catalog_bucket="bucket",
                catalog_credentials="env",
            )

            mock_mgr.save_profile.assert_called_once()
            saved = mock_mgr.save_profile.call_args[0][0]
            assert saved.name == "dev"
            assert saved.registry == "ghcr.io/org"

    def test_missing_name_exits(self):
        from kamiwaza_extensions.commands.config import publish_profile

        with pytest.raises((SystemExit, typer.Exit)):
            publish_profile(
                name=None,
                registry="ghcr.io/org",
                catalog_endpoint="https://s3.example.com",
                catalog_bucket="bucket",
                catalog_credentials="env",
            )

    def test_missing_required_fields_exits(self):
        from kamiwaza_extensions.commands.config import publish_profile

        with pytest.raises((SystemExit, typer.Exit)):
            publish_profile(
                name="dev",
                registry=None,
                catalog_endpoint=None,
                catalog_bucket=None,
                catalog_credentials=None,
            )


class TestListProfiles:
    def test_lists_profiles(self, tmp_path):
        from kamiwaza_extensions.commands.config import _list_profiles
        from kamiwaza_extensions.profile_manager import ProfileManager, PublishProfile

        mgr = MagicMock()
        mgr.list_profiles_with_source.return_value = [
            (PublishProfile(name="dev", registry="r", catalog_endpoint="e", catalog_bucket="b", catalog_credentials="env"), "user"),
        ]

        _list_profiles(mgr)
        mgr.list_profiles_with_source.assert_called_once()

    def test_empty_list(self):
        from kamiwaza_extensions.commands.config import _list_profiles

        mgr = MagicMock()
        mgr.list_profiles_with_source.return_value = []

        _list_profiles(mgr)  # Should not raise


class TestShowProfile:
    def test_shows_profile(self):
        from kamiwaza_extensions.commands.config import _show_profile
        from kamiwaza_extensions.profile_manager import PublishProfile

        mgr = MagicMock()
        mgr.get_profile.return_value = PublishProfile(
            name="dev", registry="r", catalog_endpoint="e",
            catalog_bucket="b", catalog_credentials="env",
        )

        _show_profile(mgr, "dev")
        mgr.get_profile.assert_called_once()

    def test_not_found_exits(self):
        from kamiwaza_extensions.commands.config import _show_profile

        mgr = MagicMock()
        mgr.get_profile.side_effect = ValueError("not found")

        with pytest.raises((SystemExit, typer.Exit)):
            _show_profile(mgr, "missing")


class TestDeleteProfile:
    def test_deletes_profile(self):
        from kamiwaza_extensions.commands.config import _delete_profile

        mgr = MagicMock()
        _delete_profile(mgr, "dev")
        mgr.delete_profile.assert_called_once()

    def test_not_found_exits(self):
        from kamiwaza_extensions.commands.config import _delete_profile

        mgr = MagicMock()
        mgr.delete_profile.side_effect = ValueError("not found")

        with pytest.raises((SystemExit, typer.Exit)):
            _delete_profile(mgr, "missing")
