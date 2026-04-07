"""Tests for RegistryBuilder."""

import pytest
import yaml

from kamiwaza_extensions.registry_builder import (
    RegistryBuilder,
    _constraint_relationship,
    _normalize_preview_image,
)
from packaging.specifiers import SpecifierSet


@pytest.fixture
def builder():
    return RegistryBuilder()


@pytest.fixture
def metadata():
    return {
        "name": "my-app",
        "version": "1.0.0",
        "description": "A test application",
        "source_type": "kamiwaza",
        "visibility": "public",
    }


@pytest.fixture
def transformed_compose():
    return {
        "services": {
            "frontend": {
                "image": "kamiwazaai/my-app-frontend:1.0.0",
                "ports": ["3000"],
            },
            "backend": {
                "image": "kamiwazaai/my-app-backend:1.0.0",
                "ports": ["8000"],
            },
            "db": {
                "image": "postgres:15",
            },
        },
    }


# ------------------------------------------------------------------
# build_entry
# ------------------------------------------------------------------


class TestBuildEntry:
    def test_required_fields_present(self, builder, metadata, transformed_compose):
        entry = builder.build_entry(metadata, transformed_compose, "kamiwazaai", "1.0.0")
        assert entry["name"] == "my-app"
        assert entry["version"] == "1.0.0"
        assert entry["description"] == "A test application"
        assert entry["source_type"] == "kamiwaza"
        assert entry["visibility"] == "public"

    def test_compose_yml_is_string(self, builder, metadata, transformed_compose):
        entry = builder.build_entry(metadata, transformed_compose, "kamiwazaai", "1.0.0")
        assert isinstance(entry["compose_yml"], str)
        # Should be valid YAML
        parsed = yaml.safe_load(entry["compose_yml"])
        assert "services" in parsed

    def test_docker_images_extracted(self, builder, metadata, transformed_compose):
        entry = builder.build_entry(metadata, transformed_compose, "kamiwazaai", "1.0.0")
        assert "kamiwazaai/my-app-frontend:1.0.0" in entry["docker_images"]
        assert "kamiwazaai/my-app-backend:1.0.0" in entry["docker_images"]
        assert "postgres:15" in entry["docker_images"]

    def test_stage_transforms_tags(self, builder, metadata, transformed_compose):
        entry = builder.build_entry(
            metadata, transformed_compose, "kamiwazaai", "1.0.0", stage="stage"
        )
        images = entry["docker_images"]
        assert "kamiwazaai/my-app-frontend:1.0.0-stage" in images
        assert "kamiwazaai/my-app-backend:1.0.0-stage" in images
        # External image unchanged
        assert "postgres:15" in images

    def test_dev_stage(self, builder, metadata, transformed_compose):
        entry = builder.build_entry(
            metadata, transformed_compose, "kamiwazaai", "1.0.0", stage="dev"
        )
        images = entry["docker_images"]
        assert "kamiwazaai/my-app-frontend:1.0.0-dev" in images
        assert "kamiwazaai/my-app-backend:1.0.0-dev" in images

    def test_prod_stage_no_suffix(self, builder, metadata, transformed_compose):
        entry = builder.build_entry(
            metadata, transformed_compose, "kamiwazaai", "1.0.0", stage="prod"
        )
        images = entry["docker_images"]
        assert "kamiwazaai/my-app-frontend:1.0.0" in images
        assert "kamiwazaai/my-app-backend:1.0.0" in images

    def test_kamiwaza_version_included_when_present(
        self, builder, transformed_compose
    ):
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "kamiwaza_version": ">=0.8.0",
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        assert entry["kamiwaza_version"] == ">=0.8.0"

    def test_kamiwaza_version_omitted_when_absent(
        self, builder, metadata, transformed_compose
    ):
        entry = builder.build_entry(metadata, transformed_compose, "reg", "1.0.0")
        assert "kamiwaza_version" not in entry

    def test_preview_image_normalized(self, builder, transformed_compose):
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "preview_image": "screenshot.png",
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        assert entry["preview_image"] == "images/screenshot.png"

    def test_preview_image_already_prefixed(self, builder, transformed_compose):
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "preview_image": "images/screenshot.png",
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        assert entry["preview_image"] == "images/screenshot.png"

    def test_optional_fields_included(self, builder, transformed_compose):
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "risk_tier": 2,
            "tags": ["ai", "chat"],
            "category": "chatbot",
            "verified": True,
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        assert entry["risk_tier"] == 2
        assert entry["tags"] == ["ai", "chat"]
        assert entry["category"] == "chatbot"
        assert entry["verified"] is True

    def test_extra_docker_images_appended(self, builder, transformed_compose):
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": ["custom/sidecar:latest"],
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        assert "custom/sidecar:latest" in entry["docker_images"]

    def test_extra_docker_images_deduped(self, builder):
        compose = {"services": {"web": {"image": "custom/sidecar:latest"}}}
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": ["custom/sidecar:latest"],
        }
        entry = builder.build_entry(meta, compose, "reg", "1.0.0")
        assert entry["docker_images"].count("custom/sidecar:latest") == 1


