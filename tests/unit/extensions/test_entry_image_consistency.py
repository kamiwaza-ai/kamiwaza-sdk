"""Catalog-entry image-coverage validation (ENG-8270).

The offline bundler relocates exactly the images a catalog entry lists in
``docker_images`` + ``extra_docker_images``. An image ref that only appears
inside a compose *environment value* (e.g. Kaizen's dynamic-spawn
``${AGENT_SERVER_IMAGE:-...}`` default) but is missing from those lists is
unpullable on air-gapped installs: the sandbox pod ImagePullBackOffs and the
extension breaks at runtime. ENG-8270 shipped exactly that shape to the prod
catalog — services retagged to ``release-1.0.0`` while the env default kept
the extension-version tag ``2.0.2``.

``find_uncovered_env_image_refs`` is the publish-time guard that makes such
an entry unpublishable.
"""

import pytest

from kamiwaza_extensions.registry_builder import find_uncovered_env_image_refs

pytestmark = pytest.mark.unit

KAIZEN_REGISTRY = "ghcr.io/kamiwaza-internal/kamiwaza-extensions-kaizen/images"


def _entry(compose_yml: str, docker_images, extra_docker_images=None):
    entry = {
        "name": "kaizen",
        "version": "2.0.2",
        "compose_yml": compose_yml,
        "docker_images": docker_images,
    }
    if extra_docker_images is not None:
        entry["extra_docker_images"] = extra_docker_images
    return entry


class TestUncoveredEnvImageRefs:
    def test_stale_env_default_is_flagged(self):
        """Regression: the exact prod-catalog kaizen shape from ENG-8270.

        Services retagged to release-1.0.0 (and listed), but the
        AGENT_SERVER_IMAGE env default still at the extension-version tag
        2.0.2 — a ref no list covers, so no offline bundle relocates it.
        """
        compose = f"""
services:
  backend:
    image: {KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0
    environment:
      AGENT_SERVER_IMAGE: ${{AGENT_SERVER_IMAGE:-{KAIZEN_REGISTRY}/kaizen-agent:2.0.2}}
  agent:
    image: {KAIZEN_REGISTRY}/kaizen-agent:release-1.0.0
"""
        entry = _entry(
            compose,
            docker_images=[
                f"{KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0",
                f"{KAIZEN_REGISTRY}/kaizen-agent:release-1.0.0",
            ],
            extra_docker_images=[f"{KAIZEN_REGISTRY}/kaizen-agent:release-1.0.0"],
        )
        violations = find_uncovered_env_image_refs(entry, KAIZEN_REGISTRY)
        assert len(violations) == 1
        assert f"{KAIZEN_REGISTRY}/kaizen-agent:2.0.2" in violations[0]
        assert "AGENT_SERVER_IMAGE" in violations[0]

    def test_consistent_entry_passes(self):
        """The correct (stage-catalog) shape: env default restamped to the
        same tag the lists carry, digest-pinned. Digests may differ between
        surfaces (relocation re-digests); coverage compares name:tag."""
        digest = "sha256:" + "f" * 64
        compose = f"""
services:
  backend:
    image: {KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0@{digest}
    environment:
      AGENT_SERVER_IMAGE: ${{AGENT_SERVER_IMAGE:-{KAIZEN_REGISTRY}/kaizen-agent:release-1.0.0@{digest}}}
"""
        entry = _entry(
            compose,
            docker_images=[f"{KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0@{digest}"],
            extra_docker_images=[
                f"{KAIZEN_REGISTRY}/kaizen-agent:release-1.0.0@sha256:{'a' * 64}"
            ],
        )
        assert find_uncovered_env_image_refs(entry, KAIZEN_REGISTRY) == []

    def test_variable_bearing_values_are_skipped(self):
        """A bare ``${VAR}`` and a default whose tag is itself a variable
        can't be statically resolved — the runtime rewrite machinery owns
        those; the validator must not guess."""
        compose = f"""
services:
  backend:
    image: {KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0
    environment:
      A: ${{AGENT_SERVER_IMAGE}}
      B: ${{AGENT_SERVER_IMAGE:-{KAIZEN_REGISTRY}/kaizen-agent:$TAG}}
"""
        entry = _entry(
            compose,
            docker_images=[f"{KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0"],
        )
        assert find_uncovered_env_image_refs(entry, KAIZEN_REGISTRY) == []

    def test_bare_prefix_value_is_skipped(self):
        """SANDBOX_ALLOWED_IMAGE_PREFIXES-style values are repo *prefixes*
        (no tag), not pullable refs — out of scope for coverage."""
        compose = f"""
services:
  backend:
    image: {KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0
    environment:
      SANDBOX_ALLOWED_IMAGE_PREFIXES: {KAIZEN_REGISTRY}/kaizen-agent
"""
        entry = _entry(
            compose,
            docker_images=[f"{KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0"],
        )
        assert find_uncovered_env_image_refs(entry, KAIZEN_REGISTRY) == []

    def test_external_registry_refs_are_skipped(self):
        """Refs outside the extension's registry keep their independent
        cadence; the guard only enforces images this registry owns."""
        compose = f"""
services:
  backend:
    image: {KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0
    environment:
      HELPER_IMAGE: docker.io/library/redis:7
"""
        entry = _entry(
            compose,
            docker_images=[f"{KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0"],
        )
        assert find_uncovered_env_image_refs(entry, KAIZEN_REGISTRY) == []

    def test_list_shaped_environment_is_walked(self):
        compose = f"""
services:
  backend:
    image: {KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0
    environment:
      - AGENT_SERVER_IMAGE=${{AGENT_SERVER_IMAGE:-{KAIZEN_REGISTRY}/kaizen-agent:2.0.2}}
"""
        entry = _entry(
            compose,
            docker_images=[f"{KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0"],
        )
        violations = find_uncovered_env_image_refs(entry, KAIZEN_REGISTRY)
        assert len(violations) == 1
        assert "AGENT_SERVER_IMAGE" in violations[0]

    def test_bare_env_value_ref_is_checked(self):
        """An image ref written directly (no ``${VAR:-...}`` wrapper) is the
        same dynamic-spawn surface and gets the same coverage check."""
        compose = f"""
services:
  backend:
    image: {KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0
    environment:
      AGENT_SERVER_IMAGE: {KAIZEN_REGISTRY}/kaizen-agent:2.0.2
"""
        entry = _entry(
            compose,
            docker_images=[f"{KAIZEN_REGISTRY}/kaizen-backend:release-1.0.0"],
        )
        violations = find_uncovered_env_image_refs(entry, KAIZEN_REGISTRY)
        assert len(violations) == 1

    def test_missing_or_malformed_compose_yields_no_violations(self):
        assert find_uncovered_env_image_refs({"name": "x"}, KAIZEN_REGISTRY) == []
        assert (
            find_uncovered_env_image_refs(
                {"compose_yml": ":\nnot yaml: ["}, KAIZEN_REGISTRY
            )
            == []
        )
