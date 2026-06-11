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
