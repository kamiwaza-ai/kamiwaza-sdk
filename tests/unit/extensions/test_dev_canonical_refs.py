"""Tests that ``run_dev_remote`` passes canonical image refs to the builder.

The build/push refs in dev.py must match the namespace declared in
compose, the same way ``ComposeTransformer`` rewrites them. Without
``image_refs=`` plumbed through to ``ImageBuilder.build``, the builder
defaults to the legacy ``{registry}/{ext}-{svc}:{tag}`` form while the
K8s payload (built from the transformed compose) references the
declared namespace — extensions that publish under a non-conventional
path (e.g. ``ghcr.io/.../tool-omniparse/omniparse``) hit
ImagePullBackOff because the image lives at a different path than the
pod pulls from.
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
    """``ImageBuilder.build`` must receive ``image_refs`` that honor the
    compose-declared namespace — same source-of-truth as the K8s
    payload's image refs."""

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
            patch.object(
                dev_cmd,
                "_detect_kind_registry",
                return_value="registry.kamiwaza.test",
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
            "matches the transformed compose's declared namespace."
        )
        # Tag rewritten, namespace preserved verbatim.
        assert captured["image_refs"] == {
            "omniparse": "ghcr.io/example/tool-omniparse/omniparse:0.1.0-dev-abc.123",
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
            patch.object(
                dev_cmd,
                "_detect_kind_registry",
                return_value="127.0.0.1:30010",
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value=None,
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
            patch.object(
                dev_cmd, "_detect_kind_registry", return_value="127.0.0.1:30010"
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
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
            patch.object(
                dev_cmd, "_detect_kind_registry", return_value="127.0.0.1:30010"
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value=None,
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.build_engine_runs_in_vm",
                return_value=False,
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
        spy_select.assert_called_once_with(insecure=True)

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
            patch.object(
                dev_cmd, "_detect_kind_registry", return_value="127.0.0.1:30010"
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value=None,
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

    def test_insecure_preflight_skips_user_supplied_push_registry(
        self, tmp_path, monkeypatch
    ):
        """A dev-host connection can be insecure while the explicit push
        registry is a normal HTTPS registry. The Docker insecure-registry
        preflight is only for the auto loopback alias, not every split
        registry."""

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
                return_value=False,
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

    def test_insecure_preflight_skips_unused_loopback_alias_for_external_refs(
        self, tmp_path, monkeypatch
    ):
        """Declared external build refs do not retag to the local VM alias.

        Registry resolution may still find an auto loopback alias for fallback
        refs, but the Docker insecure-registry preflight should only require
        daemon config when at least one actual push ref maps to that alias.
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

        mock_accepts.assert_not_called()
        pusher.push.assert_called_once()
        assert pusher.push.call_args.args[0] == ["ghcr.io/example/custom-api:dev1"]
        assert pusher.push.call_args.kwargs["registry"] == "host.docker.internal:30010"
        assert pusher.push.call_args.kwargs["target_refs"] == {}

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
            patch.object(
                dev_cmd, "_detect_kind_registry", return_value="127.0.0.1:30010"
            ),
            patch(
                "kamiwaza_extensions.registry_resolution.detect_core_config_registry",
                return_value=None,
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
