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

        # H1: --no-push without --no-build (or --digest) is rejected
        # because we can't pin a digest for an image that's only local.
        # This test exercises the build+push-skipped path via --digest.
        run_publish(
            stage="dev", no_push=True, digest="sha256:" + "a" * 64,
        )

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


# ------------------------------------------------------------------
# ENG-4370 — --digest flag and auto-resolve digest pinning
# ------------------------------------------------------------------


_DIGEST_BACKEND = "sha256:" + "a" * 64
_DIGEST_FRONTEND = "sha256:" + "b" * 64
_DIGEST_USER_SUPPLIED = "sha256:" + "c" * 64


def _multi_buildable_compose(name: str = "my-app", version: str = "1.0.0"):
    return {
        "services": {
            "backend": {
                "build": {"context": "./backend"},
                "image": f"my-org/{name}-backend:{version}",
            },
            "frontend": {
                "build": {"context": "./frontend"},
                "image": f"my-org/{name}-frontend:{version}",
            },
            "db": {  # Pattern B: external pass-through
                "image": "postgres:15",
            },
        },
    }


def _wire_publish_mocks(
    *, detector_cls, meta_validator_cls, compose_validator_cls,
    transformer_cls, profile_mgr_cls, builder_cls, pusher_cls,
    reg_builder_cls, publisher_cls, tmp_path,
    compose_data=None, image_refs=None, transformed_services=None,
):
    """Configure the standard chain of mocks used by the digest tests."""
    info = _make_extension_info(tmp_path, compose_data=compose_data)
    detector = MagicMock()
    detector.detect.return_value = info
    detector_cls.return_value = detector

    meta_validator_cls.return_value.validate.return_value = _make_validation_result()
    compose_validator_cls.return_value.validate.return_value = _make_validation_result()
    profile_mgr_cls.return_value.resolve_profile.return_value = _make_profile()

    transformer = MagicMock()
    transformer.transform.return_value = {
        "services": transformed_services or {
            "backend": {"image": "ghcr.io/my-org/my-app-backend:1.0.0-dev"},
        },
    }
    transformer_cls.return_value = transformer

    image_builder = MagicMock()
    # Use `is None` so callers can pass [] to mean "no buildable images".
    image_builder.build.return_value = (
        image_refs if image_refs is not None
        else ["ghcr.io/my-org/my-app-backend:1.0.0-dev"]
    )
    builder_cls.return_value = image_builder

    pusher_cls.return_value = MagicMock()

    reg_builder = MagicMock()
    reg_builder.build_entry.return_value = {"name": "my-app", "version": "1.0.0"}
    reg_builder_cls.return_value = reg_builder

    publisher = MagicMock()
    publisher.publish.return_value = _make_publish_result()
    publisher_cls.return_value = publisher

    return {
        "reg_builder": reg_builder,
        "image_builder": image_builder,
        "pusher": pusher_cls.return_value,
    }


