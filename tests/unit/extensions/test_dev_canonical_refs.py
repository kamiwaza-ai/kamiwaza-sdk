"""Tests that ``run_dev_remote`` passes canonical image refs to the builder.

One ref, everywhere: whatever ``kz-ext dev`` builds, it must push, and the
K8s payload must pull exactly that. ``ImageBuilder.build`` therefore takes
``image_refs=`` from the same ``compute_canonical_refs`` map that feeds the
transformed compose. Left to itself the builder would synthesize the legacy
``{registry}/{ext}-{svc}:{tag}`` form while the payload named something else,
and an extension with a non-conventional repository path (omniparse at
``.../tool-omniparse/omniparse``) would ImagePullBackOff.

ENG-8626: that map is now computed with ``purpose="dev"``, which relocates an
owned build image into the *resolved cluster registry* while preserving its
declared repository path. Dev previously honored the declared ``ghcr.io``
host, so it built, pushed, and deployed owned dev images to the org registry —
failing outright when the developer couldn't write dev tags there, and
depending on an external private registry for local cluster development.
Publish still preserves the declared namespace; see ``test_publish_cmd.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest

from kamiwaza_extensions.connections import ConnectionInfo
from kamiwaza_extensions.extension_detector import ExtensionInfo
from kamiwaza_extensions.image_builder import ImageBuildError

pytestmark = [pytest.mark.unit, pytest.mark.extension_regression]


def _info_with_divergent_namespace(tmp_path: Path) -> ExtensionInfo:
    """Extension with one service whose image namespace diverges from the
    legacy ``{ext}-{svc}`` convention — mirrors omniparse's
    ``ghcr.io/.../tool-omniparse/omniparse``."""
    compose_data = {
        "services": {
            "omniparse": {
                "image": "ghcr.io/example/tool-omniparse/omniparse:0.1.0",
                "build": {"context": "./images/omniparse"},
                "ports": ["8000:8000"],
            },
        },
    }
    return ExtensionInfo(
        path=tmp_path,
        name="tool-omniparse",
        version="0.1.0",
        metadata={"name": "tool-omniparse", "type": "tool"},
        compose_path=tmp_path / "docker-compose.yml",
        compose_data=compose_data,
    )


def _active_connection() -> ConnectionInfo:
    return ConnectionInfo(
        name="dev",
        url="https://kamiwaza.test/api",
        active=True,
        created_at=0.0,
        verify_ssl=False,
    )


class TestDevRemoteBuildsAtCanonicalRefs:
    """``ImageBuilder.build`` must receive ``image_refs`` from the dev
    canonical map — the same source of truth as the K8s payload's image
    refs, so the ref we build is the ref the cluster pulls.

    ENG-8626: dev relocates an owned build image into the resolved cluster
    registry, preserving the declared repository path. It used to hand the
    builder the declared ``ghcr.io`` namespace verbatim, so ``kz-ext dev``
    built and pushed owned dev images to the org registry."""

    def test_divergent_image_namespace_flows_through_to_builder(
        self, tmp_path, monkeypatch
    ):
        from kamiwaza_extensions.commands import dev as dev_cmd

        info = _info_with_divergent_namespace(tmp_path)

        # Stub the heavy chain so we land on builder.build with the right
        # canonical_refs dict and immediately exit.
        captured: dict = {}

        def _capture_and_raise(**kwargs):
            captured.update(kwargs)
            raise ImageBuildError("stop-after-capture")

        token = MagicMock(access_token="tok-abc")
        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = token

        detector = MagicMock()
        detector.detect.return_value = info

        tagger = MagicMock()
        tagger.generate_tag.return_value = "0.1.0-dev-abc.123"
        tagger.get_git_info.return_value = ("abc1234", False)

        builder = MagicMock()
        builder.build.side_effect = _capture_and_raise

        # Function-local imports — patch at source module.
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
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            # ImageBuildError → typer.Exit(code=1) inside run_dev_remote
            dev_cmd.run_dev_remote(no_push=True)

        builder.build.assert_called_once()
        assert "image_refs" in captured, (
            "ImageBuilder.build must receive image_refs= so the built ref "
            "matches the ref the transformed compose deploys."
        )
        # Registry host relocated to the resolved dev registry; the declared
        # repository path (tool-omniparse/omniparse — which does NOT follow
        # the {ext}-{svc} convention) survives, so nothing collides and the
        # rewrite is idempotent.
        assert captured["image_refs"] == {
            "omniparse": (
                "registry.kamiwaza.test/example/tool-omniparse/omniparse"
                ":0.1.0-dev-abc.123"
            ),
        }
        assert not captured["image_refs"]["omniparse"].startswith("ghcr.io/")

    def test_display_name_fallback_image_refs_are_sanitized(self, tmp_path):
        from kamiwaza_extensions.commands import dev as dev_cmd

        info = ExtensionInfo(
            path=tmp_path,
            name="Hello Web",
            version="0.1.0",
            metadata={"name": "Hello Web", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )
        captured: dict = {}

        def _capture_and_raise(**kwargs):
            captured.update(kwargs)
            raise ImageBuildError("stop-after-capture")

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)
        builder = MagicMock()
        builder.build.side_effect = _capture_and_raise

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
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote(no_push=True)

        assert captured["image_refs"] == {
            "api": "registry.kamiwaza.test/hello-web-api:dev1",
        }

    def test_push_registry_split_retags_without_changing_image_refs(
        self, tmp_path, monkeypatch
    ):
        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.image_pusher import ImagePushError

        monkeypatch.delenv("KAMIWAZA_PUSH_REGISTRY", raising=False)
        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )

        captured: dict = {}

        def _capture_push(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            raise ImagePushError("stop-after-capture")

        token = MagicMock(access_token="tok-abc")
        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = token

        detector = MagicMock()
        detector.detect.return_value = info

        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)

        pusher = MagicMock()
        pusher.push.side_effect = _capture_push

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
                return_value="podman-machine-default",
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote(no_build=True)

        assert captured["args"][0] == ["127.0.0.1:30010/my-app-api:dev1"]
        assert captured["kwargs"]["registry"] == "host.containers.internal:30010"
        assert captured["kwargs"]["target_refs"] == {
            "127.0.0.1:30010/my-app-api:dev1": "host.containers.internal:30010/my-app-api:dev1",
        }
        # ENG-5719: the local (loopback) dev registry is an anonymous
        # registry:2 — the login is skipped (token=None) so the macOS-podman
        # host-side `podman login` can't break an otherwise-working push.
        assert captured["kwargs"]["token"] is None

    def test_non_loopback_registry_keeps_login_token(self, tmp_path, monkeypatch):
        """ENG-5719: a non-loopback registry (e.g. an authenticated
        ``registry.<domain>`` ingress or explicit remote ``KAMIWAZA_REGISTRY``)
        must still receive the connection token so the registry login runs."""
        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.image_pusher import ImagePushError

        monkeypatch.delenv("KAMIWAZA_PUSH_REGISTRY", raising=False)
        monkeypatch.setenv("KAMIWAZA_REGISTRY", "registry.example:5000")
        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )

        captured: dict = {}

        def _capture_push(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            raise ImagePushError("stop-after-capture")

        token = MagicMock(access_token="tok-abc")
        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = token

        detector = MagicMock()
        detector.detect.return_value = info

        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)

        pusher = MagicMock()
        pusher.push.side_effect = _capture_push

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote(no_build=True)

        # Non-loopback image registry → no push split, login token preserved.
        assert captured["kwargs"]["registry"] == "registry.example:5000"
        assert captured["kwargs"]["token"] == "tok-abc"

    def test_no_build_refuses_when_prior_build_engine_differs(
        self, tmp_path, monkeypatch
    ):
        """jxstanford iter-4 High #1, claude iter-5 S2 (e2e coverage):
        a ``--no-build`` resume whose active push engine differs from
        ``last_build_engine`` must refuse with exit 1 before ImagePusher
        is invoked. Docker and Podman keep separate image stores; the
        previously-built image isn't visible to the engine that would
        push, so retag/push would fail with a confusing error."""

        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.dev_state import DevState

        # Active push will pick podman: insecure connection (verify_ssl=
        # False) + podman on PATH. Prior build was docker → mismatch.
        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )

        # ``last_successful_step="build"`` (not "push") so resume keeps
        # push active — the engine-mismatch refuse must fire before
        # ImagePusher is invoked. With "push" complete, resume would
        # auto-skip push and the refuse check would have nothing to gate.
        prior_state = DevState(
            last_run_at="2026-05-26T00:00:00+00:00",
            last_revision="0.1.0-dev-abc1234.1714999999",
            last_successful_step="build",
            cluster="https://kamiwaza.test/api",
            extension_name="my-app",
            last_registry="127.0.0.1:30010",
            last_push_registry="host.containers.internal:30010",
            last_build_engine="docker",
        )

        token = MagicMock(access_token="tok-abc")
        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = token

        detector = MagicMock()
        detector.detect.return_value = info

        tagger = MagicMock()
        # Match the revision exactly so _is_resumable accepts.
        tagger.generate_tag.return_value = "0.1.0-dev-abc1234.1714999999"
        tagger.get_git_info.return_value = ("abc1234", False)

        pusher = MagicMock()  # Should never be called when refuse fires.

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
                return_value="podman-machine-default",
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=prior_state,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            # Force select_push_engine → "podman" so we have an actual
            # mismatch with the prior build engine. Also pretend docker
            # accepts the alias so the unrelated insecure-registries
            # pre-flight doesn't fire first.
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=True,
            ),
            pytest.raises(click.exceptions.Exit) as exc_info,
        ):
            dev_cmd.run_dev_remote(no_build=True)

        # Refuse exits before ImagePusher.push is invoked.
        assert exc_info.value.exit_code == 1
        pusher.push.assert_not_called()

    def test_no_build_treats_missing_prior_build_engine_as_docker(
        self, tmp_path, monkeypatch
    ):
        """Older dev-state files did not record ``last_build_engine``.

        Those builds came from the Docker-only build path, so a resumable
        ``--no-build`` push that would now use Podman must still be refused."""

        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.dev_state import DevState

        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )
        prior_state = DevState(
            last_run_at="2026-05-26T00:00:00+00:00",
            last_revision="0.1.0-dev-abc1234.1714999999",
            last_successful_step="build",
            cluster="https://kamiwaza.test/api",
            extension_name="my-app",
            last_registry="127.0.0.1:30010",
            last_push_registry="host.containers.internal:30010",
            last_build_engine="",
        )

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "0.1.0-dev-abc1234.1714999999"
        tagger.get_git_info.return_value = ("abc1234", False)
        pusher = MagicMock()

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
                return_value="podman-machine-default",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=prior_state,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit) as exc_info,
        ):
            dev_cmd.run_dev_remote(no_build=True)

        assert exc_info.value.exit_code == 1
        pusher.push.assert_not_called()

    def test_no_build_allows_stale_prior_engine_when_not_resumable(self, tmp_path):
        """The engine-mismatch guard only applies to a matching resume state.

        A stale dev-state file from a different revision must not block an
        explicit ``--no-build`` push; the user is asserting the image already
        exists in the active engine's store for the current inputs.
        """

        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.dev_state import DevState
        from kamiwaza_extensions.image_pusher import ImagePushError

        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )
        prior_state = DevState(
            last_run_at="2026-05-26T00:00:00+00:00",
            last_revision="old-revision",
            last_successful_step="build",
            cluster="https://kamiwaza.test/api",
            extension_name="my-app",
            last_registry="127.0.0.1:30010",
            last_push_registry="127.0.0.1:30010",
            last_build_engine="docker",
        )

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "new-revision"
        tagger.get_git_info.return_value = ("abc1234", False)
        pusher = MagicMock()
        pusher.push.side_effect = ImagePushError("stop-after-capture")

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=False,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=True,
            ),
            # On macOS/Windows ``podman_push_available()`` gates on a running
            # Podman machine; pin it so the engine choice doesn't depend on the
            # host (ENG-7006 — without this, a host with no running machine
            # falls back to 'docker' and the assertion below fails).
            patch(
                "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
                return_value="podman-machine-default",
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=prior_state,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit) as exc_info,
        ):
            dev_cmd.run_dev_remote(no_build=True)

        assert exc_info.value.exit_code == 1
        pusher.push.assert_called_once()
        assert pusher.push.call_args.kwargs["engine"] == "podman"


class TestInsecurePreflightSource:
    """ENG-5719 follow-up: the push pre-flight must derive ``insecure`` from
    ``effective_verify_ssl()`` (env override / dev-hostname auto-disable /
    persisted flag), not the persisted ``verify_ssl`` alone."""

    def test_insecure_uses_effective_verify_ssl_not_persisted_flag(self, tmp_path):
        from kamiwaza_extensions.commands import dev as dev_cmd

        # Persisted verify_ssl=True, but a dev URL auto-disables TLS, so
        # effective_verify_ssl() is False -> the insecure path must be picked.
        # The old `not connection.verify_ssl` would compute insecure=False and
        # select the secure Docker push path, then HTTPS-fail against the
        # plain-HTTP loopback registry.
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,
        )
        assert conn.verify_ssl is True
        assert conn.effective_verify_ssl() is False

        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = conn
        conn_mgr.get_token.return_value = MagicMock(access_token="tok")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "0.1.0-dev-abc1234.1"
        tagger.get_git_info.return_value = ("abc1234", False)

        spy_select = MagicMock(return_value="docker")

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
                "kamiwaza_extensions.registry_resolution.select_push_engine",
                spy_select,
            ),
            # Stop right after engine selection so we don't need the full
            # build/push/deploy scaffolding; run_dev_remote converts the
            # ValueError into Exit(1).
            patch(
                "kamiwaza_extensions.registry_resolution.resolve_dev_registries",
                side_effect=ValueError("stop after engine selection"),
            ),
            pytest.raises(click.exceptions.Exit) as exc_info,
        ):
            dev_cmd.run_dev_remote(no_build=True)

        assert exc_info.value.exit_code == 1
        spy_select.assert_called_once_with(insecure=True, push_registry=None)

    def test_push_call_uses_effective_insecure_not_persisted_flag(
        self, tmp_path, monkeypatch
    ):
        """ENG-5719 follow-up: the ``ImagePusher.push`` call itself must
        receive ``insecure`` derived from ``effective_verify_ssl()`` — the
        same value engine selection and the pre-flight use — not the persisted
        ``verify_ssl``. A dev-host connection with persisted ``verify_ssl=True``
        auto-disables TLS (effective False), so the push must be insecure; the
        old ``not connection.verify_ssl`` computed ``insecure=False`` and drove
        Docker-over-HTTPS against the plain-HTTP loopback registry — the exact
        desync the resolver/pre-flight were already fixed for."""
        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.image_pusher import ImagePushError

        monkeypatch.delenv("KAMIWAZA_PUSH_REGISTRY", raising=False)
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,
        )
        assert conn.verify_ssl is True
        assert conn.effective_verify_ssl() is False

        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )

        captured: dict = {}

        def _capture_push(*args, **kwargs):
            captured["kwargs"] = kwargs
            raise ImagePushError("stop-after-capture")

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = conn
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)
        pusher = MagicMock()
        pusher.push.side_effect = _capture_push

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            # No VM remap → push registry == image registry → the unrelated
            # insecure-registries pre-flight (push != registry) stays out and
            # we land squarely on the push call.
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=False,
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote(no_build=True)

        # The old code passed ``insecure=not connection.verify_ssl`` (False here)
        # and would have driven the secure push path; the fix forwards the
        # effective ``insecure`` (True).
        assert captured["kwargs"]["insecure"] is True

    def test_secure_user_supplied_push_registry_does_not_inherit_api_insecure(
        self, tmp_path, monkeypatch
    ):
        """A dev-host connection can be insecure while the explicit push
        registry is a normal HTTPS registry.

        Registry push TLS must be based on the push target/source, not on the
        Kamiwaza API URL. With ``--no-build`` and Podman installed, the old
        code selected Podman from ``effective_verify_ssl()`` and then ran
        ``podman login/push --tls-verify=false`` against this secure override.
        """

        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.image_pusher import ImagePushError

        monkeypatch.setenv("KAMIWAZA_PUSH_REGISTRY", "registry.example.com")
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,
        )
        assert conn.effective_verify_ssl() is False

        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )
        pusher = MagicMock()
        pusher.push.side_effect = ImagePushError("stop-after-capture")

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = conn
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=False,
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote(no_build=True)

        pusher.push.assert_called_once()
        assert pusher.push.call_args.kwargs["registry"] == "registry.example.com"
        assert pusher.push.call_args.kwargs["token"] == "tok-abc"
        assert pusher.push.call_args.kwargs["insecure"] is False
        assert pusher.push.call_args.kwargs["engine"] == "docker"

    def test_insecure_preflight_checks_non_split_docker_registry(
        self, tmp_path, monkeypatch
    ):
        """A derived non-loopback dev registry can still require HTTP push.

        When image and push registry are both ``registry.<dev-host>``, there is
        no retag map. Docker still needs that registry in insecure-registries
        before it will push over HTTP, so the preflight must not be limited to
        split VM-alias refs."""

        from kamiwaza_extensions.commands import dev as dev_cmd

        monkeypatch.delenv("KAMIWAZA_REGISTRY", raising=False)
        monkeypatch.delenv("KAMIWAZA_PUSH_REGISTRY", raising=False)
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,
        )
        assert conn.effective_verify_ssl() is False
        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )
        pusher = MagicMock()

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = conn
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.podman_push_available",
                return_value=False,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=False,
            ) as mock_accepts,
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit) as exc_info,
        ):
            dev_cmd.run_dev_remote(no_build=True)

        assert exc_info.value.exit_code == 1
        mock_accepts.assert_called_once_with("registry.kamiwaza.test")
        pusher.push.assert_not_called()

    def test_explicit_podman_vm_alias_skips_login_for_local_registry(
        self, tmp_path, monkeypatch
    ):
        """A user-supplied Podman VM alias for the local anonymous registry
        is still local. Do not pass the Kamiwaza API token to ``podman login``;
        the host-side login cannot resolve the alias, while the VM-side push
        can."""

        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.image_pusher import ImagePushError

        monkeypatch.setenv("KAMIWAZA_PUSH_REGISTRY", "host.containers.internal:30010")
        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )
        pusher = MagicMock()
        pusher.push.side_effect = ImagePushError("stop-after-capture")

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
                return_value="podman-machine-default",
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote(no_build=True)

        pusher.push.assert_called_once()
        assert pusher.push.call_args.kwargs["registry"] == (
            "host.containers.internal:30010"
        )
        assert pusher.push.call_args.kwargs["token"] is None
        assert pusher.push.call_args.kwargs["insecure"] is True
        assert pusher.push.call_args.kwargs["engine"] == "podman"

    def test_completed_push_resume_does_not_require_running_podman_machine(
        self, tmp_path, monkeypatch
    ):
        """Registry resolution must not require Podman machine liveness before
        dev-state can skip an already-completed push."""

        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.dev_state import DevState

        monkeypatch.setenv("KAMIWAZA_PUSH_REGISTRY", "host.containers.internal:30010")
        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )
        prior_state = DevState(
            last_run_at="2026-05-26T00:00:00+00:00",
            last_revision="dev1",
            last_successful_step="push",
            cluster="https://kamiwaza.test/api",
            extension_name="my-app",
            last_registry="127.0.0.1:30010",
            last_push_registry="host.containers.internal:30010",
            last_build_engine="podman",
        )

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)
        pusher = MagicMock()

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=prior_state,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.payload_builder.PayloadBuilder",
                side_effect=RuntimeError("reached-payload-stage"),
            ),
            pytest.raises(RuntimeError, match="reached-payload-stage"),
        ):
            dev_cmd.run_dev_remote(no_build=True)

        pusher.push.assert_not_called()

    def test_insecure_preflight_checks_user_supplied_docker_vm_alias(
        self, tmp_path, monkeypatch
    ):
        """Explicit Docker VM aliases are local plain-HTTP aliases too.

        A secure external override should skip the Docker insecure-registry
        preflight, but ``host.docker.internal`` targeting the loopback image
        registry still needs daemon.json coverage before retag/push runs.
        """

        from kamiwaza_extensions.commands import dev as dev_cmd

        monkeypatch.setenv("KAMIWAZA_PUSH_REGISTRY", "host.docker.internal:30010")
        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )
        pusher = MagicMock()

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=False,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=False,
            ) as mock_accepts,
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit) as exc_info,
        ):
            dev_cmd.run_dev_remote(no_build=True)

        assert exc_info.value.exit_code == 1
        mock_accepts.assert_called_once_with("host.docker.internal:30010")
        pusher.push.assert_not_called()

    def test_explicit_docker_vm_alias_is_local_with_non_loopback_image_registry(
        self, tmp_path, monkeypatch
    ):
        """An explicit VM alias is a local plain-HTTP push target even when
        the deployment image registry is a non-loopback host.

        This covers clusters where the kubelet pulls ``registry.kamiwaza.test``
        while the local Docker engine reaches that same registry through
        ``host.docker.internal``. The push must skip API-token login and still
        use the Docker insecure-registry preflight."""

        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.image_pusher import ImagePushError

        monkeypatch.setenv("KAMIWAZA_REGISTRY", "registry.kamiwaza.test")
        monkeypatch.setenv("KAMIWAZA_PUSH_REGISTRY", "host.docker.internal:30010")
        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )

        captured: dict = {}

        def _capture_push(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            raise ImagePushError("stop-after-capture")

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)
        pusher = MagicMock()
        pusher.push.side_effect = _capture_push

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=True,
            ) as mock_accepts,
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote(no_build=True)

        mock_accepts.assert_called_once_with("host.docker.internal:30010")
        assert captured["args"][0] == ["registry.kamiwaza.test/my-app-api:dev1"]
        assert captured["kwargs"]["registry"] == "host.docker.internal:30010"
        assert captured["kwargs"]["target_refs"] == {
            "registry.kamiwaza.test/my-app-api:dev1": "host.docker.internal:30010/my-app-api:dev1",
        }
        assert captured["kwargs"]["token"] is None
        assert captured["kwargs"]["insecure"] is True
        assert captured["kwargs"]["engine"] == "docker"

    def test_qualified_build_ref_pushes_via_vm_alias_transport(
        self, tmp_path, monkeypatch
    ):
        """A split image/push registry changes only the transport alias.

        ENG-8626: this used to assert the opposite — that a declared
        ``ghcr.io`` build ref stayed external, was pushed verbatim to GHCR,
        and got an empty ``target_refs``. That was the bug: the image is one
        ``kz-ext dev`` builds, so it belongs in the cluster registry.

        Now the canonical (deploy) ref sits under ``image_registry``
        (``127.0.0.1:30010``), and ``build_push_ref_map`` translates it to the
        VM-reachable ``push_registry`` alias purely for transport. The ref the
        CR deploys is the image_registry one; the alias never leaks into it.
        Because the push ref really does target the alias now, the Docker
        insecure-registry preflight correctly fires (it used to be skipped —
        no push ref reached that registry).
        """

        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.image_pusher import ImagePushError

        monkeypatch.delenv("KAMIWAZA_PUSH_REGISTRY", raising=False)
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,
        )
        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={
                "services": {
                    "api": {
                        "build": {"context": "."},
                        "image": "ghcr.io/example/custom-api:0.1.0",
                    }
                }
            },
        )
        pusher = MagicMock()
        pusher.push.side_effect = ImagePushError("stop-after-capture")

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = conn
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._docker_is_working",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=False,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=True,
            ) as mock_accepts,
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote(no_build=True)

        # The push ref now targets the alias, so the daemon preflight applies.
        mock_accepts.assert_called_once_with("host.docker.internal:30010")
        pusher.push.assert_called_once()

        image_ref = "127.0.0.1:30010/example/custom-api:dev1"
        push_ref = "host.docker.internal:30010/example/custom-api:dev1"
        # Pushed under the image registry, never GHCR.
        assert pusher.push.call_args.args[0] == [image_ref]
        assert pusher.push.call_args.kwargs["registry"] == "host.docker.internal:30010"
        # Transport-only: the alias lives in target_refs, not in the ref itself.
        assert pusher.push.call_args.kwargs["target_refs"] == {image_ref: push_ref}

    def test_podman_qualified_build_ref_pushes_via_vm_alias_transport(
        self, tmp_path, monkeypatch
    ):
        """Same alias-transport contract on the Podman path.

        ENG-8626: previously asserted that a declared ``ghcr.io`` build ref
        stayed external with an empty retag map. Dev owns the image, so it is
        built under ``image_registry`` and retagged to the Podman VM alias for
        transport only. The Docker daemon preflight stays skipped here because
        the push engine is Podman, not because no ref targets the alias.
        """

        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.image_pusher import ImagePushError

        monkeypatch.delenv("KAMIWAZA_PUSH_REGISTRY", raising=False)
        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={
                "services": {
                    "api": {
                        "build": {"context": "."},
                        "image": "ghcr.io/example/custom-api:0.1.0",
                    }
                }
            },
        )
        pusher = MagicMock()
        pusher.push.side_effect = ImagePushError("stop-after-capture")

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.running_podman_machine_name",
                return_value="podman-machine-default",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=False,
            ) as mock_accepts,
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote(no_build=True)

        # Skipped because the push engine is Podman — the preflight is
        # Docker-daemon-specific.
        mock_accepts.assert_not_called()
        pusher.push.assert_called_once()

        image_ref = "127.0.0.1:30010/example/custom-api:dev1"
        push_ref = "host.containers.internal:30010/example/custom-api:dev1"
        assert pusher.push.call_args.args[0] == [image_ref]
        assert pusher.push.call_args.kwargs["registry"] == (
            "host.containers.internal:30010"
        )
        assert pusher.push.call_args.kwargs["target_refs"] == {image_ref: push_ref}
        assert pusher.push.call_args.kwargs["engine"] == "podman"

    def test_fresh_build_forces_docker_push_engine_with_podman_installed(
        self, tmp_path, monkeypatch
    ):
        """Fresh ``kz-ext dev`` builds with Docker, so it must push with Docker.

        If the push path auto-selected Podman merely because the connection is
        insecure and Podman is installed, the Docker-built image would not be
        visible to the push engine.
        """

        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.image_pusher import ImagePushError

        monkeypatch.delenv("KAMIWAZA_PUSH_REGISTRY", raising=False)
        conn = ConnectionInfo(
            name="dev",
            url="https://kamiwaza.test/api",
            active=True,
            created_at=0.0,
            verify_ssl=True,
        )
        assert conn.effective_verify_ssl() is False

        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )
        built_ref = "127.0.0.1:30010/my-app-api:dev1"
        captured: dict = {}

        def _capture_push(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            raise ImagePushError("stop-after-capture")

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = conn
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "dev1"
        tagger.get_git_info.return_value = ("abc1234", False)
        builder = MagicMock()
        builder.build.return_value = [built_ref]
        pusher = MagicMock()
        pusher.push.side_effect = _capture_push

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._docker_is_working",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution._has_podman",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=True,
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            pytest.raises(click.exceptions.Exit),
        ):
            dev_cmd.run_dev_remote()

        builder.build.assert_called_once()
        assert builder.build.call_args.kwargs["registry"] == "127.0.0.1:30010"
        assert captured["args"][0] == [built_ref]
        assert captured["kwargs"]["registry"] == "host.docker.internal:30010"
        assert captured["kwargs"]["target_refs"] == {
            built_ref: "host.docker.internal:30010/my-app-api:dev1",
        }
        assert captured["kwargs"]["insecure"] is True
        assert captured["kwargs"]["engine"] == "docker"

    def test_insecure_preflight_skipped_when_resume_skips_push(
        self, tmp_path, monkeypatch
    ):
        """ENG-5719 follow-up: the insecure-registries pre-flight must be
        gated on the push actually running. It now lives inside the
        ``if not no_push and image_refs`` branch, so a resume that auto-skips
        an already-completed push cannot abort with a daemon.json error for a
        push that won't happen. The conditions below (insecure dev host, docker
        engine, remapped push alias, docker rejecting it) would have tripped
        the old pre-flight — which ran *before* resume flipped ``no_push`` —
        so this guards the ordering regression."""
        from kamiwaza_extensions.commands import dev as dev_cmd
        from kamiwaza_extensions.dev_state import DevState

        # Explicit push alias != image registry, so the pre-flight's
        # ``push_registry != registry`` precondition is satisfied.
        monkeypatch.setenv("KAMIWAZA_PUSH_REGISTRY", "host.docker.internal:30010")

        info = ExtensionInfo(
            path=tmp_path,
            name="my-app",
            version="0.1.0",
            metadata={"name": "my-app", "type": "app"},
            compose_path=tmp_path / "docker-compose.yml",
            compose_data={"services": {"api": {"build": {"context": "."}}}},
        )

        # Prior run completed "push" → resume must auto-skip build AND push.
        prior_state = DevState(
            last_run_at="2026-05-26T00:00:00+00:00",
            last_revision="0.1.0-dev-abc1234.1714999999",
            last_successful_step="push",
            cluster="https://kamiwaza.test/api",
            extension_name="my-app",
            last_registry="127.0.0.1:30010",
            last_push_registry="host.docker.internal:30010",
            last_build_engine="docker",
        )

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = info
        tagger = MagicMock()
        tagger.generate_tag.return_value = "0.1.0-dev-abc1234.1714999999"
        tagger.get_git_info.return_value = ("abc1234", False)
        pusher = MagicMock()  # push is resume-skipped → never called.

        # PayloadBuilder() is the first statement after the push branch; make
        # it raise a sentinel so we assert we reached it (i.e. the pre-flight
        # did NOT abort) without exercising the payload/apply machinery. If the
        # pre-flight regressed it would raise click.Exit instead, which this
        # ``pytest.raises(RuntimeError)`` would not swallow.
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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value="127.0.0.1:30010",
            ),
            # Force docker as the push engine and have docker reject the alias —
            # the pre-flight's remaining preconditions — so the only thing
            # keeping it from firing is the resume push-skip gating.
            patch(
                "kamiwaza_extensions.registry_resolution.select_push_engine",
                return_value="docker",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=False,
            ),
            patch(
                "kamiwaza_extensions.dev_state.read_state",
                return_value=prior_state,
            ),
            patch(
                "kamiwaza_extensions.dev_state.resume_message",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.payload_builder.PayloadBuilder",
                side_effect=RuntimeError("reached-payload-stage"),
            ),
            pytest.raises(RuntimeError, match="reached-payload-stage"),
        ):
            dev_cmd.run_dev_remote()

        pusher.push.assert_not_called()


_KZ_NS = "ghcr.io/kamiwaza-internal/kamiwaza-extensions-kaizen/images"
_KZ_DEV_REGISTRY = "host.docker.internal:5001"
_KZ_DEV_NS = f"{_KZ_DEV_REGISTRY}/kamiwaza-internal/kamiwaza-extensions-kaizen/images"


def _kaizen_info(tmp_path: Path) -> ExtensionInfo:
    """The exact shape from ENG-8626: four owned builds declaring a qualified
    GHCR namespace (one profile-gated) plus an external postgres."""
    compose_data = {
        "services": {
            "postgres": {
                "image": "ghcr.io/kamiwaza-internal/containers/images/postgres:v18.4",
            },
            "sandbox-controller": {
                "build": {"context": "."},
                "image": f"{_KZ_NS}/kaizen-controller:2.0.2",
                "environment": {
                    "AGENT_SERVER_IMAGE": f"{_KZ_NS}/kaizen-agent:2.0.2",
                    "SANDBOX_ALLOWED_IMAGE_PREFIXES": f"{_KZ_NS}/kaizen-agent",
                },
            },
            "agent": {
                "profiles": ["image-only"],
                "build": {"context": "."},
                "image": f"{_KZ_NS}/kaizen-agent:2.0.2",
            },
            "backend": {
                "build": {"context": "."},
                "image": f"{_KZ_NS}/kaizen-backend:2.0.2",
                "environment": {
                    "AGENT_SERVER_IMAGE": f"{_KZ_NS}/kaizen-agent:2.0.2",
                },
            },
            "frontend": {
                "build": {"context": "."},
                "image": f"{_KZ_NS}/kaizen-frontend:2.0.2",
            },
        },
    }
    return ExtensionInfo(
        path=tmp_path,
        name="kaizen",
        version="2.0.2",
        metadata={"name": "kaizen", "type": "app"},
        compose_path=tmp_path / "docker-compose.yml",
        compose_data=compose_data,
    )


class TestKaizenDevRegistryEndToEnd:
    """ENG-8626 acceptance: ``kz-ext dev`` performs no GHCR push, and the ref
    it builds is the ref it pushes is the ref it deploys."""

    @staticmethod
    def _run(tmp_path, *, no_build=False):
        """Drive run_dev_remote to the payload stage, capturing every image-ref
        surface on the way: builder, pusher, and the transformed compose that
        becomes the CR."""
        from kamiwaza_extensions.commands import dev as dev_cmd

        captured: dict = {}

        def _capture_build(**kwargs):
            captured["build"] = kwargs
            # Mirror ImageBuilder: build every build: service, at image_refs[svc]
            # when present, and return the refs actually tagged.
            return list(kwargs["image_refs"].values())

        builder = MagicMock()
        builder.build.side_effect = _capture_build

        pusher = MagicMock()

        class _StopAtPayload:
            """Capture the compose that actually becomes the CR.

            It must be read here, not off ``ComposeTransformer.transform``:
            dev.py resolves env placeholders and rewrites embedded image refs
            (AGENT_SERVER_IMAGE, the sandbox allowlist) *after* the transform,
            each producing a new dict. The last one is what the cluster gets.
            """

            def __init__(self, *a, **kw):
                pass

            @staticmethod
            def make_dev_name(*a, **kw):
                return "kaizen-dev"

            def build(self, **kwargs):
                captured["deployed"] = kwargs["transformed_compose"]
                raise RuntimeError("reached-payload-stage")

        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = _active_connection()
        conn_mgr.get_token.return_value = MagicMock(access_token="tok-abc")
        detector = MagicMock()
        detector.detect.return_value = _kaizen_info(tmp_path)
        tagger = MagicMock()
        tagger.generate_tag.return_value = "2.0.2-dev-abc.123"
        tagger.get_git_info.return_value = ("abc1234", False)

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
                "kamiwaza_extensions.image_pusher.ImagePusher",
                return_value=pusher,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value=_KZ_DEV_REGISTRY,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.docker_accepts_insecure_push_to",
                return_value=True,
            ),
            patch("kamiwaza_extensions.dev_state.read_state", return_value=None),
            patch("kamiwaza_extensions.dev_state.resume_message", return_value=None),
            patch(
                "kamiwaza_extensions.payload_builder.PayloadBuilder",
                _StopAtPayload,
            ),
            pytest.raises(RuntimeError, match="reached-payload-stage"),
        ):
            dev_cmd.run_dev_remote(no_build=no_build)

        captured["pushed"] = list(pusher.push.call_args.args[0])
        return captured

    def test_no_ghcr_push_and_all_owned_images_under_dev_registry(self, tmp_path):
        cap = self._run(tmp_path)

        pushed = cap["pushed"]
        # The headline acceptance criterion: nothing goes to GHCR.
        assert not any(ref.startswith("ghcr.io/") for ref in pushed), pushed
        assert all(ref.startswith(f"{_KZ_DEV_REGISTRY}/") for ref in pushed), pushed
        # All four owned images, one revision.
        assert sorted(pushed) == sorted(
            [
                f"{_KZ_DEV_NS}/kaizen-controller:2.0.2-dev-abc.123",
                f"{_KZ_DEV_NS}/kaizen-agent:2.0.2-dev-abc.123",
                f"{_KZ_DEV_NS}/kaizen-backend:2.0.2-dev-abc.123",
                f"{_KZ_DEV_NS}/kaizen-frontend:2.0.2-dev-abc.123",
            ]
        )

    def test_same_ref_reaches_builder_pusher_and_deployed_compose(self, tmp_path):
        cap = self._run(tmp_path)

        built = cap["build"]["image_refs"]
        deployed = {
            name: svc["image"]
            for name, svc in cap["deployed"]["services"].items()
            if name != "postgres"
        }
        # Builder and pusher agree.
        assert sorted(cap["pushed"]) == sorted(built.values())
        # ...and every deployed service pulls exactly the ref that was pushed.
        for name, ref in deployed.items():
            assert ref == built[name], name
            assert ref in cap["pushed"], name
        # The profiled agent is built and pushed but never deployed as a
        # service — the backend launches it dynamically.
        assert "agent" in built
        assert "agent" not in cap["deployed"]["services"]

    def test_external_image_never_retagged_or_pushed(self, tmp_path):
        cap = self._run(tmp_path)

        postgres = cap["deployed"]["services"]["postgres"]["image"]
        # No build: → not ours. Untouched, and never pushed anywhere.
        assert postgres == "ghcr.io/kamiwaza-internal/containers/images/postgres:v18.4"
        assert postgres not in cap["pushed"]

    def test_agent_server_image_and_allowlist_match_the_pushed_agent_ref(
        self, tmp_path
    ):
        cap = self._run(tmp_path)

        agent_ref = cap["build"]["image_refs"]["agent"]
        assert agent_ref in cap["pushed"]

        controller_env = cap["deployed"]["services"]["sandbox-controller"][
            "environment"
        ]
        # The sandbox pod is launched from this ref, and the controller gates
        # it against the allowlist — both must name the image we actually
        # pushed, or the sandbox ImagePullBackOffs / is rejected.
        assert controller_env["AGENT_SERVER_IMAGE"] == agent_ref
        prefixes = controller_env["SANDBOX_ALLOWED_IMAGE_PREFIXES"].split(",")
        assert any(agent_ref.startswith(p) for p in prefixes), (
            prefixes,
            agent_ref,
        )

    def test_no_build_does_not_push_the_profiled_agent(self, tmp_path):
        # ENG-7110 behavior that must survive ENG-8626: profiled services are
        # in the dev canonical map now, but --no-build must still not push them
        # — a resumed run may no longer hold the local image. They rely on a
        # prior run's push.
        cap = self._run(tmp_path, no_build=True)

        pushed = cap["pushed"]
        assert not any("kaizen-agent" in ref for ref in pushed), pushed
        assert sorted(pushed) == sorted(
            [
                f"{_KZ_DEV_NS}/kaizen-controller:2.0.2-dev-abc.123",
                f"{_KZ_DEV_NS}/kaizen-backend:2.0.2-dev-abc.123",
                f"{_KZ_DEV_NS}/kaizen-frontend:2.0.2-dev-abc.123",
            ]
        )


class TestResumeProbesDevRegistry:
    """ENG-8626: the resume probe must look for artifacts where dev actually
    pushes them.

    ``_prior_artifacts_in_registry`` decides whether a prior run's images are
    still in the registry (and so whether the build/push can be skipped). It
    shares ``compute_canonical_refs``, so before the fix it probed **ghcr.io**
    for a locally-built dev image. That both asked the wrong registry and made
    resume validity depend on an external one."""

    def test_probe_targets_dev_registry_not_declared_namespace(self, tmp_path):
        from kamiwaza_extensions.commands import dev as dev_cmd

        probed: list[str] = []

        with patch(
            "kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest",
            side_effect=lambda ref: probed.append(ref) or "sha256:" + "a" * 64,
        ):
            ok = dev_cmd._prior_artifacts_in_registry(
                _kaizen_info(tmp_path),
                "2.0.2-dev-old.1",
                registry=_KZ_DEV_REGISTRY,
                push_registry=_KZ_DEV_REGISTRY,
            )

        assert ok is True
        assert probed, "probe must actually resolve the prior refs"
        assert not any(ref.startswith("ghcr.io/") for ref in probed), probed
        assert all(ref.startswith(f"{_KZ_DEV_REGISTRY}/") for ref in probed), probed
        # Profiled agent included: a full prior run pushed it, so resume must
        # confirm it is still there before skipping the rebuild that would
        # otherwise re-create it.
        assert f"{_KZ_DEV_NS}/kaizen-agent:2.0.2-dev-old.1" in probed

    def test_probe_fails_when_prior_artifacts_are_absent(self, tmp_path):
        """A dev-state written under the old GHCR policy names refs that were
        never pushed to the dev registry, so the probe misses and the run
        correctly falls back to a full rebuild rather than resuming onto
        images the cluster can't pull."""
        from kamiwaza_extensions.commands import dev as dev_cmd

        with patch(
            "kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest",
            side_effect=RuntimeError("manifest unknown"),
        ):
            ok = dev_cmd._prior_artifacts_in_registry(
                _kaizen_info(tmp_path),
                "2.0.2-dev-old.1",
                registry=_KZ_DEV_REGISTRY,
                push_registry=_KZ_DEV_REGISTRY,
            )

        assert ok is False
