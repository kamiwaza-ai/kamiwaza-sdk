"""Tests for the kz-ext dev catalog-overlay hook and --unload — ENG-6802."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from kamiwaza_extensions.commands.dev import _publish_catalog_overlay, run_dev_unload

pytestmark = pytest.mark.unit


def _info(tmp_path=None):
    return SimpleNamespace(
        path=Path(tmp_path) if tmp_path else Path("/tmp/ext"),
        name="kaizen",
        version="1.0.0",
        metadata={"display_name": "Kaizen"},
        image_basename=None,
    )


TRANSFORMED = {
    "services": {"app": {"image": "registry.kamiwaza.test/kaizen-app:1.0.0-dev-abc.5"}}
}
CANONICAL = {"app": "registry.kamiwaza.test/kaizen-app:1.0.0-dev-abc.5"}


def _publish(client, *, no_push=False, service_filter=None):
    return _publish_catalog_overlay(
        client,
        _info(),
        transformed=TRANSFORMED,
        canonical_refs=CANONICAL,
        registry="registry.kamiwaza.test",
        push_registry="127.0.0.1:30010",
        no_push=no_push,
        service_filter=service_filter,
    )


@pytest.fixture
def git_mocks():
    with patch(
        "kamiwaza_extensions.revision_tagger.RevisionTagger.get_git_info",
        return_value=("abc1234", False),
    ), patch(
        "kamiwaza_extensions.catalog_overlay.get_git_branch",
        return_value="feat-x",
    ), patch(
        "kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest",
        return_value="sha256:" + "a" * 64,
    ):
        yield


class TestPublishCatalogOverlay:
    def test_skips_on_no_push(self, capsys):
        client = MagicMock()
        _publish(client, no_push=True)
        client.put.assert_not_called()
        assert "Catalog overlay skipped" in capsys.readouterr().err

    def test_skips_on_service_filter(self, capsys):
        client = MagicMock()
        _publish(client, service_filter="app")
        client.put.assert_not_called()
        assert "--service" in capsys.readouterr().err

    def test_happy_path_publishes_and_prints(self, git_mocks, capsys):
        client = MagicMock()
        client.put.return_value = {
            "shadow": {"shadows_version": "1.0.0"},
            "running_deployments": ["kaizen-room-1", "kaizen-room-2"],
        }

        _publish(client)

        path, kwargs = client.put.call_args[0][0], client.put.call_args[1]
        assert path == "/apps/app_templates/catalog/overlay/kaizen"
        entry = kwargs["json"]
        assert entry["version"] == "1.0.0-dev.feat-x.abc1234"
        assert entry["shadow"]["git_branch"] == "feat-x"
        assert "@sha256:" in entry["compose_yml"]

        err = capsys.readouterr().err
        assert "Catalog overlay" in err
        assert "shadows 1.0.0" in err
        assert "2 running instances" in err
        assert "--unload" in err

    def test_old_platform_404_is_tolerated(self, git_mocks, capsys):
        from kamiwaza_sdk.exceptions import APIError

        client = MagicMock()
        client.put.side_effect = APIError("not found", status_code=404)

        _publish(client)  # must not raise

        assert "does not support catalog overlays" in capsys.readouterr().err

    def test_other_api_errors_warn_but_do_not_fail(self, git_mocks, capsys):
        from kamiwaza_sdk.exceptions import APIError

        client = MagicMock()
        client.put.side_effect = APIError("boom", status_code=500)

        _publish(client)  # must not raise

        err = capsys.readouterr().err
        assert "overlay write failed" in err
        assert "upstream catalog build" in err

    def test_unexpected_errors_warn_but_do_not_fail(self, git_mocks, capsys):
        client = MagicMock()
        client.put.side_effect = RuntimeError("connection reset")

        _publish(client)  # must not raise

        assert "overlay write failed" in capsys.readouterr().err

    def test_entry_construction_errors_warn_but_do_not_fail(self, capsys):
        # The non-fatal guarantee covers the WHOLE helper, not just the PUT:
        # a rollout that already succeeded must never be failed by overlay
        # bookkeeping.
        client = MagicMock()
        with patch(
            "kamiwaza_extensions.registry_resolution.build_push_ref_map",
            side_effect=RuntimeError("boom in entry construction"),
        ):
            _publish(client)  # must not raise

        err = capsys.readouterr().err
        assert "overlay write failed" in err
        client.put.assert_not_called()


class TestRunDevUnload:
    def _setup(self, monkeypatch, client):
        connection = SimpleNamespace(
            url="https://kamiwaza.test",
            name="dev",
            verify_ssl=True,
            effective_verify_ssl=lambda: True,
        )
        monkeypatch.setattr(
            "kamiwaza_extensions.extension_detector.ExtensionDetector.detect",
            lambda self: _info(),
        )
        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = connection
        conn_mgr.get_token.return_value = SimpleNamespace(access_token="tok")
        monkeypatch.setattr(
            "kamiwaza_extensions.connections.ConnectionManager",
            lambda: conn_mgr,
        )
        monkeypatch.setattr(
            "kamiwaza_sdk.KamiwazaClient", lambda **kwargs: client
        )

    def test_unload_restores_upstream(self, monkeypatch, capsys):
        client = MagicMock()
        client.delete.return_value = {
            "template_name": "kaizen",
            "restored_version": "1.0.0",
            "template_removed": False,
        }
        self._setup(monkeypatch, client)

        run_dev_unload()

        client.delete.assert_called_once_with(
            "/apps/app_templates/catalog/overlay/kaizen"
        )
        err = capsys.readouterr().err
        assert "Restored" in err and "1.0.0" in err
        assert "unaffected" in err

    def test_unload_removed_template(self, monkeypatch, capsys):
        client = MagicMock()
        client.delete.return_value = {
            "template_name": "kaizen",
            "restored_version": None,
            "template_removed": True,
        }
        self._setup(monkeypatch, client)

        run_dev_unload()

        assert "no upstream catalog entry" in capsys.readouterr().err

    def test_unload_without_overlay_exits_with_message(self, monkeypatch, capsys):
        from kamiwaza_sdk.exceptions import APIError

        client = MagicMock()
        client.delete.side_effect = APIError("missing", status_code=404)
        self._setup(monkeypatch, client)

        with pytest.raises(typer.Exit):
            run_dev_unload()

        assert "No catalog overlay exists" in capsys.readouterr().err


class TestResumeRevisionAdoption:
    """Resume must deploy the PRIOR run's tag — the freshly stamped epoch
    tag was never built/pushed (found live: ImagePullBackOff during
    ENG-6802 verification)."""

    def _state(self, step="poll", revision="2.2.0-dev-0b1fbba.100"):
        from kamiwaza_extensions.dev_state import DevState

        return DevState(last_revision=revision, last_successful_step=step)

    def test_adopts_prior_revision_when_push_complete(self):
        from kamiwaza_extensions.commands.dev import _resume_revision

        adopted = _resume_revision(
            self._state(), "2.2.0-dev-0b1fbba.200", resumable=True
        )
        assert adopted == "2.2.0-dev-0b1fbba.100"

    def test_adopts_when_only_build_complete(self):
        # The local image store holds the prior tag; the push must push it.
        from kamiwaza_extensions.commands.dev import _resume_revision

        adopted = _resume_revision(
            self._state(step="build"), "2.2.0-dev-0b1fbba.200", resumable=True
        )
        assert adopted == "2.2.0-dev-0b1fbba.100"

    def test_no_adoption_when_not_resumable(self):
        from kamiwaza_extensions.commands.dev import _resume_revision

        assert (
            _resume_revision(self._state(), "2.2.0-dev-0b1fbba.200", resumable=False)
            is None
        )

    def test_no_adoption_when_no_steps_complete(self):
        from kamiwaza_extensions.commands.dev import _resume_revision

        assert (
            _resume_revision(
                self._state(step=""), "2.2.0-dev-0b1fbba.200", resumable=True
            )
            is None
        )

    def test_no_adoption_when_tags_already_match(self):
        # Custom --revision: identical tags, nothing to adopt.
        from kamiwaza_extensions.commands.dev import _resume_revision

        assert (
            _resume_revision(
                self._state(revision="pinned-rev"), "pinned-rev", resumable=True
            )
            is None
        )

    def test_no_adoption_without_prior_state(self):
        from kamiwaza_extensions.commands.dev import _resume_revision

        assert _resume_revision(None, "2.2.0-dev-0b1fbba.200", resumable=True) is None


class TestPriorArtifactProbe:
    """Adoption is trust-but-verify: dev-state can carry a revision whose
    artifacts never reached the registry (pre-fix CLI state, --no-push
    apply). Found live: a poisoned state file kept redeploying an
    unpushed tag."""

    def _info(self, with_build=True):
        services = {"server": {"build": "."}} if with_build else {"db": {"image": "postgres:16"}}
        return SimpleNamespace(
            name="tool-omniparse",
            compose_data={"services": services},
            image_basename=None,
        )

    def test_true_when_all_digests_resolve(self):
        from kamiwaza_extensions.commands.dev import _prior_artifacts_in_registry

        with patch(
            "kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest",
            return_value="sha256:" + "a" * 64,
        ):
            assert _prior_artifacts_in_registry(
                self._info(),
                "2.2.0-dev-0b1fbba.100",
                registry="127.0.0.1:30010",
                push_registry="host.docker.internal:30010",
            )

    def test_false_when_manifest_missing(self):
        from kamiwaza_extensions.commands.dev import _prior_artifacts_in_registry
        from kamiwaza_extensions.image_pusher import ImagePushError

        with patch(
            "kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest",
            side_effect=ImagePushError("manifest unknown"),
        ):
            assert not _prior_artifacts_in_registry(
                self._info(),
                "2.2.0-dev-0b1fbba.100",
                registry="127.0.0.1:30010",
                push_registry="host.docker.internal:30010",
            )

    def test_true_when_nothing_buildable(self):
        from kamiwaza_extensions.commands.dev import _prior_artifacts_in_registry

        with patch(
            "kamiwaza_extensions.image_pusher.ImagePusher.resolve_digest",
            side_effect=AssertionError("must not be called"),
        ):
            assert _prior_artifacts_in_registry(
                self._info(with_build=False),
                "2.2.0-dev-0b1fbba.100",
                registry="127.0.0.1:30010",
                push_registry="host.docker.internal:30010",
            )


class TestStatusMissingInstanceShowsOverlay:
    def test_overlay_shown_when_extension_not_found(self, monkeypatch, capsys):
        # get_extension raises NotFoundError (NOT an APIError subclass) for
        # missing instances — the lingering-shadow display must still fire.
        from kamiwaza_sdk.exceptions import NotFoundError

        from kamiwaza_extensions.commands.status import run_status

        connection = SimpleNamespace(
            url="https://kamiwaza.test",
            name="dev",
            verify_ssl=True,
            effective_verify_ssl=lambda: True,
        )
        conn_mgr = MagicMock()
        conn_mgr.get_active_connection.return_value = connection
        conn_mgr.get_token.return_value = SimpleNamespace(
            access_token="x.eyJzdWIiOiJ1MSJ9.x"
        )
        monkeypatch.setattr(
            "kamiwaza_extensions.connections.ConnectionManager", lambda: conn_mgr
        )
        monkeypatch.setattr(
            "kamiwaza_extensions.extension_detector.ExtensionDetector.detect",
            lambda self: _info(),
        )
        monkeypatch.setattr(
            "kamiwaza_extensions.dev_state.read_state", lambda path: None
        )

        client = MagicMock()
        client.extensions.get_extension.side_effect = NotFoundError("gone")
        client.get.return_value = [
            {
                "template_name": "kaizen",
                "shadow_version": "1.0.0-dev.feat-x.abc1234",
                "git_sha": "abc1234",
                "shadows_version": "1.0.0",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        monkeypatch.setattr("kamiwaza_sdk.KamiwazaClient", lambda **kwargs: client)

        with pytest.raises(typer.Exit):
            run_status(name=None)

        err = capsys.readouterr().err
        assert "not found" in err
        assert "Catalog overlay" in err
        assert "abc1234" in err


class TestStatusOverlayRendering:
    def _shadow(self, **overrides):
        shadow = {
            "template_name": "kaizen",
            "shadow_version": "1.0.0-dev.feat-x.abc1234",
            "git_sha": "abc1234",
            "git_branch": "feat-x",
            "dirty": False,
            "shadows_version": "1.0.0",
            "upstream_exists": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        shadow.update(overrides)
        return shadow

    def test_renders_shadow_line(self, capsys):
        from kamiwaza_extensions.commands.status import _print_overlay_status

        client = MagicMock()
        client.get.return_value = [self._shadow()]

        _print_overlay_status(client, "kaizen")

        err = capsys.readouterr().err
        assert "abc1234" in err
        # rich may soft-wrap mid-phrase; assert the parts.
        assert "shadowing upstream" in err
        assert "1.0.0" in err
        assert "published today" in err
        assert "--unload" in err

    def test_staleness_nudge(self, capsys):
        from kamiwaza_extensions.commands.status import _print_overlay_status

        old = (datetime.now(timezone.utc) - timedelta(days=18)).isoformat()
        client = MagicMock()
        client.get.return_value = [self._shadow(updated_at=old)]

        _print_overlay_status(client, "kaizen")

        err = capsys.readouterr().err
        assert "published 18 days ago" in err
        assert "18 days old" in err

    def test_silent_without_shadow(self, capsys):
        from kamiwaza_extensions.commands.status import _print_overlay_status

        client = MagicMock()
        client.get.return_value = []

        _print_overlay_status(client, "kaizen")

        assert capsys.readouterr().err == ""

    def test_silent_on_old_platform(self, capsys):
        from kamiwaza_extensions.commands.status import _print_overlay_status

        client = MagicMock()
        client.get.side_effect = RuntimeError("404")

        _print_overlay_status(client, "kaizen")

        assert capsys.readouterr().err == ""
