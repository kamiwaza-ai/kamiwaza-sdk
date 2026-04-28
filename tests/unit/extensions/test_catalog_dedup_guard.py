"""Tests for CatalogDedupGuard + revision plumbing (ENG-3884 / §4.2.5)."""

from __future__ import annotations

import pytest

from kamiwaza_extensions.catalog_publisher import (
    CatalogDedupError,
    CatalogDedupGuard,
)
from kamiwaza_extensions.exit_codes import ExitCode


def _entry(name: str, version: str, revision: str | None = None) -> dict:
    out = {"name": name, "version": version, "description": "x"}
    if revision is not None:
        out["revision"] = revision
    return out


@pytest.mark.unit
class TestRevisionGrammar:
    # TS-24: revision grammar enforced; invalid rejected with exit 2
    @pytest.mark.parametrize(
        "valid",
        [
            "a", "0", "abc-def", "1.0.0", "release-0.12.1",
            "0.12.1-dev-a1b2c3d.1714000000",
            "abcdef0123456789",
            "a" * 64,
        ],
    )
    def test_accepts_valid_revisions(self, valid):
        CatalogDedupGuard.validate_revision(valid)  # no raise

    @pytest.mark.parametrize(
        "invalid",
        [
            "",  # empty
            "-foo",  # leading hyphen not allowed
            ".foo",  # leading dot not allowed
            "Foo",  # uppercase
            "foo bar",  # space
            "foo_bar",  # underscore
            "foo/bar",  # slash
            "a" * 65,  # too long
        ],
    )
    def test_rejects_invalid_revisions(self, invalid):
        with pytest.raises(ValueError, match="Invalid revision"):
            CatalogDedupGuard.validate_revision(invalid)


@pytest.mark.unit
class TestDedupCheck:
    # TS-22: duplicate (name, semver, revision) → CatalogDedupError → exit 2
    def test_rejects_duplicate_triple(self):
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0", revision="abc123")]
        with pytest.raises(CatalogDedupError) as exc_info:
            guard.check(existing, _entry("hello", "1.0.0", revision="abc123"))
        # Carries enough context to print an actionable error
        assert exc_info.value.extension_name == "hello"
        assert exc_info.value.version == "1.0.0"
        assert exc_info.value.revision == "abc123"
        # CatalogDedupError is a ValueError subclass — CLI maps to exit 2
        assert isinstance(exc_info.value, ValueError)
        # Sanity check: ExitCode.VALIDATION is 2
        assert int(ExitCode.VALIDATION) == 2

    # TS-23: --force overrides dedup with warning
    def test_force_bypasses_dedup_with_warning(self, capsys):
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0", revision="abc123")]
        # Should not raise
        guard.check(
            existing,
            _entry("hello", "1.0.0", revision="abc123"),
            force=True,
        )

    def test_inserts_when_revision_differs(self):
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0", revision="abc123")]
        guard.check(existing, _entry("hello", "1.0.0", revision="def456"))

    def test_inserts_when_version_differs(self):
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0", revision="abc123")]
        guard.check(existing, _entry("hello", "1.0.1", revision="abc123"))

    def test_inserts_when_name_differs(self):
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0", revision="abc123")]
        guard.check(existing, _entry("other", "1.0.0", revision="abc123"))

    def test_no_revision_skips_dedup(self):
        # Entries published without --revision (e.g. release publishes via
        # ENG-3591's flag-omitted path) are not subject to dedup.
        guard = CatalogDedupGuard()
        existing = [_entry("hello", "1.0.0")]
        guard.check(existing, _entry("hello", "1.0.0"))  # no raise

    def test_invalid_revision_raises_value_error_before_lookup(self):
        guard = CatalogDedupGuard()
        # Even with an empty existing list, a malformed revision fails early.
        with pytest.raises(ValueError, match="Invalid revision"):
            guard.check([], _entry("hello", "1.0.0", revision="BAD CASE"))


@pytest.mark.unit
class TestForwardCompatGardenApp:
    # TS-25: Older CLI reads newer catalog with revision field
    def test_garden_app_accepts_revision_field(self):
        from kamiwaza_sdk.schemas.apps import GardenApp

        # Newer catalog entry includes a `revision` field
        payload = {
            "name": "hello",
            "version": "1.0.0",
            "description": "test",
            "compose_yml": "services: {}",
            "revision": "abc123",
        }
        app = GardenApp(**payload)
        assert app.revision == "abc123"
        assert app.name == "hello"

    def test_garden_app_accepts_unknown_future_field(self):
        from kamiwaza_sdk.schemas.apps import GardenApp

        # An older client reading a future catalog should not crash on
        # fields it has never heard of.
        payload = {
            "name": "hello",
            "version": "1.0.0",
            "description": "test",
            "compose_yml": "services: {}",
            "future_field_we_have_not_invented_yet": {"nested": True},
        }
        # Should not raise
        GardenApp(**payload)


@pytest.mark.unit
class TestRegistryBuilderRevision:
    def test_build_entry_includes_revision_when_set(self):
        from kamiwaza_extensions.registry_builder import RegistryBuilder

        builder = RegistryBuilder()
        entry = builder.build_entry(
            metadata={"name": "hello", "description": "x"},
            transformed_compose={"services": {}},
            registry="example.io",
            version="1.0.0",
            stage="prod",
            revision="abc123",
        )
        assert entry["revision"] == "abc123"

    def test_build_entry_omits_revision_when_none(self):
        from kamiwaza_extensions.registry_builder import RegistryBuilder

        builder = RegistryBuilder()
        entry = builder.build_entry(
            metadata={"name": "hello", "description": "x"},
            transformed_compose={"services": {}},
            registry="example.io",
            version="1.0.0",
            stage="prod",
        )
        assert "revision" not in entry
