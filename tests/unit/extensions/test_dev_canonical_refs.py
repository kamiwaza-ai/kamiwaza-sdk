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

        monkeypatch.setenv("KAMIWAZA_PUSH_REGISTRY", "host.containers.internal:30010")
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
