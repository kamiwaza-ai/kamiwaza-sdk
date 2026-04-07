"""Tests for the kz-ext publish command."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest
from click.exceptions import Exit as ClickExit

pytestmark = pytest.mark.unit


def _make_extension_info(
    tmp_path: Path,
    name: str = "my-app",
    version: str = "1.0.0",
    metadata: Optional[Dict[str, Any]] = None,
    compose_data: Optional[Dict[str, Any]] = None,
) -> Any:
    """Create a mock ExtensionInfo."""
    from kamiwaza_extensions.extension_detector import ExtensionInfo

    if metadata is None:
        metadata = {"name": name, "version": version, "description": "Test app"}
    if compose_data is None:
        compose_data = {
            "services": {
                "backend": {
                    "build": {"context": "."},
                    "image": f"my-org/{name}-backend:{version}",
                    "ports": ["8000"],
                },
            },
        }
    return ExtensionInfo(
        path=tmp_path,
        name=name,
        version=version,
        metadata=metadata,
        compose_path=tmp_path / "docker-compose.yml",
        compose_data=compose_data,
    )


def _make_validation_result(passed=True, errors=None, warnings=None):
    """Create a mock ValidationResult."""
    from kamiwaza_extensions.validators.result import ValidationResult

    return ValidationResult(
        passed=passed,
        errors=errors or [],
        warnings=warnings or [],
    )


def _make_publish_result(**overrides):
    """Create a mock PublishResult."""
    from kamiwaza_extensions.catalog_publisher import PublishResult

    defaults = dict(
        extension_name="my-app",
        version="1.0.0",
        action="insert",
        registry_url="ghcr.io/my-org",
        catalog_file="garden/v2/apps.json",
        images_pushed=[],
        dry_run=False,
    )
    defaults.update(overrides)
    return PublishResult(**defaults)


def _make_profile():
    """Create a mock PublishProfile."""
    from kamiwaza_extensions.profile_manager import PublishProfile

    return PublishProfile(
        name="dev",
        registry="ghcr.io/my-org",
        catalog_endpoint="https://s3.example.com",
        catalog_bucket="my-catalog",
        catalog_credentials="env",
    )


# ------------------------------------------------------------------
# Happy path: full orchestration
# ------------------------------------------------------------------


class TestPublishHappyPath:
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_full_publish_success(
        self,
        mock_detector_cls,
        mock_meta_validator_cls,
        mock_compose_validator_cls,
        mock_transformer_cls,
        mock_profile_mgr_cls,
        mock_builder_cls,
        mock_pusher_cls,
        mock_reg_builder_cls,
        mock_publisher_cls,
        tmp_path,
    ):
        # Extension detection
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(tmp_path)
        mock_detector_cls.return_value = mock_detector

        # Validation passes
        mock_meta_validator = MagicMock()
        mock_meta_validator.validate.return_value = _make_validation_result()
        mock_meta_validator_cls.return_value = mock_meta_validator

        mock_compose_validator = MagicMock()
        mock_compose_validator.validate.return_value = _make_validation_result()
        mock_compose_validator_cls.return_value = mock_compose_validator

        # Profile
        mock_profile_mgr = MagicMock()
        mock_profile_mgr.resolve_profile.return_value = _make_profile()
        mock_profile_mgr_cls.return_value = mock_profile_mgr

        # Compose transform
        mock_transformer = MagicMock()
        mock_transformer.transform.return_value = {
            "services": {
                "backend": {"image": "ghcr.io/my-org/my-app-backend:1.0.0"}
            }
        }
        mock_transformer_cls.return_value = mock_transformer

        # Build
        mock_image_builder = MagicMock()
        mock_image_builder.build.return_value = [
            "ghcr.io/my-org/my-app-backend:1.0.0"
        ]
        mock_builder_cls.return_value = mock_image_builder

        # Push
        mock_pusher = MagicMock()
        mock_pusher_cls.return_value = mock_pusher

        # Registry builder
        mock_reg_builder = MagicMock()
        mock_reg_builder.build_entry.return_value = {
            "name": "my-app",
            "version": "1.0.0",
        }
        mock_reg_builder_cls.return_value = mock_reg_builder

        # Catalog publisher
        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result()
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        # Verify orchestration
        mock_detector.detect.assert_called_once()
        mock_meta_validator.validate.assert_called_once()
        mock_compose_validator.validate.assert_called_once()
        mock_profile_mgr.resolve_profile.assert_called_once_with(
            "dev", extension_dir=tmp_path
        )
        mock_transformer.transform.assert_called_once()
        mock_image_builder.build.assert_called_once()
        mock_pusher.push.assert_called_once()
        mock_reg_builder.build_entry.assert_called_once()
        mock_publisher.publish.assert_called_once()


# ------------------------------------------------------------------
# Dry-run mode
# ------------------------------------------------------------------


class TestPublishDryRun:
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_dry_run_no_build_push_publish(
        self,
        mock_detector_cls,
        mock_meta_validator_cls,
        mock_compose_validator_cls,
        mock_transformer_cls,
        mock_profile_mgr_cls,
        mock_builder_cls,
        mock_pusher_cls,
        mock_reg_builder_cls,
        mock_publisher_cls,
        tmp_path,
    ):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(tmp_path)
        mock_detector_cls.return_value = mock_detector

        mock_meta_validator = MagicMock()
        mock_meta_validator.validate.return_value = _make_validation_result()
        mock_meta_validator_cls.return_value = mock_meta_validator

        mock_compose_validator = MagicMock()
        mock_compose_validator.validate.return_value = _make_validation_result()
        mock_compose_validator_cls.return_value = mock_compose_validator

        mock_profile_mgr = MagicMock()
        mock_profile_mgr.resolve_profile.return_value = _make_profile()
        mock_profile_mgr_cls.return_value = mock_profile_mgr

        mock_transformer = MagicMock()
        mock_transformer.transform.return_value = {"services": {}}
        mock_transformer_cls.return_value = mock_transformer

        # Dry-run still calls registry builder + catalog publisher to check for conflicts
        mock_reg_builder = MagicMock()
        mock_reg_builder.build_entry.return_value = {"name": "my-app", "version": "1.0.0"}
        mock_reg_builder_cls.return_value = mock_reg_builder

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result(dry_run=True)
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", dry_run=True)

        # Build and push should NOT be called
        mock_builder_cls.return_value.build.assert_not_called()
        mock_pusher_cls.return_value.push.assert_not_called()

        # But registry builder and catalog publisher ARE called (with dry_run=True)
        mock_reg_builder.build_entry.assert_called_once()
        mock_publisher.publish.assert_called_once()
        # Verify dry_run=True was passed
        call_kwargs = mock_publisher.publish.call_args[1]
        assert call_kwargs.get("dry_run") is True


# ------------------------------------------------------------------
# Validation failure aborts before build
# ------------------------------------------------------------------


class TestPublishValidationFailure:
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_validation_errors_abort(
        self,
        mock_detector_cls,
        mock_meta_validator_cls,
        mock_compose_validator_cls,
        mock_transformer_cls,
        mock_profile_mgr_cls,
        mock_builder_cls,
        tmp_path,
    ):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(tmp_path)
        mock_detector_cls.return_value = mock_detector

        # Metadata validation fails
        mock_meta_validator = MagicMock()
        mock_meta_validator.validate.return_value = _make_validation_result(
            passed=False,
            errors=["Missing required field: name"],
        )
        mock_meta_validator_cls.return_value = mock_meta_validator

        mock_compose_validator = MagicMock()
        mock_compose_validator.validate.return_value = _make_validation_result()
        mock_compose_validator_cls.return_value = mock_compose_validator

        from kamiwaza_extensions.commands.publish import run_publish

        with pytest.raises((SystemExit, ClickExit)):
            run_publish(stage="dev")

        # Build should never be called
        mock_builder_cls.return_value.build.assert_not_called()
        mock_profile_mgr_cls.return_value.resolve_profile.assert_not_called()


# ------------------------------------------------------------------
# --no-build and --no-push flags
# ------------------------------------------------------------------


class TestNoBuildNoPush:
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_no_build_skips_build(
        self,
        mock_detector_cls,
        mock_meta_validator_cls,
        mock_compose_validator_cls,
        mock_transformer_cls,
        mock_profile_mgr_cls,
        mock_builder_cls,
        mock_pusher_cls,
        mock_reg_builder_cls,
        mock_publisher_cls,
        tmp_path,
    ):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(tmp_path)
        mock_detector_cls.return_value = mock_detector

        mock_meta_validator = MagicMock()
        mock_meta_validator.validate.return_value = _make_validation_result()
        mock_meta_validator_cls.return_value = mock_meta_validator

        mock_compose_validator = MagicMock()
        mock_compose_validator.validate.return_value = _make_validation_result()
        mock_compose_validator_cls.return_value = mock_compose_validator

        mock_profile_mgr = MagicMock()
        mock_profile_mgr.resolve_profile.return_value = _make_profile()
        mock_profile_mgr_cls.return_value = mock_profile_mgr

        mock_transformer = MagicMock()
        mock_transformer.transform.return_value = {
            "services": {
                "backend": {"image": "ghcr.io/my-org/my-app-backend:1.0.0"}
            }
        }
        mock_transformer_cls.return_value = mock_transformer

        mock_reg_builder = MagicMock()
        mock_reg_builder.build_entry.return_value = {
            "name": "my-app",
            "version": "1.0.0",
        }
        mock_reg_builder_cls.return_value = mock_reg_builder

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result()
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", no_build=True)

        # ImageBuilder should not have build() called
        mock_builder_cls.return_value.build.assert_not_called()

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_no_push_skips_push(
        self,
        mock_detector_cls,
        mock_meta_validator_cls,
        mock_compose_validator_cls,
        mock_transformer_cls,
        mock_profile_mgr_cls,
        mock_builder_cls,
        mock_pusher_cls,
        mock_reg_builder_cls,
        mock_publisher_cls,
        tmp_path,
    ):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(tmp_path)
        mock_detector_cls.return_value = mock_detector

        mock_meta_validator = MagicMock()
        mock_meta_validator.validate.return_value = _make_validation_result()
        mock_meta_validator_cls.return_value = mock_meta_validator

        mock_compose_validator = MagicMock()
        mock_compose_validator.validate.return_value = _make_validation_result()
        mock_compose_validator_cls.return_value = mock_compose_validator

        mock_profile_mgr = MagicMock()
        mock_profile_mgr.resolve_profile.return_value = _make_profile()
        mock_profile_mgr_cls.return_value = mock_profile_mgr

        mock_transformer = MagicMock()
        mock_transformer.transform.return_value = {
            "services": {
                "backend": {"image": "ghcr.io/my-org/my-app-backend:1.0.0"}
            }
        }
        mock_transformer_cls.return_value = mock_transformer

        mock_image_builder = MagicMock()
        mock_image_builder.build.return_value = [
            "ghcr.io/my-org/my-app-backend:1.0.0"
        ]
        mock_builder_cls.return_value = mock_image_builder

        mock_reg_builder = MagicMock()
        mock_reg_builder.build_entry.return_value = {
            "name": "my-app",
            "version": "1.0.0",
        }
        mock_reg_builder_cls.return_value = mock_reg_builder

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result()
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", no_push=True)

        # Build happens
        mock_image_builder.build.assert_called_once()
        # Push should NOT be called
        mock_pusher_cls.return_value.push.assert_not_called()


# ------------------------------------------------------------------
# Profile not found error
# ------------------------------------------------------------------


class TestPublishProfileNotFound:
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_profile_not_found_exits(
        self,
        mock_detector_cls,
        mock_meta_validator_cls,
        mock_compose_validator_cls,
        mock_transformer_cls,
        mock_profile_mgr_cls,
        tmp_path,
    ):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(tmp_path)
        mock_detector_cls.return_value = mock_detector

        mock_meta_validator = MagicMock()
        mock_meta_validator.validate.return_value = _make_validation_result()
        mock_meta_validator_cls.return_value = mock_meta_validator

        mock_compose_validator = MagicMock()
        mock_compose_validator.validate.return_value = _make_validation_result()
        mock_compose_validator_cls.return_value = mock_compose_validator

        mock_transformer = MagicMock()
        mock_transformer.transform.return_value = {"services": {}}
        mock_transformer_cls.return_value = mock_transformer

        mock_profile_mgr = MagicMock()
        mock_profile_mgr.resolve_profile.side_effect = ValueError(
            "Profile 'nonexistent' not found"
        )
        mock_profile_mgr_cls.return_value = mock_profile_mgr

        from kamiwaza_extensions.commands.publish import run_publish

        with pytest.raises((SystemExit, ClickExit)):
            run_publish(stage="nonexistent")


# ------------------------------------------------------------------
# No compose data
# ------------------------------------------------------------------


class TestPublishNoCompose:
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_no_compose_exits(self, mock_detector_cls, tmp_path):
        from kamiwaza_extensions.extension_detector import ExtensionInfo

        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="1.0.0",
            metadata={"name": "my-app"},
            compose_path=None,
            compose_data=None,
        )
        mock_detector = MagicMock()
        mock_detector.detect.return_value = info
        mock_detector_cls.return_value = mock_detector

        from kamiwaza_extensions.commands.publish import run_publish

        with pytest.raises((SystemExit, ClickExit)):
            run_publish(stage="dev")

# ------------------------------------------------------------------
# Preview image path traversal
# ------------------------------------------------------------------


class TestResolvePreviewImage:
    def test_rejects_path_traversal(self, tmp_path):
        from kamiwaza_extensions.commands.publish import _resolve_preview_image

        result = _resolve_preview_image(
            {"preview_image": "../../etc/passwd"}, tmp_path
        )
        assert result is None

    def test_returns_existing_image(self, tmp_path):
        from kamiwaza_extensions.commands.publish import _resolve_preview_image

        (tmp_path / "images").mkdir()
        (tmp_path / "images" / "preview.png").write_bytes(b"PNG")
        result = _resolve_preview_image(
            {"preview_image": "images/preview.png"}, tmp_path
        )
        assert result is not None
        assert result.name == "preview.png"

    def test_returns_none_for_missing_image(self, tmp_path):
        from kamiwaza_extensions.commands.publish import _resolve_preview_image

        result = _resolve_preview_image(
            {"preview_image": "images/nonexistent.png"}, tmp_path
        )
        assert result is None

    def test_returns_none_when_no_preview(self, tmp_path):
        from kamiwaza_extensions.commands.publish import _resolve_preview_image

        result = _resolve_preview_image({}, tmp_path)
        assert result is None
