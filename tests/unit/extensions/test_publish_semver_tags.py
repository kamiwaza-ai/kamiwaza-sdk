"""Exercise SemVer identity through the real publish CLI and catalog builder."""

from unittest.mock import MagicMock
import re

import pytest
import yaml
from typer.testing import CliRunner

from kamiwaza_extensions.catalog_publisher import CatalogPublisher as RealCatalogPublisher
from kamiwaza_extensions.cli import app
from kamiwaza_extensions.extension_detector import ExtensionInfo
from kamiwaza_extensions.profile_manager import PublishProfile
from kamiwaza_extensions.validators.result import ValidationResult

pytestmark = pytest.mark.unit
REGISTRY = "ghcr.io/test"
DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def publish_io(monkeypatch, tmp_path):
    """Mock external IO, retaining real compose and registry transformations."""
    targets = {
        "detector": "extension_detector.ExtensionDetector",
        "metadata": "validators.metadata.MetadataValidator",
        "compose": "validators.compose.ComposeValidator",
        "profile": "profile_manager.ProfileManager",
        "builder": "image_builder.ImageBuilder",
        "pusher": "image_pusher.ImagePusher",
        "publisher": "catalog_publisher.CatalogPublisher",
    }
    mocks = {name: MagicMock() for name in targets}
    for name, target in targets.items():
        monkeypatch.setattr(f"kamiwaza_extensions.{target}", mocks[name])
    for name in ("metadata", "compose"):
        mocks[name].return_value.validate.return_value = ValidationResult(passed=True)
    mocks["profile"].return_value.resolve_profile.return_value = PublishProfile(
        name="test",
        registry=REGISTRY,
        catalog_endpoint="https://example.test",
        catalog_bucket="isolated",
        catalog_credentials="env",
    )
    mocks["builder"].return_value.build.side_effect = lambda **kw: list(
        kw["image_refs"].values()
    )
    mocks["pusher"].resolve_digest.return_value = DIGEST
    mocks["publisher"].return_value.publish.return_value = MagicMock(version="test")
    return mocks


@pytest.mark.parametrize("revision", [None, "ci-123"])
@pytest.mark.parametrize("stage", ["prod", "dev"])
@pytest.mark.parametrize(
    "version", ["1.2.3", "1.2.3+build.42", "1.2.3+build-dev", "1.2.3+" + "x" * 180]
)
def test_cli_uses_safe_exact_artifacts_preserving_catalog_identity(
    publish_io, tmp_path, stage, version, revision
):
    compose = {
        "services": {
            "app": {
                "build": {"context": "."},
                "image": f"{REGISTRY}/app:{version}",
                "environment": {"HELPER_IMAGE": f"{REGISTRY}/helper:{version}"},
            }
        }
    }
    metadata = {
        "name": "app",
        "version": version,
        "description": "Test",
        "extra_docker_images": [f"{REGISTRY}/helper:{{version}}"],
    }
    publish_io["detector"].return_value.detect.return_value = ExtensionInfo(
        path=tmp_path,
        name="app",
        version=version,
        metadata=metadata,
        compose_path=tmp_path / "docker-compose.yml",
        compose_data=compose,
    )
    args = ["publish", "--stage", stage]
    if revision is not None:
        args += ["--revision", revision]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    builder = publish_io["builder"].return_value.build
    tag = builder.call_args.kwargs["revision_tag"]
    assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag)
    if revision is not None:
        assert tag == revision
    elif "+" not in version:
        assert tag == (version if stage == "prod" else f"{version}-{stage}")
    publish_io["pusher"].return_value.push.assert_called_once()
    assert publish_io["pusher"].return_value.push.call_args.args[0] == [
        f"{REGISTRY}/app:{tag}"
    ]
    entry = publish_io["publisher"].return_value.publish.call_args.kwargs["entry"]
    assert entry["version"] == version
    assert metadata["version"] == version
    assert entry["docker_images"] == [f"{REGISTRY}/app:{tag}@{DIGEST}"]
    assert entry["extra_docker_images"] == [f"{REGISTRY}/helper:{tag}@{DIGEST}"]
    rendered = yaml.safe_load(entry["compose_yml"])
    assert (
        rendered["services"]["app"]["environment"]["HELPER_IMAGE"]
        == entry["extra_docker_images"][0]
    )


