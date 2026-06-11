"""Tests for the local catalog overlay client — ENG-6802."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
import yaml

from kamiwaza_extensions.catalog_overlay import (
    MAX_VERSION_LENGTH,
    build_overlay_entry,
    build_overlay_version,
    list_overlays,
    publish_overlay,
    remove_overlay,
)

pytestmark = pytest.mark.unit

TRANSFORMED = {
    "services": {
        "app": {"image": "registry.kamiwaza.test/kaizen-app:1.0.0-dev-abc1234.5"},
        "db": {"image": "postgres:16"},
    }
}
CANONICAL_REFS = {"app": "registry.kamiwaza.test/kaizen-app:1.0.0-dev-abc1234.5"}


class TestBuildOverlayVersion:
    def test_clean_tree(self):
        version = build_overlay_version(
            "1.0.0", branch="feat-x", sha="abc1234def", dirty=False
        )
        assert version == "1.0.0-dev.feat-x.abc1234"

    def test_dirty_tree_uses_dirty_marker(self):
        version = build_overlay_version(
            "1.0.0", branch="feat-x", sha="abc1234", dirty=True
        )
        assert version == "1.0.0-dev.feat-x.dirty"

    def test_branch_is_slugified(self):
        version = build_overlay_version(
            "1.0.0", branch="Feature/ENG-6802!x", sha="abc1234", dirty=False
        )
        assert version == "1.0.0-dev.feature-eng-6802-x.abc1234"

    def test_long_branch_truncated_to_column_limit(self):
        version = build_overlay_version(
            "1.0.0", branch="x" * 100, sha="abc1234", dirty=False
        )
        assert len(version) <= MAX_VERSION_LENGTH
        assert version.endswith(".abc1234")
        assert version.startswith("1.0.0-dev.")

    def test_no_git_info(self):
        version = build_overlay_version("1.0.0", branch=None, sha=None, dirty=False)
        assert version == "1.0.0-dev.nobranch.nogit"

    def test_not_pep440_parseable(self):
        # The unparseable version is itself a clobber guard on the platform.
        from packaging.version import InvalidVersion, Version

        version = build_overlay_version(
            "1.0.0", branch="feat-x", sha="abc1234", dirty=False
        )
        with pytest.raises(InvalidVersion):
            Version(version)


class TestBuildOverlayEntry:
    def test_digest_pins_built_services_only(self):
        entry = build_overlay_entry(
            version="1.0.0-dev.feat-x.abc1234",
            transformed_compose=TRANSFORMED,
            canonical_refs=CANONICAL_REFS,
            resolve_digest=lambda ref: "sha256:" + "a" * 64,
        )
        compose = yaml.safe_load(entry["compose_yml"])
        assert compose["services"]["app"]["image"] == (
            "registry.kamiwaza.test/kaizen-app:1.0.0-dev-abc1234.5@sha256:" + "a" * 64
        )
        # External (non-built) services are untouched.
        assert compose["services"]["db"]["image"] == "postgres:16"

    def test_digest_resolution_uses_push_ref(self):
        seen = []

        def resolver(ref):
            seen.append(ref)
            return "sha256:" + "b" * 64

        build_overlay_entry(
            version="v",
            transformed_compose=TRANSFORMED,
            canonical_refs=CANONICAL_REFS,
            push_ref_map={
                CANONICAL_REFS["app"]: "127.0.0.1:30010/kaizen-app:1.0.0-dev-abc1234.5"
            },
            resolve_digest=resolver,
        )
        # Queried via the host-reachable push ref...
        assert seen == ["127.0.0.1:30010/kaizen-app:1.0.0-dev-abc1234.5"]

    def test_digest_failure_degrades_to_tag_only(self):
        warnings = []

        def resolver(ref):
            raise RuntimeError("registry unreachable")

        entry = build_overlay_entry(
            version="v",
            transformed_compose=TRANSFORMED,
            canonical_refs=CANONICAL_REFS,
            resolve_digest=resolver,
            warn=warnings.append,
        )
        compose = yaml.safe_load(entry["compose_yml"])
        assert compose["services"]["app"]["image"] == CANONICAL_REFS["app"]
        assert len(warnings) == 1
        assert "registry unreachable" in warnings[0]

    def test_source_compose_not_mutated(self):
        original = yaml.safe_dump(TRANSFORMED)
        build_overlay_entry(
            version="v",
            transformed_compose=TRANSFORMED,
            canonical_refs=CANONICAL_REFS,
            resolve_digest=lambda ref: "sha256:" + "c" * 64,
        )
        assert yaml.safe_dump(TRANSFORMED) == original

    def test_metadata_fields_forwarded(self):
        entry = build_overlay_entry(
            version="v",
            transformed_compose=TRANSFORMED,
            canonical_refs={},
            metadata={
                "env_defaults": {"FOO": "bar", "PORT": 8080},
                "display_name": "Kaizen",
                "description": "desc",
                "type": "app",
            },
            git_sha="abc1234",
            git_branch="feat-x",
            dirty=True,
        )
        assert entry["env_defaults"] == {"FOO": "bar", "PORT": "8080"}
        assert entry["display_name"] == "Kaizen"
        assert entry["description"] == "desc"
        assert entry["template_type"] == "app"
        assert entry["shadow"] == {
            "git_sha": "abc1234",
            "git_branch": "feat-x",
            "dirty": True,
        }


class TestOverlayClient:
    def test_publish_overlay(self):
        client = MagicMock()
        client.put.return_value = {"shadow": {"shadows_version": "1.0.0"}}

        response = publish_overlay(client, "kaizen", {"version": "v"})

        client.put.assert_called_once_with(
            "/apps/app_templates/catalog/overlay/kaizen", json={"version": "v"}
        )
        assert response["shadow"]["shadows_version"] == "1.0.0"

    def test_remove_overlay(self):
        client = MagicMock()
        client.delete.return_value = {"restored_version": "1.0.0"}

        response = remove_overlay(client, "kaizen")

        client.delete.assert_called_once_with(
            "/apps/app_templates/catalog/overlay/kaizen"
        )
        assert response["restored_version"] == "1.0.0"

    def test_list_overlays(self):
        client = MagicMock()
        client.get.return_value = [{"template_name": "kaizen"}]

        assert list_overlays(client) == [{"template_name": "kaizen"}]
        client.get.assert_called_once_with("/apps/app_templates/catalog/overlay")


class TestImportInvariant:
    """ENG-6802 hard invariant: kz-ext dev can NEVER write a shared catalog.

    Enforced structurally — the dev path's import graph must not contain
    the S3/R2 publishing machinery. If this test fails, a change wired
    `catalog_publisher`, `profile_manager`, or `boto3` into the dev path;
    that is forbidden regardless of intent.
    """

    FORBIDDEN = (
        "kamiwaza_extensions.catalog_publisher",
        "kamiwaza_extensions.profile_manager",
        "boto3",
    )

    def _assert_clean_import_graph(self, module_name: str) -> None:
        import subprocess

        # Fresh interpreter so previously-imported modules can't mask a
        # module-level import sneaking in.
        code = (
            "import sys; "
            f"import {module_name}; "
            "import json; "
            "print(json.dumps(sorted(sys.modules.keys())))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
        import json

        loaded = set(json.loads(result.stdout.strip().splitlines()[-1]))
        forbidden_loaded = loaded & set(self.FORBIDDEN)
        assert not forbidden_loaded, (
            f"{module_name} pulled forbidden shared-catalog modules into the "
            f"dev path: {sorted(forbidden_loaded)}"
        )

    def test_catalog_overlay_never_imports_shared_catalog_machinery(self):
        self._assert_clean_import_graph("kamiwaza_extensions.catalog_overlay")

    def test_dev_command_never_imports_shared_catalog_machinery(self):
        self._assert_clean_import_graph("kamiwaza_extensions.commands.dev")

    def test_overlay_module_source_mentions_no_forbidden_imports(self):
        # Static belt-and-braces: lazy in-function imports would evade the
        # module-import probe above.
        from pathlib import Path

        import kamiwaza_extensions.catalog_overlay as overlay_mod
        import kamiwaza_extensions.commands.dev as dev_mod

        for mod in (overlay_mod, dev_mod):
            source = Path(mod.__file__).read_text()
            for forbidden in ("catalog_publisher", "profile_manager", "boto3"):
                assert f"import {forbidden}" not in source, (
                    f"{mod.__name__} imports {forbidden} — forbidden on the "
                    "dev path (ENG-6802 hard invariant)"
                )
                assert f"from kamiwaza_extensions.{forbidden}" not in source, (
                    f"{mod.__name__} imports from {forbidden} — forbidden on "
                    "the dev path (ENG-6802 hard invariant)"
                )
