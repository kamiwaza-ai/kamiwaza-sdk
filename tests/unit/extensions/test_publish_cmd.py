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
                    "image": f"ghcr.io/my-org/{name}-backend:{version}",
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


def _make_validation_result(passed=True, errors=None, warnings=None, info=None):
    """Create a mock ValidationResult."""
    from kamiwaza_extensions.validators.result import ValidationResult

    return ValidationResult(
        passed=passed,
        errors=errors or [],
        warnings=warnings or [],
        info=info or [],
    )


def _make_publish_result(**overrides):
    """Create a mock PublishResult."""
    from kamiwaza_extensions.catalog_publisher import PublishResult

    defaults = dict(
        extension_name="my-app",
        version="1.0.0",
        action="insert",
        registry_url="ghcr.io/my-org",
        catalog_file="garden/v3/apps.json",
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


class TestPublishWithInfoFindings:
    """ENG-4956 regression: info findings must not break the publish flow.

    The info-printing loop previously rebound the `info` variable (the
    ExtensionInfo object) to a message string, so `info.path` later raised
    `AttributeError: 'str' object has no attribute 'path'` whenever
    validation emitted any info entry.
    """

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_publish_succeeds_with_info_findings(
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

        # Compose validation emits scaffold-default info entries.
        mock_compose_validator = MagicMock()
        mock_compose_validator.validate.return_value = _make_validation_result(
            info=[
                "Service 'backend' uses bind mount './data:/app' — stripped at deploy.",
                "Service 'backend' has no resource limits — defaults applied at deploy.",
            ]
        )
        mock_compose_validator_cls.return_value = mock_compose_validator

        mock_profile_mgr = MagicMock()
        mock_profile_mgr.resolve_profile.return_value = _make_profile()
        mock_profile_mgr_cls.return_value = mock_profile_mgr

        mock_transformer = MagicMock()
        mock_transformer.transform.return_value = {
            "services": {"backend": {"image": "ghcr.io/my-org/my-app-backend:1.0.0"}}
        }
        mock_transformer_cls.return_value = mock_transformer

        mock_image_builder = MagicMock()
        mock_image_builder.build.return_value = ["ghcr.io/my-org/my-app-backend:1.0.0"]
        mock_builder_cls.return_value = mock_image_builder

        mock_pusher_cls.return_value = MagicMock()

        mock_reg_builder = MagicMock()
        mock_reg_builder.build_entry.return_value = {"name": "my-app", "version": "1.0.0"}
        mock_reg_builder_cls.return_value = mock_reg_builder

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result()
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        # Must not raise AttributeError — the ExtensionInfo binding survives
        # the info-printing loop.
        run_publish(stage="dev")

        # resolve_profile is the call site that crashed: it needs the real
        # ExtensionInfo's `path`, proving `info` was not clobbered.
        mock_profile_mgr.resolve_profile.assert_called_once_with(
            "dev", extension_dir=tmp_path
        )
        mock_publisher.publish.assert_called_once()


class TestPublishAppgardenValidationChannel:
    """ENG-4956: an authored appgarden compose bypasses ComposeTransformer, so
    `run_publish` must validate it with `transformer_handled=False` — otherwise
    bind mounts / missing limits are reported as benign info even though deploy
    will not strip or backfill them.
    """

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_appgarden_publish_validates_with_transformer_handled_false(
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
        # An authored appgarden compose makes run_publish take the
        # _retag_appgarden_compose path instead of the generic transform.
        (tmp_path / "docker-compose.appgarden.yml").write_text(
            "services:\n"
            "  backend:\n"
            "    image: ghcr.io/my-org/my-app-backend:1.0.0\n"
        )

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

        mock_transformer_cls.return_value = MagicMock()

        mock_image_builder = MagicMock()
        mock_image_builder.build.return_value = ["ghcr.io/my-org/my-app-backend:1.0.0"]
        mock_builder_cls.return_value = mock_image_builder

        mock_pusher_cls.return_value = MagicMock()

        mock_reg_builder = MagicMock()
        mock_reg_builder.build_entry.return_value = {"name": "my-app", "version": "1.0.0"}
        mock_reg_builder_cls.return_value = mock_reg_builder

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result()
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        # The compose validator must be told the transformer is bypassed.
        assert mock_compose_validator.validate.call_args.kwargs[
            "transformer_handled"
        ] is False


# ------------------------------------------------------------------
# extra_docker_images: digest resolution
# ------------------------------------------------------------------


class TestPublishExtrasDigestResolution:
    """`kz-ext publish` resolves digests for extra_docker_images."""

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_extras_under_registry_get_digest_resolved_and_passed_to_build_entry(
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
        # An extra_docker_images entry with `{version}` under the
        # configured registry must be substituted, stage-suffixed, and
        # digest-pinned via the same registry-resolution path compose
        # buildable services use.
        metadata = {
            "name": "kaizenv3",
            "version": "1.8.13",
            "description": "Kaizen v3",
            "extra_docker_images": [
                "ghcr.io/my-org/images/agent:{version}",
            ],
        }
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(
            tmp_path, name="kaizenv3", version="1.8.13", metadata=metadata,
        )
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
                "backend": {"image": "ghcr.io/my-org/kaizenv3-backend:1.8.13-dev"},
            },
        }
        mock_transformer_cls.return_value = mock_transformer

        mock_image_builder = MagicMock()
        mock_image_builder.build.return_value = [
            "ghcr.io/my-org/kaizenv3-backend:1.8.13-dev",
        ]
        mock_builder_cls.return_value = mock_image_builder

        # Distinct digests per ref so the assertion can pin which ref
        # got which digest.
        digest_by_ref = {
            "ghcr.io/my-org/kaizenv3-backend:1.8.13-dev": "sha256:" + "a" * 64,
            "ghcr.io/my-org/images/agent:1.8.13-dev": "sha256:" + "b" * 64,
        }
        mock_pusher_cls.resolve_digest.side_effect = lambda ref: digest_by_ref[ref]

        mock_reg_builder = MagicMock()
        mock_reg_builder.build_entry.return_value = {
            "name": "kaizenv3",
            "version": "1.8.13",
        }
        mock_reg_builder_cls.return_value = mock_reg_builder

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result(
            extension_name="kaizenv3", version="1.8.13",
        )
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        resolved_refs = [
            call.args[0] for call in mock_pusher_cls.resolve_digest.call_args_list
        ]
        assert "ghcr.io/my-org/images/agent:1.8.13-dev" in resolved_refs
        assert "ghcr.io/my-org/kaizenv3-backend:1.8.13-dev" in resolved_refs

        # Both digests reach digest_map.
        build_entry_kwargs = mock_reg_builder.build_entry.call_args.kwargs
        digest_map = build_entry_kwargs["digest_map"]
        assert digest_map["ghcr.io/my-org/images/agent:1.8.13-dev"] == (
            "sha256:" + "b" * 64
        )
        assert digest_map["ghcr.io/my-org/kaizenv3-backend:1.8.13-dev"] == (
            "sha256:" + "a" * 64
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
    def test_extras_already_in_digest_map_skipped_from_auto_resolve(
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
        # When `--digest` pins a buildable ref and that same ref is
        # redundantly listed in extras, the explicit pin must survive:
        # no registry lookup, no overwrite.
        metadata = {
            "name": "my-app",
            "version": "1.0.0",
            "description": "Test",
            "extra_docker_images": ["ghcr.io/my-org/my-app-backend:{version}"],
        }
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(
            tmp_path, metadata=metadata,
        )
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
                "backend": {"image": "ghcr.io/my-org/my-app-backend:1.0.0-dev"},
            },
        }
        mock_transformer_cls.return_value = mock_transformer

        mock_image_builder = MagicMock()
        mock_image_builder.build.return_value = [
            "ghcr.io/my-org/my-app-backend:1.0.0-dev",
        ]
        mock_builder_cls.return_value = mock_image_builder

        # If anything calls resolve_digest, fail the test loudly — the
        # user-supplied digest must be the only source of truth here.
        mock_pusher_cls.resolve_digest.side_effect = AssertionError(
            "resolve_digest should not be called when --digest pins the ref"
        )

        mock_reg_builder = MagicMock()
        mock_reg_builder.build_entry.return_value = {"name": "my-app", "version": "1.0.0"}
        mock_reg_builder_cls.return_value = mock_reg_builder

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result()
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        supplied_digest = "sha256:" + "e" * 64
        # --digest implies --no-push so the supplied digest is trusted
        # without a post-push verification round-trip.
        run_publish(stage="dev", digest=supplied_digest, no_push=True)

        # The supplied digest survived end-to-end into build_entry's map.
        build_entry_kwargs = mock_reg_builder.build_entry.call_args.kwargs
        digest_map = build_entry_kwargs["digest_map"]
        assert digest_map["ghcr.io/my-org/my-app-backend:1.0.0-dev"] == supplied_digest

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_external_extras_skipped_from_digest_resolution(
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
        # External refs and already-pinned refs skip the digest lookup:
        # external images aren't ours to pin, and re-tagging a pinned ref
        # would break its immutable identity.
        metadata = {
            "name": "my-app",
            "version": "1.0.0",
            "description": "Test",
            "extra_docker_images": [
                "postgres:15",                              # external
                "ghcr.io/external/sidecar:2.0",             # external
                "ghcr.io/my-org/images/util@sha256:" + "c" * 64,  # already pinned
            ],
        }
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(
            tmp_path, metadata=metadata,
        )
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
                "backend": {"image": "ghcr.io/my-org/my-app-backend:1.0.0-dev"},
            },
        }
        mock_transformer_cls.return_value = mock_transformer

        mock_image_builder = MagicMock()
        mock_image_builder.build.return_value = [
            "ghcr.io/my-org/my-app-backend:1.0.0-dev",
        ]
        mock_builder_cls.return_value = mock_image_builder

        compose_digest = "sha256:" + "d" * 64
        mock_pusher_cls.resolve_digest.return_value = compose_digest

        mock_reg_builder = MagicMock()
        mock_reg_builder.build_entry.return_value = {"name": "my-app", "version": "1.0.0"}
        mock_reg_builder_cls.return_value = mock_reg_builder

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result()
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        # Only the compose backend hits the registry; the three extras
        # are filtered out before resolution.
        resolved_refs = [
            call.args[0] for call in mock_pusher_cls.resolve_digest.call_args_list
        ]
        assert resolved_refs == ["ghcr.io/my-org/my-app-backend:1.0.0-dev"]


# ------------------------------------------------------------------
# Env-var image refs: compose default matches extras (ENG-5260)
# ------------------------------------------------------------------


class TestPublishEnvImageRefsAgreeWithExtras:
    """For a kaizen-shaped extension (env-var image ref + extras entry for
    the same dynamic-spawn image), the published compose's env default
    must agree with the extras list — same stage suffix, same digest."""

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_env_default_matches_extras_entry_end_to_end(
        self,
        mock_detector_cls,
        mock_meta_validator_cls,
        mock_compose_validator_cls,
        mock_profile_mgr_cls,
        mock_builder_cls,
        mock_pusher_cls,
        mock_publisher_cls,
        tmp_path,
    ):
        # Real ComposeTransformer + RegistryBuilder; only the I/O
        # boundary is mocked. The published apps.json entry's compose_yml
        # env default and extra_docker_images entry must agree on tag +
        # digest for the same dynamic-spawn image.
        import yaml as _yaml

        metadata = {
            "name": "kaizenv3",
            "version": "1.8.13",
            "description": "Kaizen v3",
            "extra_docker_images": ["ghcr.io/my-org/images/agent:{version}"],
        }
        compose_data = {
            "services": {
                "backend": {
                    "build": {"context": "."},
                    "image": "ghcr.io/my-org/kaizenv3-backend:1.8.13",
                    "ports": ["8000"],
                    "environment": {
                        "AGENT_SERVER_IMAGE":
                            "${AGENT_SERVER_IMAGE:-ghcr.io/my-org/images/agent:1.8.13}",
                    },
                },
            },
        }
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(
            tmp_path,
            name="kaizenv3",
            version="1.8.13",
            metadata=metadata,
            compose_data=compose_data,
        )
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

        mock_image_builder = MagicMock()
        mock_image_builder.build.return_value = [
            "ghcr.io/my-org/kaizenv3-backend:1.8.13-dev",
        ]
        mock_builder_cls.return_value = mock_image_builder

        agent_digest = "sha256:" + "b" * 64
        digest_by_ref = {
            "ghcr.io/my-org/kaizenv3-backend:1.8.13-dev": "sha256:" + "a" * 64,
            "ghcr.io/my-org/images/agent:1.8.13-dev": agent_digest,
        }
        mock_pusher_cls.resolve_digest.side_effect = lambda ref: digest_by_ref[ref]

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result(
            extension_name="kaizenv3", version="1.8.13",
        )
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        # Grab the entry the publisher would have written to the catalog.
        publish_kwargs = mock_publisher.publish.call_args.kwargs
        entry = publish_kwargs["entry"]
        compose_out = _yaml.safe_load(entry["compose_yml"])
        env_default = compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ]

        # Acceptance: the canonical pinned ref appears in BOTH surfaces.
        expected_ref = f"ghcr.io/my-org/images/agent:1.8.13-dev@{agent_digest}"
        assert entry["extra_docker_images"] == [expected_ref]
        assert expected_ref in env_default, (
            f"compose env default {env_default!r} must reference the same "
            f"pinned ref as extras ({expected_ref!r})"
        )
        # The ${VAR:-...} shape is preserved so runtime overrides still win.
        assert env_default.startswith("${AGENT_SERVER_IMAGE:-")
        assert env_default.endswith("}")

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_env_default_matches_extras_entry_under_revision(
        self,
        mock_detector_cls,
        mock_meta_validator_cls,
        mock_compose_validator_cls,
        mock_profile_mgr_cls,
        mock_builder_cls,
        mock_pusher_cls,
        mock_publisher_cls,
        tmp_path,
    ):
        # Kaizen-shaped end-to-end under --revision: the compose env
        # default is written at the literal version tag (the common
        # author shape); the extras list uses {version}. After
        # publish, both surfaces in the catalog entry must reference
        # the same revision-tagged + digest-pinned ref. Locks the
        # env-default-matches-extras invariant under the revision
        # path; sister test `test_env_default_matches_extras_entry_
        # end_to_end` above exercises the same invariant without
        # --revision.
        import yaml as _yaml

        metadata = {
            "name": "kaizenv3",
            "version": "1.9.0",
            "description": "Kaizen v3",
            "extra_docker_images": ["ghcr.io/my-org/images/agent:{version}"],
        }
        compose_data = {
            "services": {
                "backend": {
                    "build": {"context": "."},
                    "image": "ghcr.io/my-org/kaizenv3-backend:1.9.0",
                    "ports": ["8000"],
                    "environment": {
                        "AGENT_SERVER_IMAGE":
                            "${AGENT_SERVER_IMAGE:-ghcr.io/my-org/images/agent:1.9.0}",
                    },
                },
            },
        }
        mock_detector = MagicMock()
        mock_detector.detect.return_value = _make_extension_info(
            tmp_path,
            name="kaizenv3",
            version="1.9.0",
            metadata=metadata,
            compose_data=compose_data,
        )
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

        mock_image_builder = MagicMock()
        mock_image_builder.build.return_value = [
            "ghcr.io/my-org/kaizenv3-backend:develop",
        ]
        mock_builder_cls.return_value = mock_image_builder

        # Under --revision develop, _auto_resolve_digests queries the
        # registry for the revision-tagged refs CI actually pushed.
        agent_digest = "sha256:" + "d" * 64
        digest_by_ref = {
            "ghcr.io/my-org/kaizenv3-backend:develop": "sha256:" + "e" * 64,
            "ghcr.io/my-org/images/agent:develop": agent_digest,
        }
        mock_pusher_cls.resolve_digest.side_effect = lambda ref: digest_by_ref[ref]

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = _make_publish_result(
            extension_name="kaizenv3", version="1.9.0",
        )
        mock_publisher_cls.return_value = mock_publisher

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", revision="develop")

        publish_kwargs = mock_publisher.publish.call_args.kwargs
        entry = publish_kwargs["entry"]
        compose_out = _yaml.safe_load(entry["compose_yml"])
        env_default = compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ]

        # Under --revision, the canonical pinned ref must appear in
        # BOTH the extras list and the env default — the env-default-
        # matches-extras invariant.
        expected_ref = f"ghcr.io/my-org/images/agent:develop@{agent_digest}"
        assert entry["extra_docker_images"] == [expected_ref]
        assert expected_ref in env_default, (
            f"under --revision develop, compose env default {env_default!r} "
            f"must reference the same pinned ref as extras ({expected_ref!r})"
        )
        assert env_default.startswith("${AGENT_SERVER_IMAGE:-")
        assert env_default.endswith("}")


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
# Catalog schema flag plumbing
# ------------------------------------------------------------------


