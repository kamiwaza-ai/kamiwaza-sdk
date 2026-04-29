"""CLI polish bundle tests — §4.8 P1/P3/P7/P8/B2 (ENG-3898).

Each papercut from Preston's 0.12.1 walkthrough has a behavior contract
this file pins. The doc-only items (P3, P7, B2) are checked by file
presence + section-anchor invariants; the behavioral items (P1, P8) are
checked by exercising the scaffolder and config-command code paths.

Test scenarios: TS-M2-43..46.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kamiwaza_extensions.scaffolder import Scaffolder

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# P1 — `kz-ext create --name foo` creates `foo/` and scaffolds inside it
# when the cwd is non-empty (TS-M2-43, TS-M2-44).
# ---------------------------------------------------------------------------

class TestCreateIntoNamedSubdir:
    @pytest.fixture
    def scaffolder(self) -> Scaffolder:
        return Scaffolder()

    def test_create_in_non_empty_cwd_creates_named_subdir(
        self, tmp_path, monkeypatch, scaffolder
    ):
        # Non-empty cwd (a workspace root, README.md, .git, etc.).
        (tmp_path / "README.md").write_text("# my workspace\n")
        monkeypatch.chdir(tmp_path)

        with patch("subprocess.run"):
            target = scaffolder.create(type_="tool", name="my-tool")

        # New behavior: target is cwd / sanitized_name (tool prefix auto-applied).
        expected = tmp_path / "tool-my-tool"
        assert target == expected
        assert (expected / "kamiwaza.json").exists()
        meta = json.loads((expected / "kamiwaza.json").read_text())
        assert meta["name"] == "tool-my-tool"

    def test_create_in_non_empty_cwd_errors_when_subdir_exists_and_nonempty(
        self, tmp_path, monkeypatch, scaffolder
    ):
        (tmp_path / "README.md").write_text("# workspace\n")
        # Pre-existing target with content — must not silently overwrite.
        target_dir = tmp_path / "tool-foo"
        target_dir.mkdir()
        (target_dir / "leftover.py").write_text("# something")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(FileExistsError):
            scaffolder.create(type_="tool", name="foo")

    def test_create_in_empty_cwd_still_scaffolds_into_cwd(
        self, tmp_path, monkeypatch, scaffolder
    ):
        # Backward-compat preserved: empty cwd → scaffold into cwd, not subdir.
        monkeypatch.chdir(tmp_path)

        with patch("subprocess.run"):
            target = scaffolder.create(type_="service", name="my-svc")

        # Empty cwd path: target IS cwd, not cwd/name.
        assert target == tmp_path
        assert (tmp_path / "kamiwaza.json").exists()
        # No "service-my-svc" subdir was created.
        assert not (tmp_path / "service-my-svc").exists()


# ---------------------------------------------------------------------------
# P8 — `kz-ext config publish-profile list` works as a subcommand-style
# invocation (TS-M2-45). The deprecated `--list` / `-l` form continues to
# work for one release.
# ---------------------------------------------------------------------------

class TestPublishProfileListSubcommand:
    def test_list_as_first_argument_routes_to_list(self, tmp_path, monkeypatch, capsys):
        from kamiwaza_extensions.commands import config as config_cmd

        # Empty home → empty profile list. We just verify the function
        # didn't try to *create* a profile named "list" (which would fail
        # missing-required-field).
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))

        # Pass name="list" and no update flags — should be treated as `--list`.
        config_cmd.publish_profile(name="list")
        # Not the "Profile name is required" error path (would have raised typer.Exit).
        # No-profiles message is acceptable.
        captured = capsys.readouterr()
        assert "Profile name is required" not in (captured.out + captured.err)

    def test_explicit_list_flag_still_works(self, tmp_path, monkeypatch, capsys):
        from kamiwaza_extensions.commands import config as config_cmd

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))

        config_cmd.publish_profile(list_profiles=True)
        captured = capsys.readouterr()
        assert "Profile name is required" not in (captured.out + captured.err)


# ---------------------------------------------------------------------------
# P3, P7, B2 — Developer Guide gains a "What runs where" section, a
# `kz-ext dev local` no-hot-reload note, and an extension-author-targeted
# `.ai/extensions/` redirect (TS-M2-46).
# ---------------------------------------------------------------------------

DEV_GUIDE_PATH = REPO_ROOT / "docs" / "extensions" / "developer-guide.md"


class TestDeveloperGuide:
    @pytest.fixture(scope="class")
    def text(self) -> str:
        assert DEV_GUIDE_PATH.exists(), (
            f"Expected developer guide at {DEV_GUIDE_PATH.relative_to(REPO_ROOT)}"
        )
        return DEV_GUIDE_PATH.read_text()

    def test_has_what_runs_where_section(self, text):
        # B2 — clarifies CRD model vs. App Garden catalog mental model.
        assert "What runs where" in text, (
            "Developer Guide must include a 'What runs where' section "
            "(B2 §4.8) mapping kz-ext create|dev|publish onto the runtime "
            "surfaces they populate."
        )

    def test_has_dev_local_no_hot_reload_note(self, text):
        # P3 — current behavior: kz-ext dev local uses next build && next start.
        assert "next build" in text and "next start" in text, (
            "Developer Guide must explicitly document that `kz-ext dev local` "
            "uses `next build && next start` (P3 §4.8) — there is no hot-reload."
        )

    def test_has_ai_extensions_redirect(self, text):
        # P7 — extension authors should land somewhere relevant when they
        # look at .ai/extensions/.
        assert ".ai/extensions" in text, (
            "Developer Guide must mention .ai/extensions/ (P7 §4.8) as the "
            "author-facing AI-context entry point."
        )
