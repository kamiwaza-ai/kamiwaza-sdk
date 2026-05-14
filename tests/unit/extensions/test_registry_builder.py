"""Tests for RegistryBuilder."""

import copy
from typing import Any, Dict

import pytest
import yaml

from kamiwaza_extensions.compose_transformer import ComposeTransformer
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

    def test_extra_docker_images_emitted_on_their_own_field(
        self, builder, transformed_compose,
    ):
        # Extras live in their own catalog field; docker_images is
        # compose-derived only. The two lists never merge.
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": ["custom/sidecar:latest"],
        }
        entry = builder.build_entry(meta, transformed_compose, "reg", "1.0.0")
        assert entry["extra_docker_images"] == ["custom/sidecar:latest"]
        # docker_images stays compose-derived only.
        assert "custom/sidecar:latest" not in entry["docker_images"]

    def test_extra_docker_images_disjoint_from_docker_images(self, builder):
        # If the same ref appears in compose AND extras, each list retains
        # its own copy — no dedup across the two fields. They represent
        # different intents (runtime service vs. additional pull manifest)
        # and downstream consumers iterate them separately.
        compose = {"services": {"web": {"image": "custom/sidecar:latest"}}}
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": ["custom/sidecar:latest"],
        }
        entry = builder.build_entry(meta, compose, "reg", "1.0.0")
        assert entry["docker_images"] == ["custom/sidecar:latest"]
        assert entry["extra_docker_images"] == ["custom/sidecar:latest"]

    def test_no_extra_docker_images_field_when_metadata_omits_it(
        self, builder, metadata, transformed_compose,
    ):
        entry = builder.build_entry(metadata, transformed_compose, "reg", "1.0.0")
        assert "extra_docker_images" not in entry

    # `{version}` in extra_docker_images entries must be substituted
    # before reaching the catalog — same rewrite ComposeTransformer
    # applies to compose service images.
    def test_extra_docker_images_version_placeholder_substituted(
        self, builder, transformed_compose,
    ):
        meta = {
            "name": "kaizenv3",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": ["myreg/images/agent:{version}"],
        }
        entry = builder.build_entry(
            meta, transformed_compose, "myreg/images", "1.8.13", stage="prod",
        )
        # No raw `{version}` placeholder reaches the catalog.
        assert "myreg/images/agent:{version}" not in entry["extra_docker_images"]
        assert "myreg/images/agent:1.8.13" in entry["extra_docker_images"]
        # Extras stay out of docker_images.
        assert all(
            "agent" not in ref for ref in entry["docker_images"]
        ), entry["docker_images"]

    def test_extra_docker_images_stage_suffix_applied(
        self, builder, transformed_compose,
    ):
        meta = {
            "name": "kaizenv3",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": ["myreg/images/agent:{version}"],
        }
        for stage, expected_tag in [
            ("prod", "1.8.13"),
            ("dev", "1.8.13-dev"),
            ("stage", "1.8.13-stage"),
        ]:
            entry = builder.build_entry(
                meta, transformed_compose, "myreg/images", "1.8.13", stage=stage,
            )
            expected_ref = f"myreg/images/agent:{expected_tag}"
            assert expected_ref in entry["extra_docker_images"], (
                f"stage={stage}: expected {expected_ref} in extras, "
                f"got {entry['extra_docker_images']}"
            )
            # Extras don't leak into docker_images regardless of stage.
            assert expected_ref not in entry["docker_images"]

    def test_extra_docker_images_external_ref_not_suffixed(
        self, builder, transformed_compose,
    ):
        # Refs outside the configured registry namespace pass through
        # untouched — postgres, redis, third-party sidecars, etc.
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": [
                "ghcr.io/external/sidecar:2.0",
                "quay.io/org/util:{version}",
            ],
        }
        entry = builder.build_entry(
            meta, transformed_compose, "myreg/images", "1.8.13", stage="dev",
        )
        # External ref with no placeholder: untouched.
        assert "ghcr.io/external/sidecar:2.0" in entry["extra_docker_images"]
        # External ref with placeholder: substituted but NOT stage-suffixed.
        # External tags aren't ours to rewrite.
        assert "quay.io/org/util:1.8.13" in entry["extra_docker_images"]

    def test_extra_docker_images_fixed_tag_under_registry_passthrough(
        self, builder, transformed_compose,
    ):
        # A literal tag (no `{version}`) signals an independent release
        # cadence — a vendored helper at its own version. Re-suffixing it
        # would point at a tag this publish never built.
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": ["myreg/images/shared-helper:0.5.0"],
        }
        for stage in ["dev", "stage", "prod"]:
            entry = builder.build_entry(
                meta, transformed_compose, "myreg/images", "1.8.13", stage=stage,
            )
            assert entry["extra_docker_images"] == [
                "myreg/images/shared-helper:0.5.0"
            ], f"stage={stage}: fixed tag should pass through verbatim"

    def test_extra_docker_images_registry_with_port_untagged(
        self, builder, transformed_compose,
    ):
        # OCI refs allow `:` in the host segment (registry port). For an
        # untagged ref like `localhost:5000/org/agent`, the tag (if any)
        # lives after the last `/`, not at the first `:`. Splitting on the
        # leftmost colon would mangle the repository path.
        meta = {
            "name": "my-app",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": ["localhost:5000/org/agent:{version}"],
        }
        entry = builder.build_entry(
            meta, transformed_compose, "localhost:5000/org", "1.8.13",
            stage="dev",
        )
        assert entry["extra_docker_images"] == [
            "localhost:5000/org/agent:1.8.13-dev"
        ]

    def test_extra_docker_images_digest_pinned_ref_passthrough(
        self, builder, transformed_compose,
    ):
        # Already-digest-pinned refs are left exactly as-authored. Re-tagging
        # would mismatch the digest and break the immutable-identity contract.
        pinned = (
            "myreg/images/agent:1.8.13-dev"
            "@sha256:" + "a" * 64
        )
        meta = {
            "name": "kaizenv3",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": [pinned],
        }
        entry = builder.build_entry(
            meta, transformed_compose, "myreg/images", "1.8.13", stage="prod",
        )
        assert pinned in entry["extra_docker_images"]

    def test_extra_docker_images_digest_pinned_when_in_digest_map(
        self, builder, transformed_compose,
    ):
        # When the caller supplies a digest_map entry keyed by the
        # post-substitution ref, build_entry pins the extra and emits it
        # only via extra_docker_images.
        digest = "sha256:" + "b" * 64
        digest_map = {"myreg/images/agent:1.8.13-dev": digest}
        meta = {
            "name": "kaizenv3",
            "description": "test",
            "source_type": "kamiwaza",
            "visibility": "public",
            "extra_docker_images": ["myreg/images/agent:{version}"],
        }
        entry = builder.build_entry(
            meta, transformed_compose, "myreg/images", "1.8.13", stage="dev",
            digest_map=digest_map,
        )
        pinned_ref = f"myreg/images/agent:1.8.13-dev@{digest}"
        assert entry["extra_docker_images"] == [pinned_ref]
        assert pinned_ref not in entry["docker_images"]


