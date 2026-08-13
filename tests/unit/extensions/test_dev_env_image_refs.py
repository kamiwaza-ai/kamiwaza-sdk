"""``kz-ext dev`` must rewrite image refs embedded in env values (ENG-7110).

The dev compose transform rewrites service ``image:`` fields to the
dev-built ref, but leaves image refs that live *inside* env values
untouched. Kaizen's backend spawns agent sandbox pods dynamically from
``AGENT_SERVER_IMAGE``; its compose default
(``${AGENT_SERVER_IMAGE:-ghcr.io/.../images/kaizen-agent:2.0.2}``) is the
*released* tag, which ``kz-ext dev`` never builds or pushes. So the
sandbox pod ImagePullBackOffs on a ref that doesn't exist and chat 500s.

This is the dev analog of ENG-5260 (publish-side env-image rewriting in
``registry_builder._apply_env_image_rewrites``). It additionally aligns
``SANDBOX_ALLOWED_IMAGE_PREFIXES`` in lockstep — the agent is built into the
cluster dev registry, which is *outside* the original whitelist (that names
the ghcr.io publish namespace), so the sandbox controller would otherwise
reject the very image the backend asks it to run.

ENG-8626: the agent used to be built at the legacy ``{registry}/{ext}-agent``
fallback purely because profiled services were omitted from the canonical-ref
map. It now shares the same relocation rule as every other owned build image.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch

import click
import pytest

from kamiwaza_extensions.compose_transformer import (
    ComposeTransformer,
    compute_canonical_refs,
)
from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.dev_env_image_refs import (
    build_image_ref_map,
    rewrite_env_image_refs,
)
from kamiwaza_extensions.extension_detector import ExtensionInfo
from kamiwaza_extensions.image_builder import ImageBuildError

pytestmark = [pytest.mark.unit, pytest.mark.extension_regression]

_REGISTRY = "host.docker.internal:5001"
_EXT = "kaizen"
_REV = "2.0.2-dev-f46cd1fc.1781636703"

_AGENT_GHCR = (
    "ghcr.io/kamiwaza-internal/kamiwaza-extensions-kaizen/images/kaizen-agent"
)
_BACKEND_GHCR = (
    "ghcr.io/kamiwaza-internal/kamiwaza-extensions-kaizen/images/kaizen-backend"
)
_CONTROLLER_GHCR = (
    "ghcr.io/kamiwaza-internal/kamiwaza-extensions-kaizen/images/kaizen-controller"
)
def _dev_repo(ghcr_repo: str) -> str:
    """The dev-registry repo for a declared GHCR repo (ENG-8626).

    ``purpose="dev"`` swaps the registry host and keeps the repository path,
    so every owned Kaizen image lands beneath the cluster dev registry at a
    distinct, deterministic path.
    """
    return ghcr_repo.replace("ghcr.io", _REGISTRY, 1)


# What ImageBuilder.build actually tags each owned image at. Pre-ENG-8626 the
# agent (profiled → absent from canonical_refs) fell back to
# ``{registry}/{ext}-agent:{tag}`` while its non-profiled siblings kept their
# declared ghcr.io namespace — the mixed batch this ticket removes.
_AGENT_BUILT = f"{_dev_repo(_AGENT_GHCR)}:{_REV}"
_BACKEND_BUILT = f"{_dev_repo(_BACKEND_GHCR)}:{_REV}"
_CONTROLLER_BUILT = f"{_dev_repo(_CONTROLLER_GHCR)}:{_REV}"


def _kaizen_compose() -> dict:
    """Minimal Kaizen-shaped compose: a profiled (image-only) agent build
    service, a backend that references the agent via ``AGENT_SERVER_IMAGE``,
    and a sandbox-controller carrying the image whitelist."""
    return {
        "services": {
            "agent": {
                "profiles": ["image-only"],
                "build": {"context": ".", "dockerfile": "backend/Dockerfile.agent"},
                "image": f"{_AGENT_GHCR}:2.0.2",
            },
            "backend": {
                "build": {"context": "."},
                "image": f"{_BACKEND_GHCR}:2.0.2",
                "environment": {
                    "AGENT_SERVER_IMAGE": f"${{AGENT_SERVER_IMAGE:-{_AGENT_GHCR}:2.0.2}}",
                    "DEFAULT_AGENTS_CONFIG": "${DEFAULT_AGENTS_CONFIG:-default_agents.yaml}",
                },
            },
            "sandbox-controller": {
                "build": {"context": "."},
                "image": f"{_CONTROLLER_GHCR}:2.0.2",
                "environment": {
                    "SANDBOX_ALLOWED_IMAGE_PREFIXES": (
                        f"ghcr.io/openhands/,{_AGENT_GHCR}"
                    ),
                },
            },
            "postgres": {
                "image": "ghcr.io/kamiwaza-internal/containers/images/postgres:v18.4",
            },
        },
    }


def _ref_map(source_services: dict) -> dict:
    canonical = compute_canonical_refs(
        source_services,
        purpose="dev",
        registry=_REGISTRY,
        extension_name=_EXT,
        revision_tag=_REV,
    )
    return build_image_ref_map(source_services, canonical)


class TestBuildImageRefMap:
    """``build_image_ref_map`` reads the dev canonical map, so the env rewrite
    targets the exact ref each image is built and pushed at."""

    def test_profiled_agent_maps_to_dev_registry_ref(self):
        ref_map = _ref_map(_kaizen_compose()["services"])
        # ENG-8626: the profiled agent is in the dev canonical map now, so it
        # is built at the same relocated path as its siblings rather than the
        # legacy {registry}/{ext}-agent fallback it used to get by omission.
        assert ref_map[_AGENT_GHCR] == _AGENT_BUILT

    def test_normal_service_relocated_to_dev_registry(self):
        ref_map = _ref_map(_kaizen_compose()["services"])
        # ENG-8626: this used to assert the declared ghcr.io namespace was
        # kept — i.e. that kz-ext dev built and pushed an owned image to the
        # org registry. It builds into the cluster dev registry instead.
        assert ref_map[_BACKEND_GHCR] == _BACKEND_BUILT
        assert not ref_map[_BACKEND_GHCR].startswith("ghcr.io/")

    def test_every_owned_image_lands_under_the_dev_registry(self):
        ref_map = _ref_map(_kaizen_compose()["services"])
        # The whole point: one registry for the whole batch, no mixed refs.
        assert set(ref_map) == {_AGENT_GHCR, _BACKEND_GHCR, _CONTROLLER_GHCR}
        assert all(
            built.startswith(f"{_REGISTRY}/") for built in ref_map.values()
        ), ref_map
        assert ref_map[_CONTROLLER_GHCR] == _CONTROLLER_BUILT

    def test_external_image_excluded(self):
        ref_map = _ref_map(_kaizen_compose()["services"])
        # postgres has no build context — not a built image, must not appear.
        assert all("postgres" not in repo for repo in ref_map)


class TestRewriteEnvImageRefs:
    """The K8s payload surface: env values are bare (post
    ``resolve_env_placeholders``) literal refs."""

    def _transformed(self) -> dict:
        compose = _kaizen_compose()
        transformer = ComposeTransformer()
        transformed = transformer.transform(
            compose,
            extension_name=_EXT,
            revision_tag=_REV,
            registry=_REGISTRY,
            purpose="dev",
        )
        return transformer.resolve_env_placeholders(transformed)

    def test_agent_server_image_rewritten_to_built_ref(self):
        compose = _kaizen_compose()
        transformed = self._transformed()
        ref_map = _ref_map(compose["services"])

        result = rewrite_env_image_refs(transformed, ref_map)

        env = result["services"]["backend"]["environment"]
        assert env["AGENT_SERVER_IMAGE"] == _AGENT_BUILT

    def test_whitelist_aligned_in_lockstep(self):
        compose = _kaizen_compose()
        transformed = self._transformed()
        ref_map = _ref_map(compose["services"])

        result = rewrite_env_image_refs(transformed, ref_map)

        env = result["services"]["sandbox-controller"]["environment"]
        prefixes = env["SANDBOX_ALLOWED_IMAGE_PREFIXES"].split(",")
        # The built agent prefix is appended; the originals are preserved
        # (some flows may still reference the ghcr path). The appended prefix
        # must be the *relocated* repo — the controller gates the sandbox on
        # this list, so it has to admit exactly the ref AGENT_SERVER_IMAGE
        # names, which is now under the dev registry (ENG-8626).
        assert _dev_repo(_AGENT_GHCR) in prefixes
        assert _AGENT_BUILT.startswith(_dev_repo(_AGENT_GHCR) + ":")
        assert "ghcr.io/openhands/" in prefixes
        assert _AGENT_GHCR in prefixes

    def test_non_image_env_untouched(self):
        compose = _kaizen_compose()
        transformed = self._transformed()
        ref_map = _ref_map(compose["services"])

        result = rewrite_env_image_refs(transformed, ref_map)

        env = result["services"]["backend"]["environment"]
        assert env["DEFAULT_AGENTS_CONFIG"] == "default_agents.yaml"

    def test_does_not_mutate_input(self):
        compose = _kaizen_compose()
        transformed = self._transformed()
        ref_map = _ref_map(compose["services"])
        before = copy.deepcopy(transformed)

        rewrite_env_image_refs(transformed, ref_map)

        assert transformed == before


class TestRewriteEnvImageRefsCatalogSurface:
    """The catalog-overlay surface keeps ``${VAR:-default}`` placeholders
    (the platform performs install-time substitution), so the rewrite must
    rewrite the default *inside* the substitution form."""

    def test_substitution_default_rewritten(self):
        compose = _kaizen_compose()
        ref_map = _ref_map(compose["services"])
        # catalog_compose is transformed but NOT resolve_env_placeholders'd.
        catalog = ComposeTransformer().transform(
            compose,
            extension_name=_EXT,
            revision_tag=_REV,
            registry=_REGISTRY,
            purpose="dev",
        )

        result = rewrite_env_image_refs(catalog, ref_map)

        env = result["services"]["backend"]["environment"]
        # The ${VAR:-default} form is preserved; only the default is rewritten
        # so a runtime override still wins.
        assert env["AGENT_SERVER_IMAGE"] == (
            f"${{AGENT_SERVER_IMAGE:-{_AGENT_BUILT}}}"
        )

    def test_templated_allowlist_appended_in_lockstep(self):
        """Regression (cron re-review High #1): a *templated*
        ``${SANDBOX_ALLOWED_IMAGE_PREFIXES:-csv}`` must get the built prefix
        appended inside the substitution form — same ``${VAR:-default}``
        unwrap AGENT_SERVER_IMAGE gets. Before the fix, the CSV was split with
        the ``${…:-``/``}`` still attached, no entry matched, and the lockstep
        append silently dropped while AGENT_SERVER_IMAGE was still rewritten.
        """
        ref_map = _ref_map(_kaizen_compose()["services"])
        compose = {
            "services": {
                "sandbox-controller": {
                    "environment": {
                        "SANDBOX_ALLOWED_IMAGE_PREFIXES": (
                            f"${{SANDBOX_ALLOWED_IMAGE_PREFIXES:-ghcr.io/openhands/,{_AGENT_GHCR}}}"
                        ),
                    },
                },
            },
        }

        result = rewrite_env_image_refs(compose, ref_map)
        wl = result["services"]["sandbox-controller"]["environment"][
            "SANDBOX_ALLOWED_IMAGE_PREFIXES"
        ]

        # Substitution form preserved, built prefix appended inside it.
        assert wl == (
            f"${{SANDBOX_ALLOWED_IMAGE_PREFIXES:-ghcr.io/openhands/,"
            f"{_AGENT_GHCR},{_dev_repo(_AGENT_GHCR)}}}"
        )


class TestRewriteEnvImageRefsListForm:
    """Robustness: env may be a list of ``KEY=value`` strings, not a dict."""

    def test_list_form_agent_server_image(self):
        ref_map = _ref_map(_kaizen_compose()["services"])
        compose = {
            "services": {
                "backend": {
                    "environment": [
                        f"AGENT_SERVER_IMAGE={_AGENT_GHCR}:2.0.2",
                        "FOO=bar",
                    ],
                },
            },
        }
        result = rewrite_env_image_refs(compose, ref_map)
        env = result["services"]["backend"]["environment"]
        assert f"AGENT_SERVER_IMAGE={_AGENT_BUILT}" in env
        assert "FOO=bar" in env

    def test_name_value_dict_form_agent_server_image(self):
        ref_map = _ref_map(_kaizen_compose()["services"])
        value_from = {"secretKeyRef": {"name": "agent", "key": "image"}}
        compose = {
            "services": {
                "backend": {
                    "environment": [
                        {
                            "name": "AGENT_SERVER_IMAGE",
                            "value": f"${{AGENT_SERVER_IMAGE:-{_AGENT_GHCR}:2.0.2}}",
                        },
                        {
                            "name": "AGENT_SERVER_IMAGE_FROM_SECRET",
                            "valueFrom": value_from,
                        },
                    ],
                },
            },
        }

        result = rewrite_env_image_refs(compose, ref_map)

        assert result["services"]["backend"]["environment"] == [
            {
                "name": "AGENT_SERVER_IMAGE",
                "value": f"${{AGENT_SERVER_IMAGE:-{_AGENT_BUILT}}}",
            },
            {
                "name": "AGENT_SERVER_IMAGE_FROM_SECRET",
                "valueFrom": value_from,
            },
        ]


class TestDevRemoteWiresEnvRewrite:
    """``run_dev_remote`` must actually apply the env rewrite — guards against
    the helper being correct but never called (the 2-line wiring in dev.py).
    """

    def test_run_dev_remote_invokes_env_rewrite_for_both_surfaces(self, tmp_path):
        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.dev_env_image_refs import (
            rewrite_env_image_refs as real_rewrite,
        )

        info = ExtensionInfo(
            path=tmp_path,
            name=_EXT,
            version="2.0.2",
            metadata={"name": _EXT, "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data=_kaizen_compose(),
        )

        calls: list = []

        def _spy(compose, ref_map):
            calls.append((compose, ref_map))
            return real_rewrite(compose, ref_map)

        # Stop the pipeline right after the rewrite step (which runs before
        # the build) by raising from ImageBuilder.build.
        builder = MagicMock()
        builder.build.side_effect = ImageBuildError("stop-after-capture")

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=False,
        )
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = _REV
        tagger.get_git_info.return_value = ("f46cd1fc", False)

        with (
            patch(
                "kamiwaza_extensions.extension_detector.ExtensionDetector",
                return_value=detector,
            ),
            patch(
                "kamiwaza_extensions.connections.ConnectionManager",
                return_value=conn_mgr,
            ),
            patch(
                "kamiwaza_extensions.revision_tagger.RevisionTagger",
                return_value=tagger,
            ),
            patch(
                "kamiwaza_extensions.image_builder.ImageBuilder",
                return_value=builder,
            ),
            patch(
                "kamiwaza_extensions.dev_env_image_refs.rewrite_env_image_refs",
                side_effect=_spy,
            ),
            patch.object(dev_cmd, "_detect_kind_registry", return_value=None),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value=None,
            ),
            patch("kamiwaza_extensions.dev_state.read_state", return_value=None),
            patch("kamiwaza_extensions.dev_state.resume_message", return_value=None),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote(no_push=True)

        # Called for both the K8s payload and the catalog overlay compose.
        assert len(calls) == 2, "env rewrite must run on transformed AND catalog"
        # The ref_map must map the agent's declared repo to a dev-built
        # kaizen-agent ref (the profiled image-only fallback path), and at
        # least one surface must carry AGENT_SERVER_IMAGE.
        ref_map = calls[0][1]
        assert _AGENT_GHCR in ref_map
        assert ref_map[_AGENT_GHCR].split("/")[-1] == f"{_EXT}-agent:{_REV}"
        carried = any(
            "AGENT_SERVER_IMAGE" in (svc.get("environment") or {})
            for compose, _ in calls
            for svc in (compose.get("services") or {}).values()
            if isinstance(svc.get("environment"), dict)
        )
        assert carried, "AGENT_SERVER_IMAGE must reach the rewrite step"
