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

    def test_compose_yml_preserves_service_order(self, builder, metadata):
        # Platform infers primary-service selection from compose order
        # when no service declares x-kamiwaza.primary. Alphabetizing keys on
        # serialization silently changes which service becomes primary.
        # Use a database-first ordering that would re-sort under sort_keys=True.
        compose = {
            "services": {
                "neo4j": {"image": "neo4j:5", "ports": ["7687"]},
                "graphiti": {
                    "image": "kamiwazaai/service-graphiti-graphiti:1.0.0",
                    "ports": ["8000"],
                },
            },
        }

        entry = builder.build_entry(metadata, compose, "kamiwazaai", "1.0.0")
        parsed = yaml.safe_load(entry["compose_yml"])
        assert list(parsed["services"].keys()) == ["neo4j", "graphiti"]

    def test_docker_images_extracted(self, builder, metadata, transformed_compose):
        entry = builder.build_entry(metadata, transformed_compose, "kamiwazaai", "1.0.0")
        assert "kamiwazaai/my-app-frontend:1.0.0" in entry["docker_images"]
        assert "kamiwazaai/my-app-backend:1.0.0" in entry["docker_images"]
        assert "postgres:15" in entry["docker_images"]

    def test_preserves_image_tags_from_transformed_compose(
        self, builder, metadata, transformed_compose,
    ):
        # build_entry trusts ComposeTransformer's output and does not
        # re-rewrite image tags. Whatever tags appear in transformed_compose
        # land verbatim in the catalog entry.
        entry = builder.build_entry(
            metadata, transformed_compose, "kamiwazaai", "1.0.0", stage="dev"
        )
        images = entry["docker_images"]
        # transformed_compose already carries `:1.0.0` for buildable services.
        assert "kamiwazaai/my-app-frontend:1.0.0" in images
        assert "kamiwazaai/my-app-backend:1.0.0" in images
        # External image unchanged regardless.
        assert "postgres:15" in images

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
# build_entry passes every top-level kamiwaza.json field through to
# the catalog entry. The platform's catalog→DB sync
# (kamiwaza/serving/garden/apps/templates.py::_update_template_from_remote)
# reads many fields via `.get(field, default)` and silently degrades
# to empty/None when entries omit them — no required_env_vars
# validation, no env_defaults injection, missing UI metadata. The
# regression these tests pin: don't curate the entry down to a
# known-fields subset.
# ------------------------------------------------------------------