# ------------------------------------------------------------------
# transform_image_tags
# ------------------------------------------------------------------


class TestTransformImageTags:
    def test_prod_clean_tag(self, builder):
        yml = "image: kamiwazaai/my-app-web:old-tag"
        result = builder.transform_image_tags(yml, "kamiwazaai", "2.0.0", "prod")
        assert "kamiwazaai/my-app-web:2.0.0" in result

    def test_stage_suffix(self, builder):
        yml = "image: kamiwazaai/my-app-web:old-tag"
        result = builder.transform_image_tags(yml, "kamiwazaai", "2.0.0", "stage")
        assert "kamiwazaai/my-app-web:2.0.0-stage" in result

    def test_dev_suffix(self, builder):
        yml = "image: kamiwazaai/my-app-web:old-tag"
        result = builder.transform_image_tags(yml, "kamiwazaai", "2.0.0", "dev")
        assert "kamiwazaai/my-app-web:2.0.0-dev" in result

    def test_external_image_unchanged(self, builder):
        yml = "image: postgres:15\n    image: kamiwazaai/app:old"
        result = builder.transform_image_tags(yml, "kamiwazaai", "1.0.0", "stage")
        assert "postgres:15" in result
        assert "kamiwazaai/app:1.0.0-stage" in result

    def test_multiple_images_transformed(self, builder):
        yml = (
            "image: kamiwazaai/app-frontend:old\n"
            "image: kamiwazaai/app-backend:old\n"
        )
        result = builder.transform_image_tags(yml, "kamiwazaai", "3.0.0", "prod")
        assert "kamiwazaai/app-frontend:3.0.0" in result
        assert "kamiwazaai/app-backend:3.0.0" in result

    def test_registry_with_dots(self, builder):
        yml = "image: registry.example.com/app:old"
        result = builder.transform_image_tags(
            yml, "registry.example.com", "1.0.0", "prod"
        )
        assert "registry.example.com/app:1.0.0" in result

    def test_custom_stage_name(self, builder):
        """Custom stage names (staging, qa, etc.) should produce -{stage} suffix."""
        yml = "image: kamiwazaai/my-app:old"
        result = builder.transform_image_tags(yml, "kamiwazaai", "1.0.0", "staging")
        assert "kamiwazaai/my-app:1.0.0-staging" in result

    def test_custom_stage_qa(self, builder):
        yml = "image: kamiwazaai/my-app:old"
        result = builder.transform_image_tags(yml, "kamiwazaai", "2.0.0", "qa")
        assert "kamiwazaai/my-app:2.0.0-qa" in result


# ------------------------------------------------------------------
# extract_docker_images
# ------------------------------------------------------------------


class TestExtractDockerImages:
    def test_extracts_images_from_dict(self, builder):
        compose = {
            "services": {
                "web": {"image": "kamiwazaai/web:1.0"},
                "db": {"image": "postgres:15"},
            }
        }
        images = builder.extract_docker_images(compose)
        assert images == ["kamiwazaai/web:1.0", "postgres:15"]

    def test_deduplicates_from_dict(self, builder):
        # Two services with same image
        compose = {
            "services": {
                "web1": {"image": "kamiwazaai/web:1.0"},
                "web2": {"image": "kamiwazaai/web:1.0"},
            }
        }
        images = builder.extract_docker_images(compose)
        assert images == ["kamiwazaai/web:1.0"]

    def test_empty_when_no_images(self, builder):
        compose = {"services": {"web": {"build": "."}}}
        assert builder.extract_docker_images(compose) == []

    def test_ignores_env_var_with_image_in_name(self, builder):
        """Env vars like DOCKER_IMAGE should not be extracted."""
        compose = {
            "services": {
                "web": {
                    "image": "kamiwazaai/web:1.0",
                    "environment": {"DOCKER_IMAGE": "foo:bar"},
                }
            }
        }
        images = builder.extract_docker_images(compose)
        assert images == ["kamiwazaai/web:1.0"]

    def test_string_fallback(self, builder):
        """String input still works via regex fallback."""
        yml = "services:\n  web:\n    image: kamiwazaai/web:1.0\n"
        images = builder.extract_docker_images(yml)
        assert images == ["kamiwazaai/web:1.0"]


# ------------------------------------------------------------------
# merge_into_registry -- v1 (no constraints)
# ------------------------------------------------------------------