class TestCatalogSchemaFlag:
    """``run_publish`` threads ``catalog_schema`` into ``CatalogPublisher``."""

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_default_passes_catalog_schema_3(
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
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path)
        mock_meta_validator_cls.return_value.validate.return_value = _make_validation_result()
        mock_compose_validator_cls.return_value.validate.return_value = _make_validation_result()
        mock_profile_mgr_cls.return_value.resolve_profile.return_value = _make_profile()
        mock_transformer_cls.return_value.transform.return_value = {"services": {}}
        mock_reg_builder_cls.return_value.build_entry.return_value = {
            "name": "my-app", "version": "1.0.0",
        }
        mock_publisher_cls.return_value.publish.return_value = _make_publish_result(dry_run=True)

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", dry_run=True)

        assert mock_publisher_cls.call_args.kwargs["catalog_schema"] == 3

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_explicit_v2_threaded_to_publisher(
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
        mock_detector_cls.return_value.detect.return_value = _make_extension_info(tmp_path)
        mock_meta_validator_cls.return_value.validate.return_value = _make_validation_result()
        mock_compose_validator_cls.return_value.validate.return_value = _make_validation_result()
        mock_profile_mgr_cls.return_value.resolve_profile.return_value = _make_profile()
        mock_transformer_cls.return_value.transform.return_value = {"services": {}}
        mock_reg_builder_cls.return_value.build_entry.return_value = {
            "name": "my-app", "version": "1.0.0",
        }
        mock_publisher_cls.return_value.publish.return_value = _make_publish_result(dry_run=True)

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", dry_run=True, catalog_schema=2)

        assert mock_publisher_cls.call_args.kwargs["catalog_schema"] == 2


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

        # --no-push without --no-build (or --digest) is rejected because
        # we can't pin a digest for an image that's only local. This test
        # exercises the build+push-skipped path via --digest.
        run_publish(
            stage="dev", no_push=True, digest="sha256:" + "a" * 64,
        )

        # Build happens
        mock_image_builder.build.assert_called_once()
        # Push should NOT be called
        mock_pusher_cls.return_value.push.assert_not_called()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.check_buildx_available")
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
    def test_no_build_push_excludes_profile_gated_services(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls,
        mock_resolve, mock_preflight, tmp_path,
    ):
        # Under --no-build, the push list is sourced from canonical_refs.
        # canonical_refs must filter profile-gated services so a
        # local-only dev helper isn't pushed to the registry alongside
        # the catalog's services. The catalog itself already excludes
        # profiled services via buildable_services — push must mirror
        # that filter or stale dev images leak to the registry.
        compose = {
            "services": {
                "dev-helper": {
                    "build": {"context": "./dev"},
                    "image": "ghcr.io/my-org/my-app-dev-helper:1.0.0",
                    "profiles": ["dev"],
                },
                "backend": {
                    "build": {"context": "."},
                    "image": "ghcr.io/my-org/my-app-backend:1.0.0",
                },
            },
        }
        _wire_publish_mocks(
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
        mock_resolve.return_value = "sha256:" + "a" * 64

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", no_build=True)

        # ImagePusher.push received only the non-profiled backend ref.
        push_call = mock_pusher_cls.return_value.push.call_args
        pushed_refs = push_call.args[0] if push_call.args else push_call.kwargs.get("image_refs", [])
        assert pushed_refs == ["ghcr.io/my-org/my-app-backend:1.0.0-dev"]
        assert "dev-helper" not in str(pushed_refs)


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
                "image": f"ghcr.io/my-org/{name}-backend:{version}",
            },
            "frontend": {
                "build": {"context": "./frontend"},
                "image": f"ghcr.io/my-org/{name}-frontend:{version}",
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
    @patch("kamiwaza_extensions.image_pusher.ImagePusher.check_buildx_available")
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
    def test_buildx_preflight_aborts_before_push_when_unavailable(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls,
        mock_resolve, mock_preflight, tmp_path,
    ):
        # Re-review HIGH 1: when buildx is missing, fail BEFORE push so
        # the registry isn't mutated.
        from kamiwaza_extensions.image_pusher import ImagePushError

        mock_preflight.side_effect = ImagePushError(
            "docker buildx imagetools is not available"
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
        )

        from kamiwaza_extensions.commands.publish import run_publish

        with pytest.raises((SystemExit, ClickExit)):
            run_publish(stage="dev")

        # Preflight ran; push and resolve never did.
        mock_preflight.assert_called_once()
        wired["pusher"].push.assert_not_called()
        mock_resolve.assert_not_called()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.check_buildx_available")
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
    def test_buildx_preflight_skipped_when_no_push(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls,
        mock_resolve, mock_preflight, tmp_path,
    ):
        # The preflight only matters when push will happen. The
        # catalog-only-republish path (--no-build --no-push) doesn't
        # push, so the buildx preflight is moot there.
        mock_resolve.return_value = _DIGEST_BACKEND
        _wire_publish_mocks(
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

        mock_preflight.assert_not_called()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.check_buildx_available")
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
    def test_buildx_preflight_skipped_when_no_buildable_services(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls,
        mock_resolve, mock_preflight, tmp_path,
    ):
        # External-only extension (postgres etc.) — no resolve_digest
        # call will happen, so no buildx needed.
        compose = {
            "services": {
                "db": {"image": "postgres:15"},
                "cache": {"image": "redis:7"},
            },
        }
        _wire_publish_mocks(
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
            image_refs=[],
            transformed_services={
                "db": {"image": "postgres:15"},
                "cache": {"image": "redis:7"},
            },
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        mock_preflight.assert_not_called()

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.check_buildx_available")
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
        mock_reg_builder_cls, mock_publisher_cls,
        mock_resolve, mock_preflight, tmp_path,
    ):
        mock_resolve.side_effect = lambda ref: (
            _DIGEST_BACKEND if "backend" in ref else _DIGEST_FRONTEND
        )
        # Compose with two buildable services drives `published_refs`
        # (which is now the source of truth for the auto-resolve loop,
        # not `image_refs`).
        compose = {
            "services": {
                "backend": {
                    "build": {"context": "."},
                    "image": "ghcr.io/my-org/my-app-backend:1.0.0",
                },
                "frontend": {
                    "build": {"context": "./frontend"},
                    "image": "ghcr.io/my-org/my-app-frontend:1.0.0",
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

    @patch("kamiwaza_extensions.image_pusher.ImagePusher.check_buildx_available")
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
    def test_non_conventional_namespace_flows_through_build_and_resolve(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls,
        mock_resolve, mock_preflight, tmp_path,
    ):
        # ENG-4909: when the compose's declared image namespace diverges
        # from the legacy `{ext}-{svc}` convention (omniparse-style:
        # ext=`tool-omniparse`, svc=`omniparse-server`, declared image
        # `images/omniparse`), the declared namespace must propagate
        # through the build, the push, the digest_map keys, and the
        # catalog entry. Without ENG-4909 the four sites synthesized
        # divergent forms and the catalog shipped pointing at an image
        # that may not exist at the synthesized path.
        custom_ref = (
            "ghcr.io/kamiwaza-internal/foo/images/omniparse:1.0.0-dev"
        )
        custom_digest = "sha256:" + "d" * 64
        mock_resolve.return_value = custom_digest

        compose = {
            "services": {
                "omniparse-server": {
                    "build": {"context": "."},
                    "image": "ghcr.io/kamiwaza-internal/foo/images/omniparse:1.0.0",
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
            image_refs=[custom_ref],
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        # 1. ImageBuilder was passed the declared-namespace ref via
        #    the image_refs kwarg — not the synthesized
        #    {ext}-{svc} form (which for this fixture would have
        #    been .../my-app-omniparse-server:...).
        build_kwargs = wired["image_builder"].build.call_args.kwargs
        assert build_kwargs["image_refs"] == {"omniparse-server": custom_ref}

        # 2. resolve_digest queried the declared-namespace ref.
        mock_resolve.assert_called_once_with(custom_ref)

        # 3. digest_map keys match the declared-namespace ref, so
        #    `_apply_digests` in registry_builder.py will pin the
        #    catalog compose's image successfully (exact-string match).
        kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        assert kwargs["digest_map"] == {custom_ref: custom_digest}

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
        # Building locally and skipping push leaves the registry
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
        # Extensions whose compose has zero buildable
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
        # A buildable service with a `profiles:` key
        # is stripped by ComposeTransformer before publish, so it should
        # not count toward the buildable-count guard.
        compose = {
            "services": {
                "backend": {
                    "build": {"context": "."},
                    "image": "ghcr.io/my-org/my-app-backend:1.0.0",
                },
                "dev-helper": {
                    "build": {"context": "./dev"},
                    "image": "ghcr.io/my-org/my-app-dev-helper:1.0.0",
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
    def test_digest_pins_canonical_ref_when_profiled_helper_appears_first(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # Profiled service first in dict order must NOT redirect --digest
        # pinning. The pinned ref must come from the filtered
        # buildable_services list, not image_refs[0].
        compose = {
            "services": {
                "dev-helper": {
                    "build": {"context": "./dev"},
                    "image": "ghcr.io/my-org/my-app-dev-helper:1.0.0",
                    "profiles": ["dev"],   # profiled — must NOT be pinned
                },
                "backend": {
                    "build": {"context": "."},
                    "image": "ghcr.io/my-org/my-app-backend:1.0.0",
                },
            },
        }
        # ImageBuilder builds both (it doesn't filter profiles). The
        # profiled helper appears FIRST in image_refs.
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
            image_refs=[
                "ghcr.io/my-org/my-app-dev-helper:1.0.0-dev",  # FIRST
                "ghcr.io/my-org/my-app-backend:1.0.0-dev",
            ],
        )
        mock_resolve.return_value = _DIGEST_USER_SUPPLIED  # H2 verify match

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev", digest=_DIGEST_USER_SUPPLIED)

        kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        # Digest pinned against the canonical buildable ref (backend),
        # NOT against the profiled helper's ref.
        assert kwargs["digest_map"] == {
            "ghcr.io/my-org/my-app-backend:1.0.0-dev": _DIGEST_USER_SUPPLIED,
        }
        assert "dev-helper" not in str(kwargs["digest_map"])
        # H2 verify also runs against the canonical ref.
        mock_resolve.assert_called_once_with(
            "ghcr.io/my-org/my-app-backend:1.0.0-dev"
        )

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
    def test_auto_resolve_skips_profiled_helper(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # Auto-resolve must not call resolve_digest for profiled refs —
        # they aren't published in the catalog so any pin would be
        # silently dropped, and a profiled-only-in-registry ref might
        # cause a needless network round-trip / failure.
        compose = {
            "services": {
                "dev-helper": {
                    "build": {"context": "./dev"},
                    "image": "ghcr.io/my-org/my-app-dev-helper:1.0.0",
                    "profiles": ["dev"],
                },
                "backend": {
                    "build": {"context": "."},
                    "image": "ghcr.io/my-org/my-app-backend:1.0.0",
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
            image_refs=[
                "ghcr.io/my-org/my-app-dev-helper:1.0.0-dev",
                "ghcr.io/my-org/my-app-backend:1.0.0-dev",
            ],
        )
        mock_resolve.return_value = _DIGEST_BACKEND

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        # resolve_digest invoked once, against the backend ref only.
        mock_resolve.assert_called_once_with(
            "ghcr.io/my-org/my-app-backend:1.0.0-dev"
        )
        kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        assert kwargs["digest_map"] == {
            "ghcr.io/my-org/my-app-backend:1.0.0-dev": _DIGEST_BACKEND,
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
    def test_no_build_no_push_resolve_failure_aborts_by_default(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, mock_resolve,
        tmp_path,
    ):
        # Catalog-only republish now aborts loudly when resolve_digest
        # fails. The previous silent soft-fall to tag-only entries hid
        # upstream image-name mismatches that produced unpullable refs
        # in the catalog (see ENG-4909).
        from kamiwaza_extensions.image_pusher import ImagePushError

        mock_resolve.side_effect = ImagePushError("docker not found")
        _wire_publish_mocks(
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
            run_publish(stage="dev", no_build=True, no_push=True)

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
        # The stale-registry hazard does not apply under --dry-run
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
        # Escape hatch: explicit --digest lets the user opt out of
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
        # When --digest is set and push happened, resolve_digest is
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
        # Registry returns a different digest from what user supplied
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
                    "image": "ghcr.io/my-org/my-app-backend:1.0.0",
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
                    "image": "ghcr.io/my-org/my-app-backend:1.0.0",
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

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_dry_run_digest_pins_canonical_when_profiled_helper_first(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, tmp_path,
    ):
        # Same dict-order trap as the live path, but inside the dry-run
        # branch. A profiled helper appearing first in the compose dict
        # must not steal the user's --digest pin — dry-run must filter
        # via buildable_services the same way the live path does so the
        # digest binds to the canonical buildable ref that
        # ComposeTransformer actually keeps.
        compose = {
            "services": {
                "dev-helper": {
                    "build": {"context": "./dev"},
                    "image": "ghcr.io/my-org/my-app-dev-helper:1.0.0",
                    "profiles": ["dev"],   # FIRST in dict order
                },
                "backend": {
                    "build": {"context": "."},
                    "image": "ghcr.io/my-org/my-app-backend:1.0.0",
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
            # Dry-run preview pins against the canonical (non-profiled)
            # ref, not against the dev-helper that appears first.
            assert kwargs["digest_map"] == {
                "ghcr.io/my-org/my-app-backend:1.0.0-dev": _DIGEST_USER_SUPPLIED,
            }
            assert "dev-helper" not in str(kwargs["digest_map"])

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_dry_run_digest_uses_appgarden_namespace(
        self, mock_detector_cls, mock_meta_validator_cls,
        mock_compose_validator_cls, mock_transformer_cls,
        mock_profile_mgr_cls, mock_builder_cls, mock_pusher_cls,
        mock_reg_builder_cls, mock_publisher_cls, tmp_path,
    ):
        # When appgarden compose declares a different namespace than the
        # source compose AND the user passes --digest, the dry-run preview
        # must pin the digest against the appgarden ref — that's what the
        # live path would write, and what _retag_appgarden_compose writes
        # into the catalog. Reading from source compose would produce a
        # mismatched preview that "passes" while the live path would
        # silently fail _apply_digests' exact-string match.
        (tmp_path / "docker-compose.appgarden.yml").write_text(
            "services:\n"
            "  backend:\n"
            "    image: ghcr.io/published/tool-foo/backend:1.0.0\n"
        )
        compose = {
            "services": {
                "backend": {
                    "build": {"context": "."},
                    "image": "registry.test/my-app-backend:1.0.0",
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
            assert kwargs["digest_map"] == {
                "ghcr.io/published/tool-foo/backend:1.0.0-dev": _DIGEST_USER_SUPPLIED,
            }


# ------------------------------------------------------------------
# Appgarden compose preference (ENG-4907)
# ------------------------------------------------------------------


class TestLoadAppgardenCompose:
    """`_load_appgarden_compose` reads `docker-compose.appgarden.yml`."""

    def test_returns_none_when_file_missing(self, tmp_path):
        from kamiwaza_extensions.commands.publish import _load_appgarden_compose

        assert _load_appgarden_compose(tmp_path) is None

    def test_returns_path_and_data_when_present(self, tmp_path):
        from kamiwaza_extensions.commands.publish import _load_appgarden_compose

        appgarden = tmp_path / "docker-compose.appgarden.yml"
        appgarden.write_text(
            "services:\n"
            "  backend:\n"
            "    image: ghcr.io/my-org/my-app-backend:1.0.0\n"
        )
        result = _load_appgarden_compose(tmp_path)
        assert result is not None
        path, data = result
        assert path == appgarden
        assert data["services"]["backend"]["image"] == (
            "ghcr.io/my-org/my-app-backend:1.0.0"
        )

    def test_returns_none_on_malformed_yaml(self, tmp_path):
        from kamiwaza_extensions.commands.publish import _load_appgarden_compose

        (tmp_path / "docker-compose.appgarden.yml").write_text(
            "services:\n  backend: {unclosed\n"
        )
        assert _load_appgarden_compose(tmp_path) is None

    def test_returns_none_when_top_level_not_mapping(self, tmp_path):
        from kamiwaza_extensions.commands.publish import _load_appgarden_compose

        (tmp_path / "docker-compose.appgarden.yml").write_text("- not\n- a mapping\n")
        assert _load_appgarden_compose(tmp_path) is None


class TestReplaceImageTag:
    """`_replace_image_tag` preserves the namespace and replaces the tag."""

    def test_replaces_simple_tag(self):
        from kamiwaza_extensions.compose_transformer import _replace_image_tag

        assert _replace_image_tag(
            "ghcr.io/my-org/foo:1.0.0", "1.0.0-dev"
        ) == "ghcr.io/my-org/foo:1.0.0-dev"

    def test_replaces_tag_when_namespace_does_not_match_convention(self):
        # The whole point of the helper: omniparse's image is at
        # `images/omniparse`, not `images/tool-omniparse-omniparse-server`.
        # The replacement only touches the tag, leaves the path alone.
        from kamiwaza_extensions.compose_transformer import _replace_image_tag

        assert _replace_image_tag(
            "ghcr.io/kamiwaza-internal/kamiwaza-extensions-omniparse/images/omniparse:2.0.14",
            "2.0.14-dev",
        ) == (
            "ghcr.io/kamiwaza-internal/kamiwaza-extensions-omniparse/images/omniparse:2.0.14-dev"
        )

    def test_handles_registry_with_port(self):
        # `localhost:5000/foo:tag` — the port colon must not be mistaken
        # for the tag separator.
        from kamiwaza_extensions.compose_transformer import _replace_image_tag

        assert _replace_image_tag(
            "localhost:5000/foo:1.0.0", "2.0.0-dev"
        ) == "localhost:5000/foo:2.0.0-dev"

    def test_strips_digest_before_retagging(self):
        from kamiwaza_extensions.compose_transformer import _replace_image_tag

        assert _replace_image_tag(
            "ghcr.io/my-org/foo:1.0.0@sha256:" + "a" * 64,
            "1.0.0-dev",
        ) == "ghcr.io/my-org/foo:1.0.0-dev"

    def test_appends_tag_when_no_existing_tag(self):
        from kamiwaza_extensions.compose_transformer import _replace_image_tag

        assert _replace_image_tag(
            "ghcr.io/my-org/foo", "1.0.0-dev"
        ) == "ghcr.io/my-org/foo:1.0.0-dev"


class TestRetagAppgardenCompose:
    """`_retag_appgarden_compose` retags only build-context services."""

    def test_retags_build_context_service(self):
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        appgarden = {
            "services": {
                "backend": {"image": "ghcr.io/my-org/my-app-backend:2.0.0"},
            },
        }
        source = {
            "services": {
                "backend": {"build": {"context": "."}},
            },
        }
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="my-app", image_tag="2.0.0-dev",
            registry="ghcr.io/my-org",
        )
        assert out["services"]["backend"]["image"] == (
            "ghcr.io/my-org/my-app-backend:2.0.0-dev"
        )

    def test_retag_appgarden_preserves_divergent_namespace(self):
        # Omniparse-style: bake target name (and GHCR path) is
        # `images/omniparse`, but the kz-ext-computed default would be
        # `images/tool-omniparse-omniparse-server`. The fix preserves
        # the namespace declared in the appgarden compose and only
        # rewrites the tag for stage — fixes the silently-broken
        # catalog entries this used to produce (ENG-4909).
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        appgarden = {
            "services": {
                "omniparse-server": {
                    "image": (
                        "ghcr.io/kamiwaza-internal/"
                        "kamiwaza-extensions-omniparse/images/omniparse:2.0.14"
                    ),
                },
            },
        }
        source = {
            "services": {
                "omniparse-server": {"build": {"context": "."}},
            },
        }
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="tool-omniparse", image_tag="2.0.14-dev",
            registry=(
                "ghcr.io/kamiwaza-internal/"
                "kamiwaza-extensions-omniparse/images"
            ),
        )
        # Namespace preserved; only the tag changed.
        assert out["services"]["omniparse-server"]["image"] == (
            "ghcr.io/kamiwaza-internal/"
            "kamiwaza-extensions-omniparse/images/omniparse:2.0.14-dev"
        )

    def test_falls_back_to_legacy_convention_when_no_declared_image(self):
        # Extensions that rely on auto-generated image fields (no
        # `image:` in compose, just a `build:` block) still get the
        # `{registry}/{ext}-{svc}:{tag}` form — preserves backward
        # compatibility with the original ComposeTransformer behavior.
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        appgarden = {"services": {"backend": {}}}
        source = {"services": {"backend": {"build": {"context": "."}}}}
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="my-app", image_tag="2.0.0-dev",
            registry="ghcr.io/my-org",
        )
        assert out["services"]["backend"]["image"] == (
            "ghcr.io/my-org/my-app-backend:2.0.0-dev"
        )

    def test_passes_external_image_through_unchanged(self):
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        appgarden = {
            "services": {
                "neo4j": {"image": "ghcr.io/upstream/neo4j:v5.26.21"},
            },
        }
        source = {
            "services": {
                "neo4j": {"image": "ghcr.io/upstream/neo4j:v5.26.21"},
            },
        }
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="my-app", image_tag="2.0.0-dev",
            registry="ghcr.io/my-org",
        )
        assert out["services"]["neo4j"]["image"] == (
            "ghcr.io/upstream/neo4j:v5.26.21"
        )

    def test_mixed_services_only_build_owned_retagged(self):
        """Graphiti-shaped: external neo4j + built graphiti."""
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        appgarden = {
            "services": {
                "neo4j": {
                    "image": "ghcr.io/upstream/neo4j:v5.26.21",
                    "environment": ["NEO4J_AUTH=neo4j/${NEO4J_PASSWORD:?must be set}"],
                },
                "graphiti": {
                    "image": (
                        "ghcr.io/my-org/service-graphiti-graphiti:2.3.1"
                    ),
                    "environment": ["NEO4J_HOST=${NEO4J_HOST:-neo4j}"],
                },
            },
        }
        source = {
            "services": {
                "neo4j": {"image": "ghcr.io/upstream/neo4j:v5.26.21"},
                "graphiti": {
                    "build": {"context": "."},
                    "image": "ghcr.io/my-org/service-graphiti-graphiti:2.3.1",
                },
            },
        }
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="service-graphiti", image_tag="2.3.1-dev",
            registry="ghcr.io/my-org",
        )
        assert out["services"]["neo4j"]["image"] == (
            "ghcr.io/upstream/neo4j:v5.26.21"
        )
        assert out["services"]["graphiti"]["image"] == (
            "ghcr.io/my-org/service-graphiti-graphiti:2.3.1-dev"
        )
        # Env placeholders preserved verbatim (ENG-4860 acceptance).
        assert out["services"]["graphiti"]["environment"] == [
            "NEO4J_HOST=${NEO4J_HOST:-neo4j}"
        ]
        # Original appgarden dict is not mutated (deep copy).
        assert appgarden["services"]["graphiti"]["image"] == (
            "ghcr.io/my-org/service-graphiti-graphiti:2.3.1"
        )

    def test_service_only_in_appgarden_passes_through(self):
        """Service the extension's sync-compose invented (not in source) → external."""
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        appgarden = {
            "services": {
                "sidecar": {"image": "ghcr.io/third-party/sidecar:1.0"},
            },
        }
        source = {"services": {}}
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="my-app", image_tag="2.0.0-dev",
            registry="ghcr.io/my-org",
        )
        assert out["services"]["sidecar"]["image"] == (
            "ghcr.io/third-party/sidecar:1.0"
        )

    def test_revision_tag_overrides_stage_tag(self):
        """`--revision` passes through `image_tag` verbatim (CI SHA-pinning)."""
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        appgarden = {"services": {"backend": {"image": "ghcr.io/my-org/my-app-backend:2.0.0"}}}
        source = {"services": {"backend": {"build": {"context": "."}}}}
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="my-app", image_tag="2.0.0-abc123",
            registry="ghcr.io/my-org",
        )
        assert out["services"]["backend"]["image"] == (
            "ghcr.io/my-org/my-app-backend:2.0.0-abc123"
        )

    def test_profiled_build_service_not_retagged(self):
        """Service with `build:` + `profiles:` in source must not be retagged.

        Mirrors `buildable_services` filter in `run_publish`: profiled
        services are local-only and excluded from `published_refs`/digest
        pinning. Retagging them anyway would ship a catalog ref pointing
        at an image that was never built/pushed.
        """
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        appgarden = {
            "services": {
                "backend": {"image": "ghcr.io/my-org/my-app-backend:2.0.0"},
                "dev-helper": {
                    "image": "ghcr.io/my-org/my-app-dev-helper:local-only"
                },
            },
        }
        source = {
            "services": {
                "backend": {"build": {"context": "."}},
                "dev-helper": {
                    "build": {"context": "."},
                    "profiles": ["dev-only"],
                },
            },
        }
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="my-app", image_tag="2.0.0-dev",
            registry="ghcr.io/my-org",
        )
        # Non-profiled build service: retagged.
        assert out["services"]["backend"]["image"] == (
            "ghcr.io/my-org/my-app-backend:2.0.0-dev"
        )
        # Profiled build service: pass through, NOT retagged.
        assert out["services"]["dev-helper"]["image"] == (
            "ghcr.io/my-org/my-app-dev-helper:local-only"
        )

    def test_sibling_service_sharing_built_image_repo_is_retagged(self):
        """Multi-service-one-image idiom: sibling with same image repo
        as a build service must be retagged + digest-pinned alongside it
        (ENG-5648).

        Milvus-shape: a one-shot init container reuses the main image to
        chown/migrate/seed. Pre-fix the sibling shipped at its
        source-authored tag (e.g. ``:2.3.0``) which was never pushed →
        catalog ref unpullable → ImagePullBackOff on deploy.
        """
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        image = (
            "ghcr.io/kamiwaza-internal/"
            "kamiwaza-extensions-milvus/images/service-milvus:2.3.0"
        )
        appgarden = {
            "services": {
                "standalone-init": {"image": image, "user": "0:0"},
                "standalone": {"image": image, "user": "65532:65532"},
            },
        }
        source = {
            "services": {
                "standalone-init": {"image": image, "user": "0:0"},
                "standalone": {
                    "build": {"context": ".", "dockerfile": "Dockerfile"},
                    "image": image,
                },
            },
        }
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="service-milvus", image_tag="develop",
            registry=(
                "ghcr.io/kamiwaza-internal/"
                "kamiwaza-extensions-milvus/images"
            ),
        )
        expected = (
            "ghcr.io/kamiwaza-internal/"
            "kamiwaza-extensions-milvus/images/service-milvus:develop"
        )
        assert out["services"]["standalone"]["image"] == expected
        # Sibling without `build:` retagged to match — both will resolve
        # to the same digest in `digest_map` downstream.
        assert out["services"]["standalone-init"]["image"] == expected

    def test_sibling_in_same_namespace_but_different_repo_passes_through(self):
        """Safety boundary: a sibling whose image repo is in our
        namespace but was NOT built by this publish passes through
        verbatim. Rewriting it would point the catalog at a ``:dev``
        tag that was never pushed and break ``_auto_resolve_digests``
        with a confusing 404.
        """
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        appgarden = {
            "services": {
                "backend": {
                    "image": "ghcr.io/my-org/my-app/images/backend:1.0"
                },
                "helper": {
                    # Same namespace prefix (my-app), but `some-other-tool`
                    # is a different repo nothing here builds.
                    "image": (
                        "ghcr.io/my-org/my-app/images/some-other-tool:1.0"
                    ),
                },
            },
        }
        source = {
            "services": {
                "backend": {
                    "build": {"context": "."},
                    "image": "ghcr.io/my-org/my-app/images/backend:1.0",
                },
                # No build → not in built_repos.
                "helper": {
                    "image": (
                        "ghcr.io/my-org/my-app/images/some-other-tool:1.0"
                    ),
                },
            },
        }
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="my-app", image_tag="dev",
            registry="ghcr.io/my-org/my-app/images",
        )
        assert out["services"]["backend"]["image"] == (
            "ghcr.io/my-org/my-app/images/backend:dev"
        )
        # Unbuilt sibling-in-namespace: passed through, safety boundary
        # preserves external-passthrough semantics for refs we didn't
        # produce.
        assert out["services"]["helper"]["image"] == (
            "ghcr.io/my-org/my-app/images/some-other-tool:1.0"
        )

    def test_sibling_of_profiled_build_service_not_retagged(self):
        """A sibling sharing the image repo of a profile-gated build
        service must NOT be retagged. The profiled service's image is
        never pushed (excluded from ``built_repos``), so the sibling
        falls through as external.
        """
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        helper_image = "ghcr.io/my-org/my-app-dev-helper:local-only"
        appgarden = {
            "services": {
                "backend": {"image": "ghcr.io/my-org/my-app-backend:2.0.0"},
                "dev-helper": {
                    "build": {"context": "."},
                    "image": helper_image,
                },
                # Hypothetical sibling reusing the profiled helper's
                # image to seed test data; would only ever run alongside
                # the dev-only helper.
                "dev-helper-init": {"image": helper_image},
            },
        }
        source = {
            "services": {
                "backend": {"build": {"context": "."}},
                "dev-helper": {
                    "build": {"context": "."},
                    "image": helper_image,
                    "profiles": ["dev-only"],
                },
                "dev-helper-init": {"image": helper_image},
            },
        }
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="my-app", image_tag="2.0.0-dev",
            registry="ghcr.io/my-org",
        )
        assert out["services"]["backend"]["image"] == (
            "ghcr.io/my-org/my-app-backend:2.0.0-dev"
        )
        # Profiled service still passes through.
        assert out["services"]["dev-helper"]["image"] == helper_image
        # Its sibling does too — the profiled service did not contribute
        # to built_repos.
        assert out["services"]["dev-helper-init"]["image"] == helper_image

    def test_sibling_retagged_when_build_service_uses_legacy_fallback(self):
        """When a build service has no declared ``image:``,
        ``_canonical_build_ref`` falls back to
        ``{registry}/{ext}-{svc}:{tag}``. A sibling that explicitly
        references that fallback repo (rare but legal) should still be
        recognized as built and retagged.
        """
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        fallback_repo = "ghcr.io/my-org/my-app-backend"
        appgarden = {
            "services": {
                "backend": {},
                "backend-init": {"image": f"{fallback_repo}:1.0"},
            },
        }
        source = {
            "services": {
                # No image → legacy fallback applies.
                "backend": {"build": {"context": "."}},
                "backend-init": {"image": f"{fallback_repo}:1.0"},
            },
        }
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="my-app", image_tag="dev",
            registry="ghcr.io/my-org",
        )
        assert out["services"]["backend"]["image"] == f"{fallback_repo}:dev"
        assert out["services"]["backend-init"]["image"] == f"{fallback_repo}:dev"

    def test_sibling_matches_when_build_uses_image_basename_override(self):
        """Sibling-image gate must honor ``image_basename`` so a build
        service using the override and a sibling pointing at the same
        override-derived repo both retag together (ENG-5648 × ENG-5643).

        Without threading ``image_basename`` into ``compute_canonical_refs``
        inside ``_retag_appgarden_compose``, ``built_repos`` would be
        keyed on ``extension_name`` while the rewrite uses
        ``image_basename`` — the sibling check would miss.
        """
        from kamiwaza_extensions.commands.publish import _retag_appgarden_compose

        fallback_repo = "ghcr.io/my-org/outcome-d563-workroom-manager-backend"
        appgarden = {
            "services": {
                # Build service: no image declared → uses image_basename
                # fallback at deploy time.
                "backend": {},
                # Sibling pointing at the override-derived ref.
                "backend-init": {"image": f"{fallback_repo}:1.0"},
            },
        }
        source = {
            "services": {
                "backend": {"build": {"context": "."}},
                "backend-init": {"image": f"{fallback_repo}:1.0"},
            },
        }
        out = _retag_appgarden_compose(
            appgarden, source,
            extension_name="workroom-manager",
            image_tag="dev",
            registry="ghcr.io/my-org",
            image_basename="outcome-d563-workroom-manager",
        )
        assert out["services"]["backend"]["image"] == f"{fallback_repo}:dev"
        # Sibling caught only when built_repos honors image_basename.
        assert out["services"]["backend-init"]["image"] == f"{fallback_repo}:dev"


class TestPublishWithAppgarden:
    """End-to-end: appgarden.yml on disk skips ComposeTransformer.transform."""

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_appgarden_file_bypasses_generic_transform(
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
        # Authored appgarden compose carries the extension's intended shape.
        (tmp_path / "docker-compose.appgarden.yml").write_text(
            "services:\n"
            "  neo4j:\n"
            "    image: ghcr.io/upstream/neo4j:v5.26.21\n"
            "  backend:\n"
            "    image: ghcr.io/my-org/my-app-backend:1.0.0\n"
            "    environment:\n"
            "      - HOSTNAME_FROM_APPGARDEN=set-by-sync-compose\n"
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
            compose_data={
                "services": {
                    "neo4j": {"image": "ghcr.io/upstream/neo4j:v5.26.21"},
                    "backend": {
                        "build": {"context": "."},
                        "image": "ghcr.io/my-org/my-app-backend:1.0.0",
                    },
                },
            },
        )

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        # ComposeTransformer.transform must NOT be invoked when appgarden present.
        mock_transformer_cls.return_value.transform.assert_not_called()

        # The compose validator was called against the appgarden file path.
        validator_args, _ = (
            mock_compose_validator_cls.return_value.validate.call_args
        )
        assert validator_args[0].name == "docker-compose.appgarden.yml"

        # Catalog entry's `transformed_compose` is the retagged appgarden:
        # - backend (had build:) → retagged with stage suffix
        # - neo4j (external) → unchanged
        # - environment values preserved verbatim from appgarden.yml
        entry_kwargs = wired["reg_builder"].build_entry.call_args.kwargs
        transformed = entry_kwargs["transformed_compose"]
        assert transformed["services"]["backend"]["image"] == (
            "ghcr.io/my-org/my-app-backend:1.0.0-dev"
        )
        assert transformed["services"]["neo4j"]["image"] == (
            "ghcr.io/upstream/neo4j:v5.26.21"
        )
        assert transformed["services"]["backend"]["environment"] == [
            "HOSTNAME_FROM_APPGARDEN=set-by-sync-compose"
        ]

    @patch("kamiwaza_extensions.catalog_publisher.CatalogPublisher")
    @patch("kamiwaza_extensions.registry_builder.RegistryBuilder")
    @patch("kamiwaza_extensions.image_pusher.ImagePusher")
    @patch("kamiwaza_extensions.image_builder.ImageBuilder")
    @patch("kamiwaza_extensions.profile_manager.ProfileManager")
    @patch("kamiwaza_extensions.compose_transformer.ComposeTransformer")
    @patch("kamiwaza_extensions.validators.compose.ComposeValidator")
    @patch("kamiwaza_extensions.validators.metadata.MetadataValidator")
    @patch("kamiwaza_extensions.extension_detector.ExtensionDetector")
    def test_no_appgarden_falls_back_to_generic_transform(
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
        # No docker-compose.appgarden.yml on disk → legacy behavior.
        _wire_publish_mocks(
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

        run_publish(stage="dev")

        mock_transformer_cls.return_value.transform.assert_called_once()
        # Validator called against the source docker-compose.yml path.
        validator_args, _ = (
            mock_compose_validator_cls.return_value.validate.call_args
        )
        assert validator_args[0].name == "docker-compose.yml"

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
    def test_appgarden_image_namespace_drives_build_and_push(
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
        mock_resolve,
        tmp_path,
    ):
        # When ``docker-compose.appgarden.yml`` declares a different image
        # namespace than the source ``docker-compose.yml``, the appgarden
        # file is the source of truth (matches what gets written into the
        # catalog by ``_retag_appgarden_compose``). Build, push, and
        # digest resolution must all happen at the appgarden ref —
        # otherwise the catalog references an image the registry was
        # never told to expect.
        (tmp_path / "docker-compose.appgarden.yml").write_text(
            "services:\n"
            "  backend:\n"
            "    image: ghcr.io/published/tool-foo/backend:1.0.0\n"
        )
        _wire_publish_mocks(
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
            compose_data={
                "services": {
                    "backend": {
                        "build": {"context": "."},
                        "image": "registry.test/my-app-backend:1.0.0",
                    },
                },
            },
        )
        mock_resolve.return_value = "sha256:" + "a" * 64

        from kamiwaza_extensions.commands.publish import run_publish

        run_publish(stage="dev")

        # ImageBuilder.build receives the appgarden-declared ref, not the
        # source-compose ref. ``image_refs`` is keyed by service name.
        builder_kwargs = mock_builder_cls.return_value.build.call_args.kwargs
        assert builder_kwargs["image_refs"] == {
            "backend": "ghcr.io/published/tool-foo/backend:1.0.0-dev",
        }
        # Digest auto-resolve queries the registry at the appgarden ref.
        mock_resolve.assert_called_once_with(
            "ghcr.io/published/tool-foo/backend:1.0.0-dev"
        )


# ------------------------------------------------------------------
# ENG-5643 E2E: kamiwaza.json `image_basename` override end-to-end
# ------------------------------------------------------------------


class TestPublishDryRunImageBasename:
    """End-to-end dry-run verification of the `image_basename` override.

    Unit tests pin `_canonical_build_ref` math; these drive `run_publish`
    with `dry_run=True` against a synthetic extension (kamiwaza.json +
    docker-compose.yml mimicking workroom-manager's flat layout — two
    services, build: only, no declared image:) and assert that the
    canonical image refs reaching the catalog actually use the override.

    Three runs:

      A. ``name=workroom-manager-test`` + ``image_basename=outcome-d563-
         workroom-manager-test`` → refs use the override.
      B. Same name, ``image_basename`` field omitted → refs fall back
         to ``name`` (today's behavior, backwards-compat guarantee).
      C. Same name, ``image_basename=""`` (blank) → normalized to None,
         same refs as run B.

    ComposeTransformer runs for real so the full ref-synthesis path is
    exercised; only the I/O-bound seams (detector, validators, profile,
    catalog writer) are mocked.
    """

    _REGISTRY = (
        "ghcr.io/kamiwaza-internal/"
        "kamiwaza-extensions-workroom-manager/images"
    )

    @staticmethod
    def _compose_data() -> Dict[str, Any]:
        # Mimic workroom-manager's flat docker-compose.yml — both services
        # have build: contexts and no image: field. This is the layout
        # that triggers the legacy fallback synthesis where the bug bit.
        return {
            "services": {
                "backend": {"build": "./backend"},
                "frontend": {"build": "./frontend"},
            },
        }

    @staticmethod
    def _metadata(name: str, image_basename: Optional[str]) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "name": name,
            "version": "0.13.0",
            "source_type": "kamiwaza",
            "visibility": "public",
            "description": "ENG-5643 e2e",
            "risk_tier": 0,
            "verified": False,
        }
        if image_basename is not None:
            meta["image_basename"] = image_basename
        return meta

    @classmethod
    def _make_info(
        cls,
        tmp_path: Path,
        *,
        name: str,
        image_basename: Optional[str],
    ) -> Any:
        from kamiwaza_extensions.extension_detector import ExtensionInfo

        # Apply the same blank-to-None normalization ExtensionDetector
        # does at read time, so the synthetic ExtensionInfo matches
        # what the real loader would produce for these manifests.
        if isinstance(image_basename, str) and not image_basename.strip():
            effective = None
        else:
            effective = image_basename
        return ExtensionInfo(
            path=tmp_path,
            name=name,
            version="0.13.0",
            metadata=cls._metadata(name, image_basename),
            compose_path=tmp_path / "docker-compose.yml",
            compose_data=cls._compose_data(),
            image_basename=effective,
        )

    @classmethod
    def _profile(cls):
        from kamiwaza_extensions.profile_manager import PublishProfile

        return PublishProfile(
            name="dev",
            registry=cls._REGISTRY,
            catalog_endpoint="https://s3.example.com",
            catalog_bucket="my-catalog",
            catalog_credentials="env",
        )

    def _drive(self, info: Any) -> Dict[str, str]:
        # Real ComposeTransformer runs — only I/O seams are mocked so the
        # full ref-synthesis path is exercised end-to-end.
        with patch(
            "kamiwaza_extensions.catalog_publisher.CatalogPublisher"
        ) as mock_publisher_cls, patch(
            "kamiwaza_extensions.registry_builder.RegistryBuilder"
        ) as mock_reg_builder_cls, patch(
            "kamiwaza_extensions.profile_manager.ProfileManager"
        ) as mock_profile_mgr_cls, patch(
            "kamiwaza_extensions.validators.compose.ComposeValidator"
        ) as mock_compose_validator_cls, patch(
            "kamiwaza_extensions.validators.metadata.MetadataValidator"
        ) as mock_meta_validator_cls, patch(
            "kamiwaza_extensions.extension_detector.ExtensionDetector"
        ) as mock_detector_cls:
            mock_detector_cls.return_value.detect.return_value = info
            mock_meta_validator_cls.return_value.validate.return_value = (
                _make_validation_result()
            )
            mock_compose_validator_cls.return_value.validate.return_value = (
                _make_validation_result()
            )
            mock_profile_mgr_cls.return_value.resolve_profile.return_value = (
                self._profile()
            )
            mock_reg_builder_cls.return_value.build_entry.return_value = {
                "name": info.name, "version": info.version,
            }
            mock_publisher_cls.return_value.publish.return_value = (
                _make_publish_result(dry_run=True)
            )

            from kamiwaza_extensions.commands.publish import run_publish

            run_publish(stage="dev", dry_run=True, revision="testrev")

            # Capture the transformed-compose argument the catalog reg-builder
            # receives. transformed["services"][svc]["image"] is the canonical
            # ref the catalog (and therefore the operator) will see.
            kwargs = (
                mock_reg_builder_cls.return_value.build_entry.call_args.kwargs
            )
            transformed = kwargs["transformed_compose"]
            return {
                svc: transformed["services"][svc]["image"]
                for svc in ("backend", "frontend")
            }

    def test_run_a_image_basename_override_used(self, tmp_path):
        # Bug repro: name diverges from bake-target basename. With the
        # override set, refs must use the basename — not `name`.
        info = self._make_info(
            tmp_path,
            name="workroom-manager-test",
            image_basename="outcome-d563-workroom-manager-test",
        )
        refs = self._drive(info)
        prefix = self._REGISTRY
        assert refs == {
            "backend": f"{prefix}/outcome-d563-workroom-manager-test-backend:testrev",
            "frontend": f"{prefix}/outcome-d563-workroom-manager-test-frontend:testrev",
        }
        # Override name has the manifest name as a SUBSTRING — anchor
        # the negative invariant against the captured ref values
        # themselves, not the formatted dry-run preview line. (Preview
        # line spacing is presentation-layer churn that shouldn't
        # break this test.)
        for ref in refs.values():
            assert (
                f"{prefix}/workroom-manager-test-" not in ref
            ), f"unexpected legacy fallback in ref: {ref!r}"

    def test_run_b_no_override_falls_back_to_name(self, tmp_path):
        # Backwards-compat: a manifest without the field must produce
        # the same refs the codebase produced before this PR.
        info = self._make_info(
            tmp_path,
            name="workroom-manager-test",
            image_basename=None,
        )
        refs = self._drive(info)
        prefix = self._REGISTRY
        assert refs == {
            "backend": f"{prefix}/workroom-manager-test-backend:testrev",
            "frontend": f"{prefix}/workroom-manager-test-frontend:testrev",
        }

    def test_run_c_empty_string_normalizes_to_none(self, tmp_path):
        # Blank override → normalized to None → identical to run B.
        # Guards against an empty value silently producing
        # `{registry}/-{svc}:tag` refs.
        info = self._make_info(
            tmp_path,
            name="workroom-manager-test",
            image_basename="",
        )
        refs = self._drive(info)
        prefix = self._REGISTRY
        assert refs == {
            "backend": f"{prefix}/workroom-manager-test-backend:testrev",
            "frontend": f"{prefix}/workroom-manager-test-frontend:testrev",
        }