class TestBuildEntryPassthrough:
    """Every top-level field in kamiwaza.json survives to the catalog entry."""

    def test_platform_consumed_fields_pass_through(
        self, builder, transformed_compose,
    ):
        # The fields the platform's _update_template_from_remote reads.
        meta = {
            "name": "my-app",
            "version": "1.0.0",
            "description": "An app",
            "source_type": "kamiwaza",
            "visibility": "public",
            "display_name": "My App",
            "env_defaults": {"FOO": "bar", "PORT": "8000"},
            "env_metadata": {"FOO": {"description": "foo var"}},
            "required_env_vars": ["API_KEY"],
            "capabilities": ["chat", "completion"],
            "category": "chatbot",
            "tags": ["ai"],
            "author": "Kamiwaza",
            "license": "Apache-2.0",
            "homepage": "https://example.com",
            "image": "kamiwazaai/my-app:1.0.0",
            "strip_path_prefix": True,
            "preferred_model_type": "reasoning",
            "preferred_model_name": "gpt-4",
            "fail_if_model_type_unavailable": False,
            "fail_if_model_name_unavailable": False,
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        for key, value in meta.items():
            assert entry[key] == value, f"{key} did not pass through"

    def test_unknown_fields_pass_through(self, builder, transformed_compose):
        # omniparse-style: `validation` block (e.g. allowed_images) and
        # `template_type` are not enumerated anywhere in build_entry but
        # the platform and the legacy publish path treat kamiwaza.json
        # as the contract. New fields must not require a kz-ext change.
        meta = {
            "name": "tool-omniparse",
            "version": "2.0.14",
            "description": "OmniParse",
            "source_type": "kamiwaza",
            "visibility": "public",
            "template_type": "tool",
            "validation": {"allowed_images": ["ghcr.io/foo/*"]},
            "x_custom_extension": {"any": "shape"},
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "2.0.14")
        assert entry["template_type"] == "tool"
        assert entry["validation"] == {"allowed_images": ["ghcr.io/foo/*"]}
        assert entry["x_custom_extension"] == {"any": "shape"}

    def test_entry_keys_are_superset_of_source(
        self, builder, transformed_compose,
    ):
        # Stricter check: legacy `make publish-registry` AC #1 says the
        # entry's top-level keys are a superset of source kamiwaza.json
        # plus the generated `compose_yml` / `docker_images`.
        meta = {
            "name": "svc",
            "version": "1.0.0",
            "description": "svc",
            "source_type": "kamiwaza",
            "visibility": "public",
            "template_type": "service",
            "env_defaults": {"X": "1"},
            "required_env_vars": ["Y"],
            "strip_path_prefix": False,
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        for key in meta:
            assert key in entry, f"source field {key} dropped from entry"

    def test_publish_generated_fields_win_over_metadata(
        self, builder, transformed_compose,
    ):
        # If the source kamiwaza.json carries a stale `compose_yml` or
        # `docker_images` from a hand-edit, the publish-time values
        # (from ComposeTransformer + extract_docker_images) must win.
        meta = {
            "name": "my-app",
            "version": "0.9.0-stale",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "compose_yml": "stale: data",
            "docker_images": ["stale:image"],
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        assert entry["version"] == "1.0.0"
        assert entry["compose_yml"] != "stale: data"
        assert "stale:image" not in entry["docker_images"]

    def test_metadata_not_mutated(self, builder, transformed_compose):
        # build_entry must not mutate the caller's metadata dict — it's
        # often the parsed kamiwaza.json shared with other code paths
        # (validators, dedup guard, etc.). deepcopy must protect nested
        # structures, not just top-level keys.
        meta = {
            "name": "my-app",
            "version": "1.0.0",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "env_defaults": {"FOO": "bar"},
            "preview_image": "./logo.png",
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        assert meta["preview_image"] == "./logo.png"  # not normalized
        # Mutating the entry's nested dict must not bleed into source.
        entry["env_defaults"]["FOO"] = "mutated"
        assert meta["env_defaults"]["FOO"] == "bar"

    def test_revision_not_inherited_from_metadata(
        self, builder, transformed_compose,
    ):
        # `revision` is publish-time only — owned by the --revision CLI
        # arg, never by source kamiwaza.json. Without explicit pop, a
        # stale revision in metadata (or a catalog entry round-tripped
        # through metadata) would survive deepcopy and trip
        # CatalogDedupGuard with a revision that wasn't used to tag the
        # images.
        meta = {
            "name": "my-app",
            "version": "1.0.0",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "revision": "stale-from-prior-publish",
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        assert "revision" not in entry

    def test_revision_param_overrides_metadata(
        self, builder, transformed_compose,
    ):
        meta = {
            "name": "my-app",
            "version": "1.0.0",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "revision": "stale",
        }
        entry = builder.build_entry(
            meta, transformed_compose, "reg", "1.0.0", revision="fresh",
        )
        assert entry["revision"] == "fresh"

    def test_defaults_applied_for_missing_required_fields(
        self, builder, transformed_compose,
    ):
        # Belt-and-suspenders: the upstream MetadataValidator should
        # catch these, but build_entry still papers over with the same
        # defaults the prior enumerated implementation used.
        meta = {"name": "my-app", "version": "1.0.0"}
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        assert entry["description"] == ""
        assert entry["source_type"] == "kamiwaza"
        assert entry["visibility"] == "public"


# ------------------------------------------------------------------
# build_entry treats ComposeTransformer as canonical (ENG-3591)
# ------------------------------------------------------------------


class TestBuildEntryRespectsComposeTransformerOutput:
    """build_entry no longer second-passes the compose YAML through a regex
    rewrite. Whatever tags ComposeTransformer left in the dict — including
    revision-overridden buildable-service tags AND verbatim Pattern C
    image refs (no build context) — flow through to the catalog entry
    unchanged."""

    def test_revision_pinned_buildable_tags_pass_through(self, builder):
        # Simulates ComposeTransformer's output for a publish run with
        # --revision 1.0.0-dev-abc1234.
        transformed = {
            "services": {
                "backend": {"image": "kamiwazaai/my-app-backend:1.0.0-dev-abc1234"},
                "frontend": {"image": "kamiwazaai/my-app-frontend:1.0.0-dev-abc1234"},
            },
        }
        entry = builder.build_entry(
            {"name": "my-app", "description": "x"},
            transformed,
            "kamiwazaai",
            "1.0.0",
            stage="dev",
        )
        images = entry["docker_images"]
        assert "kamiwazaai/my-app-backend:1.0.0-dev-abc1234" in images
        assert "kamiwazaai/my-app-frontend:1.0.0-dev-abc1234" in images
        # Round-trip through compose_yml as well.
        parsed = yaml.safe_load(entry["compose_yml"])
        assert (
            parsed["services"]["backend"]["image"]
            == "kamiwazaai/my-app-backend:1.0.0-dev-abc1234"
        )

    def test_pattern_c_image_passes_through_verbatim(self, builder):
        # An extension whose compose has BOTH a buildable service AND an
        # internal-named prebuilt service (Pattern C — image: but no build:).
        # Pre-ENG-3591, build_entry would have rewritten the helper's tag
        # to the stage-derived default (or the override), pointing the
        # catalog at a tag publish never pushed. After the fix, the helper's
        # declared tag is preserved as-is.
        transformed = {
            "services": {
                "backend": {
                    "image": "kamiwazaai/my-app-backend:1.0.0-dev-abc1234",
                },
                "helper": {
                    # No build context — publish doesn't own this image.
                    "image": "kamiwazaai/my-app-helper:0.5.0",
                },
            },
        }
        entry = builder.build_entry(
            {"name": "my-app", "description": "x"},
            transformed,
            "kamiwazaai",
            "1.0.0",
            stage="dev",
            revision="1.0.0-dev-abc1234",
        )
        images = entry["docker_images"]
        assert "kamiwazaai/my-app-helper:0.5.0" in images
        # The revision tag must NOT have leaked into the helper ref.
        assert "kamiwazaai/my-app-helper:1.0.0-dev-abc1234" not in images
        # Round-trip the published compose YAML to confirm what an installer
        # would actually see in the catalog entry.
        parsed = yaml.safe_load(entry["compose_yml"])
        assert (
            parsed["services"]["helper"]["image"]
            == "kamiwazaai/my-app-helper:0.5.0"
        )

    def test_external_images_pass_through_verbatim(self, builder):
        transformed = {
            "services": {
                "backend": {"image": "kamiwazaai/my-app-backend:1.0.0-dev"},
                "db": {"image": "postgres:15"},
                "cache": {"image": "redis:7"},
            },
        }
        entry = builder.build_entry(
            {"name": "my-app", "description": "x"},
            transformed,
            "kamiwazaai",
            "1.0.0",
            stage="dev",
        )
        images = entry["docker_images"]
        assert "postgres:15" in images
        assert "redis:7" in images
        # Round-trip the published compose YAML.
        parsed = yaml.safe_load(entry["compose_yml"])
        assert parsed["services"]["db"]["image"] == "postgres:15"
        assert parsed["services"]["cache"]["image"] == "redis:7"


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

    # ENG-4370: digest suffixes must survive tag rewriting.

    def test_preserves_digest_suffix_extension_branch(self, builder):
        digest = "sha256:" + "a" * 64
        yml = f"image: kamiwazaai/my-app-web:old@{digest}"
        result = builder.transform_image_tags(
            yml, "kamiwazaai", "2.0.0", "prod", extension_name="my-app",
        )
        assert f"kamiwazaai/my-app-web:2.0.0@{digest}" in result
        # Digest preserved, not stripped.
        assert "kamiwazaai/my-app-web:2.0.0\n" not in result + "\n"
        assert "kamiwazaai/my-app-web:2.0.0 " not in result + " "

    def test_preserves_digest_suffix_fallback_branch(self, builder):
        digest = "sha256:" + "b" * 64
        yml = f"image: kamiwazaai/my-app:old@{digest}"
        result = builder.transform_image_tags(yml, "kamiwazaai", "2.0.0", "stage")
        assert f"kamiwazaai/my-app:2.0.0-stage@{digest}" in result

    def test_digest_only_ref_extension_branch_unchanged(self, builder):
        # image@sha256:<64> (no tag) must NOT be rewritten. An earlier
        # regex captured `service@sha256` as the path and the hex as
        # the tag, producing `service@sha256:newtag` — corruption.
        digest = "sha256:" + "a" * 64
        yml = f"image: kamiwazaai/my-app-web@{digest}"
        result = builder.transform_image_tags(
            yml, "kamiwazaai", "2.0.0", "prod", extension_name="my-app",
        )
        # Verbatim — no rewrite.
        assert result == yml

    def test_digest_only_ref_fallback_branch_unchanged(self, builder):
        digest = "sha256:" + "b" * 64
        yml = f"image: kamiwazaai/my-app@{digest}"
        result = builder.transform_image_tags(yml, "kamiwazaai", "2.0.0", "stage")
        assert result == yml

    def test_digest_only_and_tag_only_siblings(self, builder):
        # Mixed compose: one ref pinned by digest only, one tagged.
        # The tagged ref is rewritten; the digest-only ref passes through.
        digest = "sha256:" + "c" * 64
        yml = (
            f"image: kamiwazaai/app-frontend@{digest}\n"
            "image: kamiwazaai/app-backend:old\n"
        )
        result = builder.transform_image_tags(yml, "kamiwazaai", "3.0.0", "prod")
        assert f"kamiwazaai/app-frontend@{digest}" in result
        assert "kamiwazaai/app-backend:3.0.0" in result
        # Frontend was not rewritten with a tag.
        frontend_line = [
            ln for ln in result.splitlines() if "app-frontend" in ln
        ][0]
        assert ":3.0.0" not in frontend_line

    def test_malformed_digest_suffix_passes_through_untouched(self, builder):
        # An `@sha256:short` (or any non-conforming suffix) doesn't
        # match the optional digest group, so the regex matches up to
        # the bad `@` and the broken tail passes through verbatim.
        # Preserves rather than corrupts user input.
        yml = "image: kamiwazaai/my-app-web:old@sha256:short"
        result = builder.transform_image_tags(
            yml, "kamiwazaai", "2.0.0", "prod", extension_name="my-app",
        )
        assert "kamiwazaai/my-app-web:2.0.0@sha256:short" in result

    def test_preserves_digest_on_some_siblings_only(self, builder):
        digest = "sha256:" + "c" * 64
        yml = (
            f"image: kamiwazaai/app-frontend:old@{digest}\n"
            "image: kamiwazaai/app-backend:old\n"
        )
        result = builder.transform_image_tags(yml, "kamiwazaai", "3.0.0", "prod")
        assert f"kamiwazaai/app-frontend:3.0.0@{digest}" in result
        assert "kamiwazaai/app-backend:3.0.0" in result
        # Backend has no `@` after rewrite.
        backend_line = [
            ln for ln in result.splitlines() if "app-backend" in ln
        ][0]
        assert "@" not in backend_line


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


# ------------------------------------------------------------------
# ENG-4370 — digest pinning via build_entry(digest_map=...)
# ------------------------------------------------------------------


_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64


class TestBuildEntryDigestPinning:
    def test_pinning_rewrites_compose_yml_and_docker_images(
        self, builder, metadata, transformed_compose,
    ):
        digest_map = {
            "kamiwazaai/my-app-frontend:1.0.0": _DIGEST_A,
            "kamiwazaai/my-app-backend:1.0.0": _DIGEST_B,
        }
        entry = builder.build_entry(
            metadata, transformed_compose, "kamiwazaai", "1.0.0",
            digest_map=digest_map,
        )

        # docker_images carries `tag@digest` for buildable refs.
        assert f"kamiwazaai/my-app-frontend:1.0.0@{_DIGEST_A}" in entry["docker_images"]
        assert f"kamiwazaai/my-app-backend:1.0.0@{_DIGEST_B}" in entry["docker_images"]
        # Pre-existing tag-only refs no longer present.
        assert "kamiwazaai/my-app-frontend:1.0.0" not in entry["docker_images"]
        assert "kamiwazaai/my-app-backend:1.0.0" not in entry["docker_images"]
        # Pass-through external image left verbatim (Pattern B).
        assert "postgres:15" in entry["docker_images"]

        parsed = yaml.safe_load(entry["compose_yml"])
        assert (
            parsed["services"]["frontend"]["image"]
            == f"kamiwazaai/my-app-frontend:1.0.0@{_DIGEST_A}"
        )
        assert (
            parsed["services"]["backend"]["image"]
            == f"kamiwazaai/my-app-backend:1.0.0@{_DIGEST_B}"
        )
        assert parsed["services"]["db"]["image"] == "postgres:15"

    def test_omitted_digest_map_leaves_refs_unchanged(
        self, builder, metadata, transformed_compose,
    ):
        entry = builder.build_entry(
            metadata, transformed_compose, "kamiwazaai", "1.0.0",
        )
        assert "kamiwazaai/my-app-frontend:1.0.0" in entry["docker_images"]
        assert all("@" not in img for img in entry["docker_images"])

    def test_empty_digest_map_leaves_refs_unchanged(
        self, builder, metadata, transformed_compose,
    ):
        entry = builder.build_entry(
            metadata, transformed_compose, "kamiwazaai", "1.0.0",
            digest_map={},
        )
        assert "kamiwazaai/my-app-frontend:1.0.0" in entry["docker_images"]
        assert all("@" not in img for img in entry["docker_images"])

    def test_partial_digest_map_only_pins_listed_refs(
        self, builder, metadata, transformed_compose,
    ):
        # Only one of the two buildable refs in the map. The other is
        # left as tag-only — verifies the map is the source of truth.
        digest_map = {"kamiwazaai/my-app-frontend:1.0.0": _DIGEST_A}
        entry = builder.build_entry(
            metadata, transformed_compose, "kamiwazaai", "1.0.0",
            digest_map=digest_map,
        )
        assert f"kamiwazaai/my-app-frontend:1.0.0@{_DIGEST_A}" in entry["docker_images"]
        assert "kamiwazaai/my-app-backend:1.0.0" in entry["docker_images"]

    def test_already_pinned_ref_not_double_pinned(self, builder, metadata):
        compose = {
            "services": {
                "backend": {
                    "image": f"kamiwazaai/my-app-backend:1.0.0@{_DIGEST_A}"
                },
            },
        }
        # Map keyed by the original (unpinned) ref. Because the compose
        # value already contains '@', _apply_digests must skip it.
        digest_map = {"kamiwazaai/my-app-backend:1.0.0": _DIGEST_B}
        entry = builder.build_entry(
            metadata, compose, "kamiwazaai", "1.0.0", digest_map=digest_map,
        )
        # Original digest preserved; no double `@`.
        assert f"kamiwazaai/my-app-backend:1.0.0@{_DIGEST_A}" in entry["docker_images"]
        assert _DIGEST_B not in entry["compose_yml"]

    def test_does_not_mutate_caller_compose(self, builder, metadata):
        compose = {
            "services": {
                "backend": {"image": "kamiwazaai/my-app-backend:1.0.0"},
            },
        }
        digest_map = {"kamiwazaai/my-app-backend:1.0.0": _DIGEST_A}
        builder.build_entry(metadata, compose, "kamiwazaai", "1.0.0", digest_map=digest_map)
        assert (
            compose["services"]["backend"]["image"]
            == "kamiwazaai/my-app-backend:1.0.0"
        )

    def test_extra_docker_images_collision_pinned_and_deduped(self, builder):
        # When extra_docker_images repeats a buildable service ref,
        # both copies must end up pinned identically so dedup collapses
        # them. Otherwise the catalog leaks an unpinned duplicate.
        compose = {
            "services": {
                "backend": {"image": "kamiwazaai/my-app-backend:1.0.0"},
            },
        }
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            # Redundantly listed (same as the service image).
            "extra_docker_images": ["kamiwazaai/my-app-backend:1.0.0"],
        }
        digest_map = {"kamiwazaai/my-app-backend:1.0.0": _DIGEST_A}
        entry = builder.build_entry(
            meta, compose, "kamiwazaai", "1.0.0", digest_map=digest_map,
        )
        # Single pinned entry — no unpinned duplicate.
        assert entry["docker_images"] == [
            f"kamiwazaai/my-app-backend:1.0.0@{_DIGEST_A}"
        ]

    def test_revision_and_digest_are_orthogonal(self, builder, metadata):
        # Catalog ref carries both: <reg>/<ext>-<svc>:<revision>@<digest>.
        compose = {
            "services": {
                "backend": {
                    "image": "kamiwazaai/my-app-backend:abc1234",  # revision tag
                },
            },
        }
        digest_map = {"kamiwazaai/my-app-backend:abc1234": _DIGEST_A}
        entry = builder.build_entry(
            metadata, compose, "kamiwazaai", "1.0.0",
            revision="abc1234",
            digest_map=digest_map,
        )
        assert entry["revision"] == "abc1234"
        assert (
            f"kamiwazaai/my-app-backend:abc1234@{_DIGEST_A}"
            in entry["docker_images"]
        )
