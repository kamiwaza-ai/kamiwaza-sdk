"""Tests for platform_compat module (OperatorImagePin)."""

from __future__ import annotations

import pytest

from kamiwaza_extensions.platform_compat import (
    OPERATOR_COMPATIBLE_TAGS,
    OPERATOR_IMAGE,
    is_compatible_tag,
    is_local_connection,
    parse_image_ref,
    validate_compatible_tag_grammar,
)


@pytest.mark.unit
class TestPlatformCompatConstants:
    def test_operator_image_is_full_repo(self):
        assert OPERATOR_IMAGE.startswith("ghcr.io/")
        assert OPERATOR_IMAGE.endswith("/extension-operator")

    def test_compatible_tags_non_empty(self):
        assert len(OPERATOR_COMPATIBLE_TAGS) >= 1

    def test_every_compatible_tag_matches_release_grammar(self):
        # TS-13 sanity-check (offline portion): every tag in the pin list
        # must follow the canonical release-X.Y.Z grammar so a typo can be
        # caught before the GHCR resolve step runs.
        for tag in OPERATOR_COMPATIBLE_TAGS:
            assert validate_compatible_tag_grammar(tag), (
                f"{tag!r} does not match release-X.Y.Z grammar"
            )


@pytest.mark.unit
class TestParseImageRef:
    def test_simple_tag(self):
        repo, tag = parse_image_ref("ghcr.io/x/y:release-0.12.1")
        assert repo == "ghcr.io/x/y"
        assert tag == "release-0.12.1"

    def test_no_tag(self):
        repo, tag = parse_image_ref("ghcr.io/x/y")
        assert repo == "ghcr.io/x/y"
        assert tag is None

    def test_registry_with_port(self):
        repo, tag = parse_image_ref("registry.local:5000/x/y:v1")
        assert repo == "registry.local:5000/x/y"
        assert tag == "v1"

    def test_digest(self):
        repo, tag = parse_image_ref("ghcr.io/x/y@sha256:abc123")
        assert repo == "ghcr.io/x/y"
        assert tag == "sha256:abc123"


@pytest.mark.unit
class TestIsCompatibleTag:
    def test_compatible(self):
        assert is_compatible_tag(OPERATOR_COMPATIBLE_TAGS[0]) is True

    def test_incompatible(self):
        assert is_compatible_tag("v0.1.1") is False

    def test_none(self):
        assert is_compatible_tag(None) is False


@pytest.mark.unit
class TestIsLocalConnection:
    """Review re-review PR #84 H1/H2: kubectl-based probes must skip when
    the Kamiwaza connection is remote, since local kube-context has no
    verified relationship to the remote cluster."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:7777/api",
            "https://127.0.0.1:8443/api",
            "http://[::1]:7777/api",
            "https://kamiwaza.test/api",     # kind-cluster convention
            "http://kamiwaza.local/api",     # mDNS / hosts file
            "https://Kamiwaza.Test/api",     # case-insensitive
            "https://my-machine.localhost/api",
        ],
    )
    def test_local_url_returns_true(self, url):
        assert is_local_connection(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://kamiwaza.cloud/api",
            "https://customer-prod.kamiwaza.cloud/api",
            "https://10.0.0.5/api",          # internal IP — still remote in our model
            "https://192.168.1.10/api",      # ditto; opt in via explicit /etc/hosts entry
            "https://api.example.com/v1",
        ],
    )
    def test_remote_url_returns_false(self, url):
        assert is_local_connection(url) is False

    def test_none_or_empty_returns_false(self):
        assert is_local_connection(None) is False
        assert is_local_connection("") is False
        assert is_local_connection("not-a-url") is False
