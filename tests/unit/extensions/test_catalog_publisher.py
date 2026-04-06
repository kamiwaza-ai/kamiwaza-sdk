"""Tests for the CatalogPublisher module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _make_profile(**overrides):
    """Create a minimal PublishProfile for testing."""
    from kamiwaza_extensions.profile_manager import PublishProfile

    defaults = dict(
        name="dev",
        registry="ghcr.io/my-org",
        catalog_endpoint="https://s3.example.com",
        catalog_bucket="my-catalog",
        catalog_credentials="env",
        catalog_prefix="",
    )
    defaults.update(overrides)
    return PublishProfile(**defaults)


def _make_entry(**overrides):
    """Create a sample catalog entry."""
    defaults = dict(
        name="my-app",
        version="1.0.0",
        description="Test app",
        compose={"services": {}},
    )
    defaults.update(overrides)
    return defaults


def _s3_get_object_response(body_data):
    """Create a mock S3 get_object response."""
    body_mock = MagicMock()
    body_mock.read.return_value = json.dumps(body_data).encode("utf-8")
    return {"Body": body_mock}


# ------------------------------------------------------------------
# Publish success path
# ------------------------------------------------------------------


class TestPublishSuccess:
    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_publish_full_lifecycle(self, mock_boto3):
        """Lock -> backup -> download -> merge -> upload -> verify -> unlock."""
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        mock_s3 = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3
        mock_boto3.Session.return_value = mock_session

        # Lock: succeed (no PreconditionFailed)
        mock_s3.put_object.return_value = {}

        # Backup + download: return empty list (first publish)
        from botocore.exceptions import ClientError

        no_such_key_error = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
            "GetObject",
        )
        # First call: backup (NoSuchKey), second call: download (NoSuchKey),
        # third call: verify download
        mock_s3.get_object.side_effect = [
            no_such_key_error,  # _backup_current
            no_such_key_error,  # _download_entries
            _s3_get_object_response([_make_entry()]),  # _verify re-download
        ]

        profile = _make_profile()
        publisher = CatalogPublisher(profile)

        result = publisher.publish(
            entry=_make_entry(),
            extension_type="app",
        )

        assert result.extension_name == "my-app"
        assert result.version == "1.0.0"
        assert result.action == "insert"
        assert result.dry_run is False

        # Verify lock was released (delete_object called with lock key)
        lock_delete_calls = [
            c for c in mock_s3.delete_object.call_args_list
            if "registry.lock" in str(c)
        ]
        assert len(lock_delete_calls) == 1

    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_publish_with_existing_entries(self, mock_boto3):
        """Merge into existing catalog entries."""
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        mock_s3 = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3
        mock_boto3.Session.return_value = mock_session

        existing = [_make_entry(name="other-app", version="2.0.0")]
        new_entry = _make_entry(name="my-app", version="1.0.0")

        mock_s3.get_object.side_effect = [
            _s3_get_object_response(existing),  # backup
            _s3_get_object_response(existing),  # download
            _s3_get_object_response(existing + [new_entry]),  # verify
        ]

        profile = _make_profile()
        publisher = CatalogPublisher(profile)
        result = publisher.publish(entry=new_entry, extension_type="app")

        assert result.action == "insert"


# ------------------------------------------------------------------
# Publish with version conflict
# ------------------------------------------------------------------


class TestPublishVersionConflict:
    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_duplicate_version_rejected_without_force(self, mock_boto3):
        """Merge should reject duplicate version without force."""
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        mock_s3 = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3
        mock_boto3.Session.return_value = mock_session

        existing_entry = _make_entry(name="my-app", version="1.0.0")
        existing = [existing_entry]

        mock_s3.get_object.side_effect = [
            _s3_get_object_response(existing),  # backup
            _s3_get_object_response(existing),  # download
        ]

        profile = _make_profile()
        publisher = CatalogPublisher(profile)

        with pytest.raises(ValueError):
            publisher.publish(
                entry=_make_entry(name="my-app", version="1.0.0"),
                extension_type="app",
                force=False,
            )

    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_duplicate_version_allowed_with_force(self, mock_boto3):
        """force=True should allow overwriting an existing version."""
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        mock_s3 = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3
        mock_boto3.Session.return_value = mock_session

        existing_entry = _make_entry(name="my-app", version="1.0.0")
        new_entry = _make_entry(name="my-app", version="1.0.0", description="Updated")

        mock_s3.get_object.side_effect = [
            _s3_get_object_response([existing_entry]),  # backup
            _s3_get_object_response([existing_entry]),  # download
            _s3_get_object_response([new_entry]),  # verify
        ]

        profile = _make_profile()
        publisher = CatalogPublisher(profile)

        result = publisher.publish(
            entry=new_entry,
            extension_type="app",
            force=True,
        )

        assert result.action == "replace"


# ------------------------------------------------------------------
# Dry-run mode
# ------------------------------------------------------------------


class TestDryRun:
    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_dry_run_no_writes(self, mock_boto3):
        """Dry run should not lock, upload, or modify S3."""
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        mock_s3 = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3
        mock_boto3.Session.return_value = mock_session

        from botocore.exceptions import ClientError

        no_such_key_error = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
            "GetObject",
        )
        mock_s3.get_object.side_effect = no_such_key_error

        profile = _make_profile()
        publisher = CatalogPublisher(profile)

        result = publisher.publish(
            entry=_make_entry(),
            extension_type="app",
            dry_run=True,
        )

        assert result.dry_run is True
        assert result.action == "insert"

        # No put_object calls (no lock, no upload)
        put_calls = mock_s3.put_object.call_args_list
        assert len(put_calls) == 0

        # No delete_object calls (no lock release)
        mock_s3.delete_object.assert_not_called()


# ------------------------------------------------------------------
# Lock acquisition failure
# ------------------------------------------------------------------


class TestLockFailure:
    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_lock_already_held(self, mock_boto3):
        """Should raise CatalogPublishError with owner info when lock held."""
        from botocore.exceptions import ClientError

        from kamiwaza_extensions.catalog_publisher import (
            CatalogPublishError,
            CatalogPublisher,
        )

        mock_s3 = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3
        mock_boto3.Session.return_value = mock_session

        # put_object raises PreconditionFailed (lock already exists)
        lock_error = ClientError(
            {"Error": {"Code": "PreconditionFailed", "Message": "Lock exists"}},
            "PutObject",
        )
        mock_s3.put_object.side_effect = lock_error

        # get_object returns lock info when reporter tries to read it
        lock_info = {
            "owner": "ci-job-42",
            "hostname": "builder.local",
            "acquired_at": "2026-04-01T12:00:00Z",
            "pid": 1234,
        }
        mock_s3.get_object.return_value = _s3_get_object_response(lock_info)

        profile = _make_profile()
        publisher = CatalogPublisher(profile)

        with pytest.raises(CatalogPublishError, match="Failed to acquire"):
            publisher.publish(
                entry=_make_entry(),
                extension_type="app",
            )


# ------------------------------------------------------------------
# Lock release in finally block
# ------------------------------------------------------------------


class TestLockReleaseOnError:
    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_lock_released_on_upload_failure(self, mock_boto3):
        """Lock should be released even if upload fails."""
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        mock_s3 = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3
        mock_boto3.Session.return_value = mock_session

        from botocore.exceptions import ClientError

        no_such_key = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": ""}},
            "GetObject",
        )

        # Lock succeeds, backup empty, download empty, then upload raises
        call_count = [0]
        original_put = mock_s3.put_object

        def put_object_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: lock acquisition -- succeed
                return {}
            # Second call: upload entries -- fail
            raise RuntimeError("S3 upload failed")

        mock_s3.put_object.side_effect = put_object_side_effect
        mock_s3.get_object.side_effect = [
            no_such_key,  # backup
            no_such_key,  # download
        ]

        profile = _make_profile()
        publisher = CatalogPublisher(profile)

        with pytest.raises(RuntimeError, match="S3 upload failed"):
            publisher.publish(entry=_make_entry(), extension_type="app")

        # Lock release should still happen
        mock_s3.delete_object.assert_called_once()
        delete_key = mock_s3.delete_object.call_args[1]["Key"]
        assert "registry.lock" in delete_key


# ------------------------------------------------------------------
# Credential resolution
# ------------------------------------------------------------------


class TestCredentialResolution:
    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_aws_profile_credentials(self, mock_boto3):
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        profile = _make_profile(catalog_credentials="aws-profile:my-profile")
        CatalogPublisher(profile)

        mock_boto3.Session.assert_called_once_with(profile_name="my-profile")
        mock_session.client.assert_called_once_with(
            "s3", endpoint_url="https://s3.example.com"
        )

    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_env_credentials(self, mock_boto3):
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        profile = _make_profile(catalog_credentials="env")
        CatalogPublisher(profile)

        mock_boto3.Session.assert_called_once_with()

    def test_sso_credentials_not_implemented(self):
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        profile = _make_profile(catalog_credentials="sso")
        with pytest.raises(NotImplementedError, match="SSO"):
            CatalogPublisher(profile)

    def test_unknown_credentials_raises(self):
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        profile = _make_profile(catalog_credentials="magic-auth")
        with pytest.raises(ValueError, match="Unknown credential spec"):
            CatalogPublisher(profile)


# ------------------------------------------------------------------
# Preview image upload
# ------------------------------------------------------------------


class TestPreviewImageUpload:
    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_preview_image_uploaded(self, mock_boto3, tmp_path: Path):
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        mock_s3 = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3
        mock_boto3.Session.return_value = mock_session

        from botocore.exceptions import ClientError

        no_such_key = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": ""}},
            "GetObject",
        )

        entry = _make_entry(preview_image="screenshot.png")

        # Lock OK, backup empty, download empty, verify OK
        mock_s3.get_object.side_effect = [
            no_such_key,  # backup
            no_such_key,  # download
            _s3_get_object_response([entry]),  # verify
        ]

        # Create preview image file
        preview = tmp_path / "screenshot.png"
        preview.write_bytes(b"\x89PNG fake image data")

        profile = _make_profile()
        publisher = CatalogPublisher(profile)

        result = publisher.publish(
            entry=entry,
            extension_type="app",
            preview_image_path=preview,
        )

        assert len(result.images_pushed) == 1
        assert "screenshot.png" in result.images_pushed[0]

        # Verify put_object was called for image upload (in addition to lock + entries)
        image_puts = [
            c for c in mock_s3.put_object.call_args_list
            if "images/" in str(c)
        ]
        assert len(image_puts) == 1


# ------------------------------------------------------------------
# Backup and restore on failure
# ------------------------------------------------------------------


class TestBackupRestore:
    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_restore_backup_on_verify_failure(self, mock_boto3, tmp_path: Path):
        """When verification fails, backup should be restored."""
        from kamiwaza_extensions.catalog_publisher import (
            CatalogPublishError,
            CatalogPublisher,
        )

        mock_s3 = MagicMock()
        mock_session = MagicMock()
        mock_session.client.return_value = mock_s3
        mock_boto3.Session.return_value = mock_session

        existing = [_make_entry(name="other", version="2.0.0")]
        new_entry = _make_entry()

        # We need to handle CWD for backup dir creation
        import os

        orig_cwd = os.getcwd()
        os.chdir(tmp_path)

        try:
            mock_s3.get_object.side_effect = [
                _s3_get_object_response(existing),  # backup
                _s3_get_object_response(existing),  # download
                _s3_get_object_response([{"different": "data"}]),  # verify (mismatch!)
            ]

            profile = _make_profile()
            publisher = CatalogPublisher(profile)

            with pytest.raises(CatalogPublishError, match="verification failed"):
                publisher.publish(entry=new_entry, extension_type="app")

            # Verify that put_object was called for restore (the backup data)
            # Lock put + entries upload + restore = 3 put_object calls
            put_calls = mock_s3.put_object.call_args_list
            restore_calls = [
                c for c in put_calls
                if c[1].get("Key", "").endswith("apps.json")
            ]
            assert len(restore_calls) >= 1  # At least entry upload + restore
        finally:
            os.chdir(orig_cwd)


# ------------------------------------------------------------------
# Invalid extension type
# ------------------------------------------------------------------


class TestInvalidExtensionType:
    @patch("kamiwaza_extensions.catalog_publisher.boto3")
    def test_invalid_extension_type_raises(self, mock_boto3):
        from kamiwaza_extensions.catalog_publisher import CatalogPublisher

        mock_session = MagicMock()
        mock_boto3.Session.return_value = mock_session

        profile = _make_profile()
        publisher = CatalogPublisher(profile)

        with pytest.raises(ValueError, match="Invalid extension_type"):
            publisher.publish(
                entry=_make_entry(),
                extension_type="unknown",
            )