# ------------------------------------------------------------------
# Env-var image-ref rewriting (ENG-5260)
# ------------------------------------------------------------------


def _kaizen_shaped_compose(env_value: str) -> Dict[str, Any]:
    """Build a compose dict with a single env-var image ref on the backend."""
    return {
        "services": {
            "backend": {
                "image": "myreg/images/kaizenv3-backend:1.8.13-dev",
                "environment": {
                    "AGENT_SERVER_IMAGE": env_value,
                },
            },
        },
    }


class TestBuildEntryEnvImageRewrites:
    """Image refs embedded in ``services[*].environment`` values get the
    same digest-pin treatment as ``extra_docker_images`` so a
    dynamic-spawn ref like ``${AGENT_SERVER_IMAGE:-...}`` stays in
    lockstep with the air-gap mirror list for the same image.

    Contract: env rewrites are gated on ``digest_map`` membership. A
    candidate ref that doesn't appear in the map is left verbatim — the
    publish never produced it, so re-stamping would point the runtime
    at a tag that was never built or mirrored. This mirrors the
    literal-tag-passthrough rule on the extras surface.
    """

    _AGENT_DIGEST = "sha256:" + "c" * 64
    _AGENT_DEV_MAP = {"myreg/images/agent:1.8.13-dev": _AGENT_DIGEST}

    def _meta(self, **overrides):
        base = {"name": "k", "description": "", "source_type": "kamiwaza",
                "visibility": "public"}
        base.update(overrides)
        return base

    def test_default_sub_rewritten_when_candidate_in_digest_map(self, builder):
        # The kaizen-shaped case: ${VAR:-<reg>/agent:1.8.13} published
        # to stage=dev with the post-suffix ref in digest_map emits
        # ${VAR:-<reg>/agent:1.8.13-dev@sha256:...}.
        compose = _kaizen_shaped_compose(
            "${AGENT_SERVER_IMAGE:-myreg/images/agent:1.8.13}"
        )
        entry = builder.build_entry(
            self._meta(name="kaizenv3"), compose, "myreg/images", "1.8.13",
            stage="dev", digest_map=self._AGENT_DEV_MAP,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        expected = (
            "${AGENT_SERVER_IMAGE:-"
            f"myreg/images/agent:1.8.13-dev@{self._AGENT_DIGEST}}}"
        )
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == expected

    def test_default_sub_dash_form_supported(self, builder):
        # ``${VAR-default}`` (use default only if unset) is rewritten
        # the same as ``${VAR:-default}``; both forms collapse to the
        # default at deploy time in our pipeline.
        compose = _kaizen_shaped_compose(
            "${AGENT_SERVER_IMAGE-myreg/images/agent:1.8.13}"
        )
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="dev", digest_map=self._AGENT_DEV_MAP,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        expected = (
            "${AGENT_SERVER_IMAGE-"
            f"myreg/images/agent:1.8.13-dev@{self._AGENT_DIGEST}}}"
        )
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == expected

    def test_bare_ref_rewritten_when_candidate_in_digest_map(self, builder):
        # Some authors set env defaults via plain values, not ${VAR:-...}.
        # Bare ref under the published surface gets pinned in place.
        compose = _kaizen_shaped_compose("myreg/images/agent:1.8.13")
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="dev", digest_map=self._AGENT_DEV_MAP,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == f"myreg/images/agent:1.8.13-dev@{self._AGENT_DIGEST}"

    def test_fixed_tag_literal_not_in_digest_map_passes_through(self, builder):
        # Codex #1: a vendored helper at its own version (literal tag,
        # not opt-in via {version}) won't have a post-suffix entry in
        # digest_map. The env ref must stay verbatim — re-stamping would
        # point at :0.5.0-dev which the publish never built.
        compose = _kaizen_shaped_compose(
            "${HELPER_IMAGE:-myreg/images/shared-helper:0.5.0}"
        )
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="dev", digest_map=self._AGENT_DEV_MAP,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == "${HELPER_IMAGE:-myreg/images/shared-helper:0.5.0}"

    def test_no_digest_map_no_rewrite(self, builder):
        # When the publish path didn't supply a digest_map (e.g. the
        # catalog-only-republish path soft-fell on resolution), env
        # refs pass through. Without a digest there's no proof the
        # post-suffix ref exists in the registry.
        compose = _kaizen_shaped_compose(
            "${AGENT_SERVER_IMAGE:-myreg/images/agent:1.8.13}"
        )
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13", stage="dev",
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == "${AGENT_SERVER_IMAGE:-myreg/images/agent:1.8.13}"

    def test_external_ref_not_rewritten(self, builder):
        # External refs (postgres, redis, third-party sidecars) aren't
        # in digest_map and pass through verbatim.
        compose = _kaizen_shaped_compose("${DB_IMAGE:-postgres:15}")
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="dev", digest_map=self._AGENT_DEV_MAP,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == "${DB_IMAGE:-postgres:15}"

    def test_non_image_default_passes_through(self, builder):
        # ${VAR:-info} (feature flag, log level, etc.) is shaped like a
        # default-sub but the default isn't an image ref.
        compose = _kaizen_shaped_compose("${LOG_LEVEL:-info}")
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="dev", digest_map=self._AGENT_DEV_MAP,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == "${LOG_LEVEL:-info}"

    def test_already_pinned_ref_passes_through(self, builder):
        # ${VAR:-<reg>/agent:1.8.13-dev@sha256:...} is already immutable;
        # leave the digest alone (re-suffixing would mismatch the pin).
        pinned_default = (
            "myreg/images/agent:1.8.13-dev@sha256:" + "a" * 64
        )
        compose = _kaizen_shaped_compose(
            f"${{AGENT_SERVER_IMAGE:-{pinned_default}}}"
        )
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="stage", digest_map=self._AGENT_DEV_MAP,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == f"${{AGENT_SERVER_IMAGE:-{pinned_default}}}"

    def test_prod_strips_stage_suffix(self, builder):
        # Source compose may already carry a -dev tag (re-publish
        # round-trip). For prod publish, candidate becomes :1.8.13
        # (no suffix) — if that's in digest_map, pin it.
        prod_map = {
            "myreg/images/agent:1.8.13":
                "sha256:" + "e" * 64,
        }
        compose = _kaizen_shaped_compose(
            "${AGENT_SERVER_IMAGE:-myreg/images/agent:1.8.13-dev}"
        )
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="prod", digest_map=prod_map,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        expected = (
            "${AGENT_SERVER_IMAGE:-"
            "myreg/images/agent:1.8.13@sha256:" + "e" * 64 + "}"
        )
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == expected

    def test_env_list_form_supported(self, builder):
        # Compose env can be a list of ``KEY=value`` strings instead of
        # a dict. Both shapes must round-trip the rewrite.
        compose = {
            "services": {
                "backend": {
                    "image": "myreg/images/k-backend:1.8.13-dev",
                    "environment": [
                        "AGENT_SERVER_IMAGE=${AGENT_SERVER_IMAGE:-myreg/images/agent:1.8.13}",
                        "LOG_LEVEL=info",
                    ],
                },
            },
        }
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="dev", digest_map=self._AGENT_DEV_MAP,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        env_list = compose_out["services"]["backend"]["environment"]
        expected = (
            "AGENT_SERVER_IMAGE=${AGENT_SERVER_IMAGE:-"
            f"myreg/images/agent:1.8.13-dev@{self._AGENT_DIGEST}}}"
        )
        assert expected in env_list
        assert "LOG_LEVEL=info" in env_list

    def test_env_long_form_dict_supported(self, builder):
        # ``[{"name": KEY, "value": VAL}, ...]`` — the long-form list
        # entry shape that Compose v3 also accepts.
        compose = {
            "services": {
                "backend": {
                    "image": "myreg/images/k-backend:1.8.13-dev",
                    "environment": [
                        {"name": "AGENT_SERVER_IMAGE",
                         "value": "${AGENT_SERVER_IMAGE:-myreg/images/agent:1.8.13}"},
                    ],
                },
            },
        }
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="dev", digest_map=self._AGENT_DEV_MAP,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        env_list = compose_out["services"]["backend"]["environment"]
        assert env_list[0]["value"] == (
            "${AGENT_SERVER_IMAGE:-"
            f"myreg/images/agent:1.8.13-dev@{self._AGENT_DIGEST}}}"
        )

    def test_non_string_env_values_untouched(self, builder):
        # Compose allows int/bool env values. Walking the dict must not
        # coerce or otherwise corrupt them.
        compose = {
            "services": {
                "backend": {
                    "image": "myreg/images/k-backend:1.8.13-dev",
                    "environment": {
                        "MAX_CONN": 100,
                        "ENABLE_FEATURE": True,
                    },
                },
            },
        }
        entry = builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="dev", digest_map=self._AGENT_DEV_MAP,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        env = compose_out["services"]["backend"]["environment"]
        assert env["MAX_CONN"] == 100
        assert env["ENABLE_FEATURE"] is True

    def test_registry_with_port_handled(self, builder):
        # OCI refs allow `:` in the host segment (registry port). The
        # tag-split must happen after the last `/`, not the leftmost `:`.
        port_map = {
            "localhost:5000/org/agent:1.8.13-dev": "sha256:" + "f" * 64,
        }
        compose = _kaizen_shaped_compose(
            "${AGENT_SERVER_IMAGE:-localhost:5000/org/agent:1.8.13}"
        )
        entry = builder.build_entry(
            self._meta(), compose, "localhost:5000/org", "1.8.13",
            stage="dev", digest_map=port_map,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        expected = (
            "${AGENT_SERVER_IMAGE:-localhost:5000/org/agent:1.8.13-dev"
            "@sha256:" + "f" * 64 + "}"
        )
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == expected

    def test_appgarden_divergent_namespace_works(self, builder):
        # Codex #3: when the appgarden compose's `image:` namespace
        # differs from the profile registry, digest_map is still keyed
        # by the actual published ref. Gating on digest_map (not
        # registry prefix) lets env refs under the appgarden namespace
        # rewrite correctly even though profile.registry says otherwise.
        appgarden_digest = "sha256:" + "9" * 64
        appgarden_map = {
            "ghcr.io/published/tool-foo/agent:2.0.0-dev": appgarden_digest,
        }
        compose = _kaizen_shaped_compose(
            "${AGENT_SERVER_IMAGE:-ghcr.io/published/tool-foo/agent:2.0.0}"
        )
        # The "registry" arg here represents profile.registry; the
        # appgarden compose has chosen a different namespace.
        entry = builder.build_entry(
            self._meta(), compose, "ghcr.io/some-other-org", "2.0.0",
            stage="dev", digest_map=appgarden_map,
        )
        compose_out = yaml.safe_load(entry["compose_yml"])
        expected = (
            "${AGENT_SERVER_IMAGE:-"
            f"ghcr.io/published/tool-foo/agent:2.0.0-dev@{appgarden_digest}}}"
        )
        assert compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ] == expected

    def test_caller_compose_not_mutated(self, builder):
        # Codex #5: build_entry must not mutate the caller's compose
        # dict. The deepcopy in _apply_env_image_rewrites guards this.
        original_env = "${AGENT_SERVER_IMAGE:-myreg/images/agent:1.8.13}"
        compose = _kaizen_shaped_compose(original_env)
        compose_snapshot = copy.deepcopy(compose)
        builder.build_entry(
            self._meta(), compose, "myreg/images", "1.8.13",
            stage="dev", digest_map=self._AGENT_DEV_MAP,
        )
        assert compose == compose_snapshot, (
            "build_entry mutated the caller's compose dict"
        )

    def test_env_value_matches_extra_docker_images_entry(self, builder):
        # The acceptance criterion: for kaizen-shaped input (env-var
        # default + extra_docker_images for the same image), the
        # published compose env default agrees with the extras list.
        digest = "sha256:" + "d" * 64
        digest_map = {"myreg/images/agent:1.8.13-dev": digest}
        compose = _kaizen_shaped_compose(
            "${AGENT_SERVER_IMAGE:-myreg/images/agent:1.8.13}"
        )
        meta = self._meta(
            name="kaizenv3",
            extra_docker_images=["myreg/images/agent:{version}"],
        )
        entry = builder.build_entry(
            meta, compose, "myreg/images", "1.8.13", stage="dev",
            digest_map=digest_map,
        )
        # extras list carries the canonical pinned ref.
        extras_ref = f"myreg/images/agent:1.8.13-dev@{digest}"
        assert entry["extra_docker_images"] == [extras_ref]
        # And the compose env default points at the same ref.
        compose_out = yaml.safe_load(entry["compose_yml"])
        env_default = compose_out["services"]["backend"]["environment"][
            "AGENT_SERVER_IMAGE"
        ]
        assert extras_ref in env_default


# ------------------------------------------------------------------
# Publish-path ordering: end-to-end coverage
# ------------------------------------------------------------------


class TestPublishPathOrdering:
    # build_entry's sort_keys=False only pins the final serialization step.
    # The full publish path runs the compose dict through ComposeTransformer
    # first, so any intermediate step that loses dict insertion order would
    # silently flip primary-service selection downstream — the same class
    # of bug as ENG-4920. These tests fence the full chain.

    @pytest.fixture
    def transformer(self):
        return ComposeTransformer()

    @pytest.fixture
    def graphiti_shaped_compose(self):
        # ENG-4920 trigger shape: database service first, app service second.
        # If anything in the chain re-sorts keys alphabetically, neo4j (n)
        # would land after graphiti (g), flipping which service the platform's
        # fallback heuristic picks as primary.
        return {
            "services": {
                "neo4j": {
                    "image": "ghcr.io/kamiwaza-internal/containers/images/neo4j:v5.26.21",
                    "ports": ["7687:7687"],
                },
                "graphiti": {
                    "build": {"context": ".", "dockerfile": "Dockerfile"},
                    "ports": ["8000:8000"],
                    "depends_on": ["neo4j"],
                },
            },
        }

    def test_full_publish_chain_preserves_service_order(
        self, builder, transformer, graphiti_shaped_compose
    ):
        transformed = transformer.transform(
            graphiti_shaped_compose,
            extension_name="service-graphiti",
            revision_tag="1.0.0",
            registry="kamiwazaai",
        )
        meta = {
            "name": "service-graphiti",
            "version": "1.0.0",
            "source_type": "kamiwaza",
            "visibility": "public",
        }
        entry = builder.build_entry(meta, transformed, "kamiwazaai", "1.0.0")

        parsed = yaml.safe_load(entry["compose_yml"])
        assert list(parsed["services"].keys()) == ["neo4j", "graphiti"]

    def test_compose_transformer_preserves_service_order(
        self, transformer, graphiti_shaped_compose
    ):
        # Targeted fence on the upstream half of the chain — guards against a
        # future ComposeTransformer refactor (dict comprehension, sorted(),
        # rebuild-from-items) that would lose insertion order before the
        # compose ever reaches build_entry's sort_keys=False.
        transformed = transformer.transform(
            graphiti_shaped_compose,
            extension_name="service-graphiti",
            revision_tag="1.0.0",
            registry="kamiwazaai",
        )
        assert list(transformed["services"].keys()) == ["neo4j", "graphiti"]


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