class TestMergeV1:
    def test_insert_into_empty(self, builder):
        entry = {"name": "app", "version": "1.0.0"}
        result, action = builder.merge_into_registry(entry, [])
        assert action == "insert"
        assert len(result) == 1
        assert result[0] == entry

    def test_insert_new_name(self, builder):
        existing = [{"name": "other", "version": "1.0.0"}]
        entry = {"name": "app", "version": "1.0.0"}
        result, action = builder.merge_into_registry(entry, existing)
        assert action == "insert"
        assert len(result) == 2

    def test_replace_newer_version(self, builder):
        existing = [{"name": "app", "version": "1.0.0"}]
        entry = {"name": "app", "version": "2.0.0"}
        result, action = builder.merge_into_registry(entry, existing)
        assert action == "replace"
        assert len(result) == 1
        assert result[0]["version"] == "2.0.0"

    def test_reject_same_version(self, builder):
        existing = [{"name": "app", "version": "1.0.0"}]
        entry = {"name": "app", "version": "1.0.0"}
        with pytest.raises(ValueError, match="already exists"):
            builder.merge_into_registry(entry, existing)

    def test_force_same_version(self, builder):
        existing = [{"name": "app", "version": "1.0.0"}]
        entry = {"name": "app", "version": "1.0.0", "description": "updated"}
        result, action = builder.merge_into_registry(entry, existing, force=True)
        assert action == "replace"
        assert result[0]["description"] == "updated"

    def test_reject_older_version(self, builder):
        existing = [{"name": "app", "version": "2.0.0"}]
        entry = {"name": "app", "version": "1.0.0"}
        with pytest.raises(ValueError, match="newer version"):
            builder.merge_into_registry(entry, existing)

    def test_replace_removes_all_duplicates(self, builder):
        """v1 merge with multiple same-name entries removes all of them."""
        existing = [
            {"name": "app", "version": "1.0.0"},
            {"name": "app", "version": "1.0.0"},  # accidental duplicate
            {"name": "other", "version": "1.0.0"},
        ]
        entry = {"name": "app", "version": "2.0.0"}
        result, action = builder.merge_into_registry(entry, existing)
        assert action == "replace"
        # Both old "app" entries removed; one new "app" + "other" remain.
        assert len(result) == 2
        app_entries = [e for e in result if e["name"] == "app"]
        assert len(app_entries) == 1
        assert app_entries[0]["version"] == "2.0.0"

    def test_does_not_mutate_existing(self, builder):
        existing = [{"name": "app", "version": "1.0.0"}]
        original = list(existing)
        entry = {"name": "app", "version": "2.0.0"}
        builder.merge_into_registry(entry, existing)
        # Original list unchanged
        assert existing == original


# ------------------------------------------------------------------
# merge_into_registry -- v2 (constraint-aware)
# ------------------------------------------------------------------


