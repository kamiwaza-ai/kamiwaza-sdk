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


def _info(manifest: dict | None = _manifest(), name: str = "m365") -> ExtensionInfo:
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
    entry = build_connector_entry(
        _info(), _manifest(), pinned_digest="sha256:deadbeef"
    )
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
    profile = SimpleNamespace(
        registry="ghcr.io", catalog_bucket="kamiwaza-catalog"
    )
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


def test_publish_connector_skips_digest_resolution_on_no_push():
    publisher = MagicMock()
    publisher.publish.return_value = SimpleNamespace(
        action="insert", catalog_file="garden/v3/connectors.json", version="1.0.0"
    )
    p_profile, p_buildx, p_digest, p_pub = _patches(publisher)
    with p_profile, p_buildx as buildx_mock, p_digest as digest_mock, p_pub:
        publish_connector(_info(), stage="prod", no_push=True)

    # --no-push does no registry round-trip at all: neither preflight nor resolve.
    buildx_mock.assert_not_called()
    digest_mock.assert_not_called()
    entry = publisher.publish.call_args.kwargs["entry"]
    assert "image_digest" not in entry["deployment"]


def test_publish_connector_verifies_supplied_digest_against_registry():
    """A supplied --digest that disagrees with the registry aborts (not trusted blind)."""
    publisher = MagicMock()
    p_profile, p_buildx, p_digest, p_pub = _patches(publisher)  # registry -> sha256:abc123
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
    p_profile, p_buildx, p_digest, p_pub = _patches(publisher)  # registry -> sha256:abc123
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
