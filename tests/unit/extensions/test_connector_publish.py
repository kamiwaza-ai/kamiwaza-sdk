"""Unit tests for connector publishing (kz-ext publish for type: connector)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer
from kamiwaza_extensions.catalog_publisher import _TYPE_FILE_MAP
from kamiwaza_extensions.connector_publisher import (
    build_connector_entry,
    publish_connector,
)
from kamiwaza_extensions.extension_detector import ExtensionInfo, infer_extension_type

pytestmark = pytest.mark.unit


def _manifest() -> dict:
    return {
        "connector_type": "m365",
        "provider_id": "m365",
        "provider_label": "Microsoft 365",
        "icon": "data:image/svg+xml;base64,AAAA",
        "egress_allowlist": ["graph.microsoft.com"],
        "oauth": {"token_endpoint": "https://login/token", "flow": "device_code"},
        "deployment": {
            "image_repository": "kamiwaza-internal/connectors/m365",
            "image_registry": "ghcr.io",
            "image_tag": "1.0.0",
            "port": 8080,
        },
        "config_schema": {"type": "object"},
    }


_DEFAULT_MANIFEST = object()  # sentinel: distinguish "use default" from explicit None


def _info(manifest=_DEFAULT_MANIFEST, name: str = "m365") -> ExtensionInfo:
    if manifest is _DEFAULT_MANIFEST:
        manifest = _manifest()  # build a fresh manifest each call
    metadata = {"name": name, "version": "1.0.0", "type": "connector"}
    if manifest is not None:
        metadata["manifest"] = manifest
    return ExtensionInfo(
        path=Path("/tmp/connector-m365"),
        name=name,
        version="1.0.0",
        metadata=metadata,
    )


# --- detection + type map -------------------------------------------------


def test_infer_extension_type_recognizes_connector():
    assert infer_extension_type({"type": "connector"}) == "connector"
    assert infer_extension_type({"template_type": "connector"}) == "connector"
    # Connectors are explicit-type ONLY — a `connector-`-prefixed NAME must NOT
    # be misread as a connector (e.g. the `connector-builder` app).
    assert infer_extension_type({"name": "connector-builder"}) == "app"
    # unrelated types unaffected
    assert infer_extension_type({"name": "tool-foo"}) == "tool"
    assert infer_extension_type({"name": "plain"}) == "app"


def test_type_file_map_routes_connectors_to_connectors_json():
    assert _TYPE_FILE_MAP["connector"] == "connectors.json"


# --- entry construction ---------------------------------------------------


def test_build_connector_entry_carries_manifest_name_version_and_digest():
    entry = build_connector_entry(_info(), _manifest(), pinned_digest="sha256:deadbeef")
    assert entry["name"] == "m365"
    assert entry["version"] == "1.0.0"
    assert entry["connector_type"] == "m365"  # manifest field preserved
    assert entry["deployment"]["image_digest"] == "sha256:deadbeef"
    # original manifest deployment is not mutated
    assert "image_digest" not in _manifest()["deployment"]


def test_build_connector_entry_without_digest_omits_it():
    entry = build_connector_entry(_info(), _manifest(), pinned_digest=None)
    assert "image_digest" not in entry["deployment"]


# --- publish flow ---------------------------------------------------------


def _patches(publisher_mock):
    profile = SimpleNamespace(registry="ghcr.io", catalog_bucket="kamiwaza-catalog")
    return (
        patch(
            "kamiwaza_extensions.profile_manager.ProfileManager.resolve_profile",
            return_value=profile,
        ),
        patch(
            "kamiwaza_extensions.image_pusher.ImagePusher.check_buildx_available",
            return_value=None,
        ),
        patch(
            "kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest",
            return_value="sha256:abc123",
        ),
        patch(
            "kamiwaza_extensions.catalog_publisher.CatalogPublisher",
            return_value=publisher_mock,
        ),
    )


def test_publish_connector_publishes_manifest_to_connectors_catalog():
    publisher = MagicMock()
    publisher.publish.return_value = SimpleNamespace(
        action="insert", catalog_file="garden/v3/connectors.json", version="1.0.0"
    )
    p_profile, p_buildx, p_digest, p_pub = _patches(publisher)
    with p_profile, p_buildx, p_digest, p_pub:
        publish_connector(_info(), stage="prod")

    publisher.publish.assert_called_once()
    kwargs = publisher.publish.call_args.kwargs
    assert kwargs["extension_type"] == "connector"
    entry = kwargs["entry"]
    assert entry["name"] == "m365"
    assert entry["connector_type"] == "m365"
    # the pre-pushed image digest was resolved and pinned
    assert entry["deployment"]["image_digest"] == "sha256:abc123"


def test_publish_connector_honors_revision_as_image_tag():
    """--revision (the build's canonical tag) overrides the manifest's static
    deployment.image_tag for BOTH digest resolution and the published entry, so
    a stage/prod publish pins that stage's image instead of the authoring
    default (ENG-8617; prevents the ENG-8350 develop-pin tag mismatch)."""
    publisher = MagicMock()
    publisher.publish.return_value = SimpleNamespace(
        action="insert", catalog_file="garden/v3/connectors.json", version="1.0.0"
    )
    p_profile, p_buildx, p_digest, p_pub = _patches(publisher)
    with p_profile, p_buildx, p_digest as digest_mock, p_pub:
        publish_connector(_info(), stage="stage", revision="release-1.0.0")

    # digest resolved from the REVISION-tagged ref, not the manifest's :1.0.0
    assert (
        digest_mock.call_args.args[0]
        == "ghcr.io/kamiwaza-internal/connectors/m365:release-1.0.0"
    )
    entry = publisher.publish.call_args.kwargs["entry"]
    assert entry["deployment"]["image_tag"] == "release-1.0.0"
    assert entry["deployment"]["image_digest"] == "sha256:abc123"
    # original manifest is not mutated by the revision override
    assert _manifest()["deployment"]["image_tag"] == "1.0.0"


def test_publish_connector_resolves_digest_on_no_push():
    """--no-push means 'the image is already pushed, don't re-push' — resolution
    is a read-only lookup, so it must still run and pin the digest (matching the
    app catalog-only-republish path). Otherwise the entry is tag-only/mutable."""
    publisher = MagicMock()
    publisher.publish.return_value = SimpleNamespace(
        action="insert", catalog_file="garden/v3/connectors.json", version="1.0.0"
    )
    p_profile, p_buildx, p_digest, p_pub = _patches(publisher)
    with p_profile, p_buildx as buildx_mock, p_digest as digest_mock, p_pub:
        publish_connector(_info(), stage="prod", revision="release-1.0.0", no_push=True)

    buildx_mock.assert_called_once()  # preflight ran
    digest_mock.assert_called_once()  # digest resolved despite --no-push
    dep = publisher.publish.call_args.kwargs["entry"]["deployment"]
    assert dep["image_tag"] == "release-1.0.0"
    assert dep["image_digest"] == "sha256:abc123"  # pinned, not tag-only


def test_publish_connector_trusts_supplied_digest_on_no_push():
    """--no-push WITH an explicit --digest is the publish-only escape hatch: trust
    the precomputed digest and do no registry round-trip (no buildx needed),
    matching the app path."""
    publisher = MagicMock()
    publisher.publish.return_value = SimpleNamespace(
        action="insert", catalog_file="garden/v3/connectors.json", version="1.0.0"
    )
    p_profile, p_buildx, p_digest, p_pub = _patches(publisher)
    with p_profile, p_buildx as buildx_mock, p_digest as digest_mock, p_pub:
        publish_connector(_info(), stage="prod", no_push=True, digest="sha256:supplied")

    buildx_mock.assert_not_called()  # no registry round-trip
    digest_mock.assert_not_called()
    dep = publisher.publish.call_args.kwargs["entry"]["deployment"]
    assert dep["image_digest"] == "sha256:supplied"  # trusted as-is


def test_publish_connector_revision_drops_stale_digest_on_tag_change_dry_run():
    """On a dry run (no resolution), a revision that changes the tag must drop the
    manifest's now-stale authored digest rather than emit :<revision>@<old>."""
    manifest = _manifest()
    manifest["deployment"]["image_digest"] = "sha256:stale"
    publisher = MagicMock()
    publisher.publish.return_value = SimpleNamespace(
        action="insert", catalog_file="garden/v3/connectors.json", version="1.0.0"
    )
    p_profile, p_buildx, p_digest, p_pub = _patches(publisher)
    with p_profile, p_buildx, p_digest as digest_mock, p_pub:
        publish_connector(
            _info(manifest=manifest),
            stage="stage",
            revision="release-1.0.0",
            dry_run=True,
        )

    digest_mock.assert_not_called()  # dry run resolves nothing
    dep = publisher.publish.call_args.kwargs["entry"]["deployment"]
    assert dep["image_tag"] == "release-1.0.0"
    assert "image_digest" not in dep  # stale digest dropped on tag change


def test_publish_connector_revision_keeps_matching_digest_when_tag_unchanged():
    """A revision equal to the manifest's already-rendered tag must NOT strip its
    matching authored digest (e.g. CI rendered the final tag+digest, then
    republishes with --revision <same tag> --dry-run/--no-push)."""
    manifest = _manifest()  # deployment.image_tag == "1.0.0"
    manifest["deployment"]["image_digest"] = "sha256:pinned"
    publisher = MagicMock()
    publisher.publish.return_value = SimpleNamespace(
        action="insert", catalog_file="garden/v3/connectors.json", version="1.0.0"
    )
    p_profile, p_buildx, p_digest, p_pub = _patches(publisher)
    with p_profile, p_buildx, p_digest, p_pub:
        publish_connector(
            _info(manifest=manifest), stage="stage", revision="1.0.0", dry_run=True
        )

    dep = publisher.publish.call_args.kwargs["entry"]["deployment"]
    assert dep["image_tag"] == "1.0.0"
    assert dep["image_digest"] == "sha256:pinned"  # matching digest preserved


def test_publish_connector_verifies_supplied_digest_against_registry():
    """A supplied --digest that disagrees with the registry aborts (not trusted blind)."""
    publisher = MagicMock()
    p_profile, p_buildx, p_digest, p_pub = _patches(
        publisher
    )  # registry -> sha256:abc123
    with p_profile, p_buildx, p_digest as digest_mock, p_pub, pytest.raises(typer.Exit):
        publish_connector(_info(), stage="prod", digest="sha256:wrongwrong")

    digest_mock.assert_called_once()  # resolved to verify, not skipped
    publisher.publish.assert_not_called()


def test_publish_connector_accepts_matching_supplied_digest():
    """A supplied --digest that matches the registry is verified and pinned."""
    publisher = MagicMock()
    publisher.publish.return_value = SimpleNamespace(
        action="insert", catalog_file="garden/v3/connectors.json", version="1.0.0"
    )
    p_profile, p_buildx, p_digest, p_pub = _patches(
        publisher
    )  # registry -> sha256:abc123
    with p_profile, p_buildx, p_digest as digest_mock, p_pub:
        publish_connector(_info(), stage="prod", digest="sha256:abc123")

    digest_mock.assert_called_once()  # verified against the registry
    entry = publisher.publish.call_args.kwargs["entry"]
    assert entry["deployment"]["image_digest"] == "sha256:abc123"


def test_publish_connector_rejects_kamiwaza_json_without_manifest():
    with pytest.raises(typer.Exit):
        publish_connector(_info(manifest=None), stage="prod")


def test_publish_connector_rejects_manifest_missing_deployment_image():
    bad = _manifest()
    del bad["deployment"]["image_repository"]
    with pytest.raises(typer.Exit):
        publish_connector(_info(manifest=bad), stage="prod")