class TestPublishDigest:
    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_auto_resolve_passes_digest_map_to_build_entry(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        mock_resolve.side_effect = lambda ref: (
            _DIGEST_BACKEND if "backend" in ref else _DIGEST_FRONTEND
        )
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
            image_refs=[
                "ghcr.io/my-org/my-app-backend:1.0.0-dev",
                "ghcr.io/my-org/my-app-frontend:1.0.0-dev",
            ],
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        assert mock_resolve.call_count == 2
        kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        assert kwargs["digest_map"] == {
            "ghcr.io/my-org/my-app-backend:1.0.0-dev": _DIGEST_BACKEND,
            "ghcr.io/my-org/my-app-frontend:1.0.0-dev": _DIGEST_FRONTEND,
        }

    # Note: previous test_explicit_digest_skips_resolver was removed —
    # H2 changed the contract so explicit --digest with push DOES call
    # resolve_digest for verification. See test_explicit_digest_with_
    # push_verifies_against_registry below.

    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    def test_invalid_digest_format_exits_before_detect(
        self, mock_meta_validator_cls, mock_compose_validator_cls,
        mock_detector_cls, tmp_path,
    ):
        from kamiwaza_extensions.commands.publish import run_publish

        with pytest.raises((SystemExit, ClickExit)):
            run_publish(stage="dev", digest="not-a-digest")

        # Detection should NOT happen when format is bad — we fail fast.
        mock_detector_cls.return_value.detect.assert_not_called()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_digest_with_multi_buildable_errors(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        info = _make_extension_info(
            tmp_path, compose_data=_multi_buildable_compose(),
        )
        mock_detector_cls.return_value.detect.return_value = info

        from kamiwaza_extensions.commands.publish import run_publish

        with pytest.raises((SystemExit, ClickExit)):
            run_publish(stage="dev", digest=_DIGEST_USER_SUPPLIED)

        # Should never have built or pushed.
        mock_builder_cls.return_value.build.assert_not_called()
        mock_pusher_cls.return_value.push.assert_not_called()
        mock_resolve.assert_not_called()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_built_no_push_without_digest_errors(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # H1: building locally and skipping push leaves the registry
        # stale relative to the local image; auto-resolve would pin to
        # the wrong digest. The CLI must reject this combination.
        info = _make_extension_info(tmp_path)
        mock_detector_cls.return_value.detect.return_value = info

        from kamiwaza_extensions.commands.publish import run_publish

        with pytest.raises((SystemExit, ClickExit)):
            run_publish(stage="dev", no_push=True)

        # Should never have built or attempted to resolve.
        mock_builder_cls.return_value.build.assert_not_called()
        mock_resolve.assert_not_called()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_external_only_extension_no_push_allowed(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # Re-review HIGH 1: extensions whose compose has zero buildable
        # services (only external/prebuilt refs) must not trip H1 — there
        # is no local-only image to pin.
        compose = {
            "services": {
                "db": {"image": "postgres:15"},   # no `build:` key
                "cache": {"image": "redis:7"},
            },
        }
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
            compose_data=compose,
            image_refs=[],  # nothing built
            transformed_services={
                "db": {"image": "postgres:15"},
                "cache": {"image": "redis:7"},
            },
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", no_push=True)

        # H1 must NOT fire — build runs (and returns nothing), push is
        # skipped, no digest resolution attempted, catalog publishes.
        wired["pusher"].push.assert_not_called()
        mock_resolve.assert_not_called()
        wired["reg_builder"].build_entry.assert_called_once()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_digest_with_profiled_helper_allowed(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # Re-review HIGH 2: a buildable service with a `profiles:` key
        # is stripped by ComposeTransformer before publish, so it should
        # not count toward the buildable-count guard.
        compose = {
            "services": {
                "backend": {
                    "build": {"context": "."},
                    "image": "my-org/my-app-backend:1.0.0",
                },
                "dev-helper": {
                    "build": {"context": "./dev"},
                    "image": "my-org/my-app-dev-helper:1.0.0",
                    "profiles": ["dev"],   # local-only, stripped on publish
                },
            },
        }
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
            compose_data=compose,
        )
        mock_resolve.return_value = _DIGEST_USER_SUPPLIED

        from kamiwaza_extensions.commands.publish import run_publish

        # Should NOT error on "found 2"; profiled service is excluded.
        run_publish(stage="dev", digest=_DIGEST_USER_SUPPLIED)

        kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        assert kwargs["digest_map"] == {
            "ghcr.io/my-org/my-app-backend:1.0.0-dev": _DIGEST_USER_SUPPLIED,
        }

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_no_build_no_push_resolve_failure_falls_back_to_tag_only(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # Re-review HIGH 3 (option A): catalog-only republish softly
        # falls back to tag-only when resolve_digest fails (e.g. no
        # docker on host). Catalog publishes; the failed ref is absent
        # from digest_map.
        from kamiwaza_extensions.image_pusher import ImagePushError

        mock_resolve.side_effect = ImagePushError("docker not found")
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
        )

        from kamiwaza_extensions.commands.publish import run_publish

        # Must NOT raise — catalog still publishes with tag-only.
        run_publish(stage="dev", no_build=True, no_push=True)

        wired["reg_builder"].build_entry.assert_called_once()
        kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        # digest_map either None or empty — no entries for the failed ref.
        assert not kwargs.get("digest_map")

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_dry_run_no_push_without_digest_allowed(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # F2: H1's stale-registry hazard does not apply under --dry-run
        # (no build, no push, no auto-resolve happens). Preview should
        # complete without forcing the user to add --no-build or --digest.
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
        )
        mock_publisher_cls.return_value.publish.return_value = (
            _make_publish_result(dry_run=True)
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", dry_run=True, no_push=True)

        # Dry-run completed: build_entry called, no resolver invocation.
        wired["reg_builder"].build_entry.assert_called_once()
        mock_resolve.assert_not_called()
        wired["image_builder"].build.assert_not_called()
        wired["pusher"].push.assert_not_called()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_built_no_push_with_digest_skips_resolve(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # H1 escape hatch: explicit --digest lets the user opt out of
        # auto-resolve, so build+no-push is allowed when --digest is set.
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", no_push=True, digest=_DIGEST_USER_SUPPLIED)

        # No push, no resolve (no_push=True), digest taken verbatim.
        wired["pusher"].push.assert_not_called()
        mock_resolve.assert_not_called()
        kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        assert kwargs["digest_map"] == {
            "ghcr.io/my-org/my-app-backend:1.0.0-dev": _DIGEST_USER_SUPPLIED,
        }

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_explicit_digest_with_push_verifies_against_registry(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # H2: when --digest is set and push happened, resolve_digest is
        # called for integrity verification (not as the source of truth).
        mock_resolve.return_value = _DIGEST_USER_SUPPLIED
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", digest=_DIGEST_USER_SUPPLIED)

        mock_resolve.assert_called_once()
        kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        assert kwargs["digest_map"] == {
            "ghcr.io/my-org/my-app-backend:1.0.0-dev": _DIGEST_USER_SUPPLIED,
        }

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_explicit_digest_mismatch_aborts(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # H2: registry returns a different digest from what user supplied
        # → publish must abort before catalog is written.
        mock_resolve.return_value = _DIGEST_BACKEND  # registry has this
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
        )

        from kamiwaza_extensions.commands.publish import run_publish

        with pytest.raises((SystemExit, ClickExit)):
            run_publish(stage="dev", digest=_DIGEST_USER_SUPPLIED)  # not _DIGEST_BACKEND

        # Catalog publish never happened.
        wired["reg_builder"].build_entry.assert_not_called()
        mock_publisher_cls.return_value.publish.assert_not_called()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_resolve_digest_failure_aborts_publish(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # Orchestration: if resolve_digest raises, run_publish exits 1
        # and never writes the catalog.
        from kamiwaza_extensions.image_pusher import ImagePushError

        mock_resolve.side_effect = ImagePushError("manifest unknown")
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
        )

        from kamiwaza_extensions.commands.publish import run_publish

        with pytest.raises((SystemExit, ClickExit)):
            run_publish(stage="dev")

        wired["reg_builder"].build_entry.assert_not_called()
        mock_publisher_cls.return_value.publish.assert_not_called()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_no_build_no_push_resolves_digest_from_registry(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        mock_resolve.return_value = _DIGEST_BACKEND
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", no_build=True, no_push=True)

        wired["image_builder"].build.assert_not_called()
        wired["pusher"].push.assert_not_called()
        # Image refs come from compose data; digest still resolved.
        mock_resolve.assert_called_once()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_revision_and_digest_orthogonal(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        mock_resolve.return_value = _DIGEST_BACKEND
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
            image_refs=["ghcr.io/my-org/my-app-backend:abc1234"],
            transformed_services={
                "backend": {"image": "ghcr.io/my-org/my-app-backend:abc1234"},
            },
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", revision="abc1234")

        kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        assert kwargs["revision"] == "abc1234"
        assert kwargs["digest_map"] == {
            "ghcr.io/my-org/my-app-backend:abc1234": _DIGEST_BACKEND,
        }

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest")
    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_pattern_b_external_image_not_in_digest_map(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # Compose: one buildable backend + postgres pass-through.
        compose = {
            "services": {
                "backend": {
                    "build": {"context": "."},
                    "image": "my-org/my-app-backend:1.0.0",
                },
                "db": {"image": "postgres:15"},
            },
        }
        mock_resolve.return_value = _DIGEST_BACKEND
        wired = _wire_publish_mocks(
            detector_cls=mock_detector_cls,
            meta_validator_cls=mock_meta_validator_cls,
            compose_validator_cls=mock_compose_validator_cls,
            transformer_cls=mock_transformer_cls,
            profile_mgr_cls=mock_profile_mgr_cls,
            builder_cls=mock_builder_cls,
            pusher_cls=mock_pusher_cls,
            reg_builder_cls=mock_reg_builder_cls,
            publisher_cls=mock_publisher_cls,
            tmp_path=tmp_path,
            compose_data=compose,
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        # Map only carries the buildable ref; postgres absent.
        assert "postgres:15" not in kwargs["digest_map"]
        assert all(
            "postgres" not in ref for ref in kwargs["digest_map"]
        )

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_dry_run_skips_digest_resolution(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, tmp_path,
    ):
        with patch(
            "kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest"
        ) as mock_resolve:
            wired = _wire_publish_mocks(
                detector_cls=mock_detector_cls,
                meta_validator_cls=mock_meta_validator_cls,
                compose_validator_cls=mock_compose_validator_cls,
                transformer_cls=mock_transformer_cls,
                profile_mgr_cls=mock_profile_mgr_cls,
                builder_cls=mock_builder_cls,
                pusher_cls=mock_pusher_cls,
                reg_builder_cls=mock_reg_builder_cls,
                publisher_cls=mock_publisher_cls,
                tmp_path=tmp_path,
            )
            wired["reg_builder"].build_entry.return_value = {
                "name": "my-app", "version": "1.0.0",
            }
            mock_publisher_cls.return_value.publish.return_value = (
                _make_publish_result(dry_run=True)
            )

            from kamiwaza_extensions.commands.publish import run_publish

            run_publish(stage="dev", dry_run=True)

            mock_resolve.assert_not_called()

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_dry_run_with_explicit_digest_passes_through(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, tmp_path,
    ):
        # Compose with one buildable backend (so --digest is valid).
        compose = {
            "services": {
                "backend": {
                    "build": {"context": "."},
                    "image": "my-org/my-app-backend:1.0.0",
                },
            },
        }
        with patch(
            "kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest"
        ) as mock_resolve:
            wired = _wire_publish_mocks(
                detector_cls=mock_detector_cls,
                meta_validator_cls=mock_meta_validator_cls,
                compose_validator_cls=mock_compose_validator_cls,
                transformer_cls=mock_transformer_cls,
                profile_mgr_cls=mock_profile_mgr_cls,
                builder_cls=mock_builder_cls,
                pusher_cls=mock_pusher_cls,
                reg_builder_cls=mock_reg_builder_cls,
                publisher_cls=mock_publisher_cls,
                tmp_path=tmp_path,
                compose_data=compose,
            )
            mock_publisher_cls.return_value.publish.return_value = (
                _make_publish_result(dry_run=True)
            )

            from kamiwaza_extensions.commands.publish import run_publish

            run_publish(stage="dev", dry_run=True, digest=_DIGEST_USER_SUPPLIED)

            mock_resolve.assert_not_called()
            kwargs = wired["reg_builder"].build_entry.call_args.kwargs
            # Dry-run preview pins the supplied digest on the buildable ref.
            assert kwargs["digest_map"] == {
                "ghcr.io/my-org/my-app-backend:1.0.0-dev": _DIGEST_USER_SUPPLIED,
            }