def test_tags_do_not_alias_build_identities_prereleases_or_stages():
    from kamiwaza_extensions.publish_image_tag import publish_image_tag

    identities = [
        ("1.2.3+abc", "prod"),
        ("1.2.3-abc", "prod"),
        ("1.2.3+abc-dev", "prod"),
        ("1.2.3+abc", "dev"),
        ("1.2.3+" + "x" * 180, "prod"),
        ("1.2.3+" + "x" * 179 + "y", "prod"),
    ]
    tags = [publish_image_tag(*identity) for identity in identities]
    assert len(set(tags)) == len(identities)
    assert tags == [publish_image_tag(*identity) for identity in identities]
    assert publish_image_tag("1.2.3+abc", "prod", "ci-123") == "ci-123"


@pytest.mark.parametrize("existing_description", ["Test", "Different content"])
@pytest.mark.parametrize("flags", [[], ["--force"], ["--no-build"], ["--no-push", "--digest", DIGEST]])
def test_compat_known_identity_rejected_before_build_or_push(
    publish_io, tmp_path, monkeypatch, existing_description, flags
):
    info = ExtensionInfo(
        path=tmp_path, name="app", version="1.0.0",
        metadata={"name": "app", "version": "1.0.0", "description": "Test"},
        compose_path=tmp_path / "docker-compose.yml",
        compose_data={"services": {"app": {"build": ".", "image": f"{REGISTRY}/app:1.0.0"}}},
    )
    publish_io["detector"].return_value.detect.return_value = info
    publisher = publish_io["publisher"].return_value
    import io
    import json

    from botocore.exceptions import ClientError

    publisher._catalog_schema = "compat-v1"
    publisher._garden_dir = "garden/compat-v1/"
    publisher._ClientError = ClientError
    publisher._s3.get_object.return_value = {
        "ETag": '"known"',
        "Body": io.BytesIO(json.dumps([{
            "name": "app", "version": "1.0", "description": existing_description,
        }]).encode()),
    }
    publisher.preflight_new_release.side_effect = lambda *args: (
        RealCatalogPublisher.preflight_new_release(publisher, *args)
    )
    result = CliRunner().invoke(app, ["publish", "--stage", "prod", "--catalog-schema", "compat-v1", *flags])
    assert result.exit_code != 0
    publisher.preflight_new_release.assert_called_once_with("app", "1.0.0", "app")
    publish_io["builder"].assert_not_called()
    publish_io["pusher"].assert_not_called()
    publisher.publish.assert_not_called()
    publisher._s3.put_object.assert_not_called()


@pytest.mark.parametrize("flags", [[], ["--no-build", "--no-push"]])
def test_compat_new_or_publish_only_identity_reaches_catalog(publish_io, tmp_path, flags):
    publish_io["detector"].return_value.detect.return_value = ExtensionInfo(
        path=tmp_path, name="app", version="1.0.0",
        metadata={"name": "app", "version": "1.0.0", "description": "Test"},
        compose_path=tmp_path / "docker-compose.yml",
        compose_data={"services": {"app": {"build": ".", "image": f"{REGISTRY}/app:1.0.0"}}},
    )
    result = CliRunner().invoke(app, ["publish", "--stage", "prod", "--catalog-schema", "compat-v1", *flags])
    assert result.exit_code == 0, result.output
    publisher = publish_io["publisher"].return_value
    publisher.publish.assert_called_once()
    if flags:
        publisher.preflight_new_release.assert_not_called()
        publish_io["builder"].assert_not_called()
        publish_io["pusher"].assert_not_called()
    else:
        publisher.preflight_new_release.assert_called_once()
        publish_io["builder"].return_value.build.assert_called_once()
        publish_io["pusher"].return_value.push.assert_called_once()


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("service_count", [1, 2])
def test_compat_cli_dry_run_unknown_outputs_is_explicit_plan_only(publish_io, tmp_path, batch, service_count):
    info = ExtensionInfo(
        path=tmp_path, name="app", version="1.0.0",
        metadata={"name": "app", "version": "1.0.0", "description": "Test"},
        compose_path=tmp_path / "docker-compose.yml",
        compose_data={"services": {f"app{i}": {"build": ".", "image": f"{REGISTRY}/app{i}:1.0.0"} for i in range(service_count)}},
    )
    publish_io["detector"].return_value.detect.return_value = info
    publish_io["detector"].return_value.detect_all.return_value = [info]
    args = ["publish", "--stage", "prod", "--catalog-schema", "compat-v1", "--dry-run"]
    if batch:
        args.append("--all")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "PLAN ONLY" in result.output
    assert "unverified" in result.output
    publish_io["builder"].assert_not_called()
    publish_io["pusher"].assert_not_called()
    publish_io["publisher"].assert_not_called()