class TestMergeV2:
    def test_insert_disjoint_constraints(self, builder):
        existing = [
            {"name": "app", "version": "1.0.0", "kamiwaza_version": ">=0.7.0,<0.8.0"},
        ]
        entry = {
            "name": "app",
            "version": "2.0.0",
            "kamiwaza_version": ">=0.8.0",
        }
        result, action = builder.merge_into_registry(entry, existing)
        assert action == "insert"
        assert len(result) == 2

    def test_replace_same_constraint_newer_version(self, builder):
        existing = [
            {"name": "app", "version": "1.0.0", "kamiwaza_version": ">=0.8.0"},
        ]
        entry = {
            "name": "app",
            "version": "2.0.0",
            "kamiwaza_version": ">=0.8.0",
        }
        result, action = builder.merge_into_registry(entry, existing)
        assert action == "replace"
        assert len(result) == 1
        assert result[0]["version"] == "2.0.0"

    def test_reject_same_constraint_same_version(self, builder):
        existing = [
            {"name": "app", "version": "1.0.0", "kamiwaza_version": ">=0.8.0"},
        ]
        entry = {
            "name": "app",
            "version": "1.0.0",
            "kamiwaza_version": ">=0.8.0",
        }
        with pytest.raises(ValueError, match="already exists"):
            builder.merge_into_registry(entry, existing)

    def test_force_same_constraint_same_version(self, builder):
        existing = [
            {"name": "app", "version": "1.0.0", "kamiwaza_version": ">=0.8.0"},
        ]
        entry = {
            "name": "app",
            "version": "1.0.0",
            "kamiwaza_version": ">=0.8.0",
            "description": "updated",
        }
        result, action = builder.merge_into_registry(entry, existing, force=True)
        assert action == "replace"
        assert result[0]["description"] == "updated"

    def test_reject_overlapping_constraints(self, builder):
        existing = [
            {"name": "app", "version": "1.0.0", "kamiwaza_version": ">=0.7.0"},
        ]
        entry = {
            "name": "app",
            "version": "2.0.0",
            "kamiwaza_version": ">=0.8.0,<1.0.0",
        }
        # >=0.8.0,<1.0.0 is a subset of >=0.7.0 -- should reject
        with pytest.raises(ValueError, match="overlaps"):
            builder.merge_into_registry(entry, existing)

    def test_superset_constraint_replaces(self, builder):
        existing = [
            {"name": "app", "version": "1.0.0", "kamiwaza_version": ">=0.8.0,<1.0.0"},
        ]
        entry = {
            "name": "app",
            "version": "2.0.0",
            "kamiwaza_version": ">=0.7.0",
        }
        result, action = builder.merge_into_registry(entry, existing)
        assert action == "replace"
        assert result[0]["version"] == "2.0.0"

    def test_entry_with_constraint_existing_without(self, builder):
        existing = [
            {"name": "app", "version": "1.0.0"},
        ]
        entry = {
            "name": "app",
            "version": "2.0.0",
            "kamiwaza_version": ">=0.8.0",
        }
        result, action = builder.merge_into_registry(entry, existing)
        assert action == "replace"
        assert result[0]["version"] == "2.0.0"

    def test_multiple_disjoint_existing(self, builder):
        existing = [
            {"name": "app", "version": "1.0.0", "kamiwaza_version": ">=0.7.0,<0.8.0"},
            {"name": "app", "version": "1.5.0", "kamiwaza_version": ">=0.8.0,<0.9.0"},
        ]
        entry = {
            "name": "app",
            "version": "2.0.0",
            "kamiwaza_version": ">=0.9.0",
        }
        result, action = builder.merge_into_registry(entry, existing)
        assert action == "insert"
        assert len(result) == 3

    def test_superset_replaces_multiple_existing(self, builder):
        """A wider constraint that is a superset of two existing entries
        should replace both of them."""
        existing = [
            {"name": "app", "version": "1.0.0", "kamiwaza_version": ">=0.8.0,<0.9.0"},
            {"name": "app", "version": "1.5.0", "kamiwaza_version": ">=0.9.0,<1.0.0"},
            {"name": "other", "version": "1.0.0"},  # unrelated entry
        ]
        entry = {
            "name": "app",
            "version": "2.0.0",
            "kamiwaza_version": ">=0.7.0",
        }
        result, action = builder.merge_into_registry(entry, existing)
        assert action == "replace"
        # Both app entries replaced by the single new entry; "other" remains.
        assert len(result) == 2
        app_entries = [e for e in result if e["name"] == "app"]
        assert len(app_entries) == 1
        assert app_entries[0]["version"] == "2.0.0"
        assert app_entries[0]["kamiwaza_version"] == ">=0.7.0"
        # Unrelated entry preserved.
        assert any(e["name"] == "other" for e in result)


# ------------------------------------------------------------------
# _constraint_relationship
# ------------------------------------------------------------------


class TestConstraintRelationship:
    def test_equal(self):
        a = SpecifierSet(">=0.8.0")
        b = SpecifierSet(">=0.8.0")
        assert _constraint_relationship(a, b) == "equal"

    def test_disjoint(self):
        a = SpecifierSet(">=1.0.0")
        b = SpecifierSet("<1.0.0")
        assert _constraint_relationship(a, b) == "disjoint"

    def test_superset(self):
        a = SpecifierSet(">=0.7.0")
        b = SpecifierSet(">=0.8.0,<1.0.0")
        assert _constraint_relationship(a, b) == "superset"

    def test_subset(self):
        a = SpecifierSet(">=0.8.0,<1.0.0")
        b = SpecifierSet(">=0.7.0")
        assert _constraint_relationship(a, b) == "subset"

    def test_overlap(self):
        a = SpecifierSet(">=0.5.0,<1.0.0")
        b = SpecifierSet(">=0.8.0,<2.0.0")
        assert _constraint_relationship(a, b) == "overlap"

    def test_disjoint_adjacent_ranges(self):
        a = SpecifierSet(">=0.7.0,<0.8.0")
        b = SpecifierSet(">=0.8.0,<0.9.0")
        assert _constraint_relationship(a, b) == "disjoint"


# ------------------------------------------------------------------
# _normalize_preview_image
# ------------------------------------------------------------------


class TestNormalizePreviewImage:
    def test_bare_filename(self):
        assert _normalize_preview_image("screenshot.png") == "images/screenshot.png"

    def test_already_prefixed(self):
        assert _normalize_preview_image("images/screenshot.png") == "images/screenshot.png"

    def test_dot_slash_prefix(self):
        assert _normalize_preview_image("./screenshot.png") == "images/screenshot.png"

    def test_dot_slash_images_prefix(self):
        assert _normalize_preview_image("./images/screenshot.png") == "images/screenshot.png"

    def test_leading_slash(self):
        assert _normalize_preview_image("/screenshot.png") == "images/screenshot.png"
