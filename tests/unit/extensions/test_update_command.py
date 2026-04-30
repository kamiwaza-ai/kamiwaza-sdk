"""UpdateCommand unit tests.

Issue: ENG-3890 / D210 M2 / Tasks T2.2..T2.6.
Scenarios: TS-M2-1..11.

These tests use the live ``Scaffolder`` to generate a synthetic scaffold
in a tmp dir, then exercise ``run_update`` against it. The scaffolder
already stamps ``template_version`` + ``template_shape`` (T2.3), so most
modes operate on a freshly-rendered scaffold and assert the expected
no-change / change behavior.

Replay tests against historical template snapshots (TS-M2-13/14) live in
``tests/integration/test_template_replay.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from kamiwaza_extensions.commands.update import run_update
from kamiwaza_extensions.exit_codes import ExitCode
from kamiwaza_extensions.scaffolder import Scaffolder
from kamiwaza_extensions.template_manifest import current_template_version


def _make_scaffold(tmp_path: Path, monkeypatch, type_: str = "tool", name: str = "my") -> Path:
    """Render a fresh scaffold rooted at ``tmp_path``; return its directory."""
    monkeypatch.chdir(tmp_path)
    scaffolder = Scaffolder()
    with patch("subprocess.run"):
        return scaffolder.create(type_=type_, name=name)


# ---------------------------------------------------------------------------
# Happy-path: no-change reconciliation across all 3 shapes (TS-M2-1..3 setup
# + the freshly-rendered case where update should be a no-op).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["app", "tool", "service"])
def test_dry_run_on_fresh_scaffold_reports_no_changes(shape: str, tmp_path, monkeypatch):
    """TS-M2-1, TS-M2-2, TS-M2-3 — dry-run on each shape."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_=shape)
    monkeypatch.chdir(scaffold)
    summary = run_update(dry_run=True)
    # A freshly-rendered scaffold matches the template exactly → nothing
    # to update on a dry-run.
    assert summary.updated == 0
    assert summary.conflicts == 0
    # Most files report no-change; some may be reported as "would-update"
    # only if the scaffolder context differs from update's reconstructed
    # context. We don't assert on individual file actions to avoid coupling.
    assert all(fr.action != "updated" for fr in summary.files)


@pytest.mark.parametrize("shape", ["app", "tool", "service"])
def test_update_on_fresh_scaffold_makes_no_changes(shape: str, tmp_path, monkeypatch):
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_=shape)
    monkeypatch.chdir(scaffold)
    summary = run_update()
    # Freshly-rendered scaffold has no conflicts and reports zero updates.
    assert summary.conflicts == 0
    assert summary.updated == 0


# ---------------------------------------------------------------------------
# Author modification → conflict path (TS-M2-4..6).
# ---------------------------------------------------------------------------


def test_default_interactive_prompts_on_conflict(tmp_path, monkeypatch):
    """TS-M2-4 — interactive default prompts on author-modified file."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    target = scaffold / "src" / "server.py"
    target.write_text("# my edits\n")
    monkeypatch.chdir(scaffold)

    with patch("typer.prompt", return_value="k") as mock_prompt:
        summary = run_update()

    # The diff prompt fired at least once.
    assert mock_prompt.call_count >= 1
    # We answered "keep" → file remains modified.
    assert target.read_text() == "# my edits\n"
    # And `update` records that the conflict was kept rather than applied.
    server_result = next(fr for fr in summary.files if fr.relative_path == "src/server.py")
    assert server_result.action == "kept"


def test_force_overwrites_with_orig_backup(tmp_path, monkeypatch):
    """TS-M2-5 — --force applies updates and writes .orig."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    target = scaffold / "src" / "server.py"
    original = target.read_text()
    target.write_text("# my custom server\n")
    monkeypatch.chdir(scaffold)

    summary = run_update(force=True)

    assert target.read_text() == original
    backup = target.with_suffix(target.suffix + ".orig")
    assert backup.exists() and backup.read_text() == "# my custom server\n"
    server_result = next(fr for fr in summary.files if fr.relative_path == "src/server.py")
    assert server_result.action == "applied"


def test_non_interactive_exits_validation_on_conflict(tmp_path, monkeypatch):
    """TS-M2-6 / PR-86 C3 — --non-interactive exits non-zero (VALIDATION)
    when any conflict is detected. The design contract is CI-fails-loudly
    rather than silently producing a partial update."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    target = scaffold / "src" / "server.py"
    target.write_text("# CI-modified\n")
    monkeypatch.chdir(scaffold)

    with pytest.raises(typer.Exit) as exc:
        run_update(non_interactive=True)

    assert exc.value.exit_code == int(ExitCode.VALIDATION)
    # File untouched on the way out.
    assert target.read_text() == "# CI-modified\n"


def test_non_interactive_succeeds_when_no_conflicts(tmp_path, monkeypatch):
    """PR-86 C3 — --non-interactive does not exit non-zero on a clean
    scaffold; only conflicts trigger the failure."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    monkeypatch.chdir(scaffold)
    summary = run_update(non_interactive=True)
    assert summary.conflicts == 0


# ---------------------------------------------------------------------------
# Bootstrap path (TS-M2-7) and missing-version error (TS-M2-8).
# ---------------------------------------------------------------------------


def test_bootstrap_stamps_version_without_overwriting(tmp_path, monkeypatch):
    """TS-M2-7 — --bootstrap records version; touches nothing else."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    # Strip template_version + template_shape to simulate an old scaffold.
    meta_path = scaffold / "kamiwaza.json"
    meta = json.loads(meta_path.read_text())
    meta.pop("template_version", None)
    meta.pop("template_shape", None)
    meta_path.write_text(json.dumps(meta, indent=4) + "\n")

    # Author-modified file that bootstrap must NOT touch.
    target = scaffold / "src" / "server.py"
    target.write_text("# my custom server\n")
    monkeypatch.chdir(scaffold)

    summary = run_update(bootstrap=True)

    refreshed = json.loads(meta_path.read_text())
    assert refreshed.get("template_version") == current_template_version()
    assert refreshed.get("template_shape") == "tool"
    assert target.read_text() == "# my custom server\n"
    # Bootstrap reports just the kamiwaza.json action.
    assert any(fr.action == "bootstrap" for fr in summary.files)


def test_missing_version_without_bootstrap_errors_with_validation_exit(tmp_path, monkeypatch):
    """TS-M2-8 — without --bootstrap the missing-version error is exit 2."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    meta_path = scaffold / "kamiwaza.json"
    meta = json.loads(meta_path.read_text())
    meta.pop("template_version", None)
    meta_path.write_text(json.dumps(meta, indent=4) + "\n")
    monkeypatch.chdir(scaffold)

    with pytest.raises(typer.Exit) as exc:
        run_update()
    assert exc.value.exit_code == int(ExitCode.VALIDATION)


# ---------------------------------------------------------------------------
# Migration path (TS-M2-9) — MANIFESTS register no migrations in v1, so we
# directly inject one to verify the algorithm runs in order.
# ---------------------------------------------------------------------------


def test_migrations_apply_before_diff(tmp_path, monkeypatch):
    """TS-M2-9 — TemplateMigration entries run in version order, before diff."""
    from kamiwaza_extensions import template_manifest as tm

    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")

    # Create a "stale" path on disk that's about to be migrated away.
    legacy = scaffold / "src" / "old_server.py"
    legacy.write_text("# legacy\n")
    monkeypatch.chdir(scaffold)

    # Patch the tool manifest to inject a migration old_server.py → new_server.py.
    original = tm.MANIFESTS["tool"]
    migration = tm.TemplateMigration(
        old_path="src/old_server.py",
        new_path="src/new_server.py",
        since_version=current_template_version(),
    )
    patched = tm.TemplateManifest(
        shape=original.shape,
        template_version=original.template_version,
        files=original.files,
        migrations=(migration,),
    )
    monkeypatch.setitem(tm.MANIFESTS, "tool", patched)

    summary = run_update(non_interactive=True)

    new = scaffold / "src" / "new_server.py"
    assert new.exists() and new.read_text() == "# legacy\n"
    assert not legacy.exists()
    assert any("old_server.py" in m and "new_server.py" in m for m in summary.migrations)


# ---------------------------------------------------------------------------
# template_version is rewritten after a successful reconcile (TS-M2-10).
# ---------------------------------------------------------------------------


def test_backup_filename_preserves_full_extension(tmp_path, monkeypatch):
    """Review iteration-1 I12: ``.orig`` is appended to the full filename, not
    used to replace the trailing extension. ``next.config.js`` must back up to
    ``next.config.js.orig``, not ``next.config.orig``."""
    from kamiwaza_extensions.commands.update import _backup

    target = tmp_path / "next.config.js"
    target.write_text("module.exports = {};\n")
    _backup(target, "module.exports = {};\n")
    assert (tmp_path / "next.config.js.orig").exists()
    assert not (tmp_path / "next.config.orig").exists()


def test_stamp_version_preserves_merge_added_kamiwaza_fields(tmp_path, monkeypatch):
    """PR-86 review C1 / M8: when _reconcile_json_merge writes new fields to
    kamiwaza.json, _stamp_version's subsequent rewrite must not clobber them.

    Pinning the in-memory ``metadata`` view (loaded pre-reconcile) was the bug;
    fix re-reads from disk before stamping.
    """
    from kamiwaza_extensions import template_manifest as tm

    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    meta_path = scaffold / "kamiwaza.json"
    # Simulate a scaffold whose recorded version is older than the manifest.
    meta = json.loads(meta_path.read_text())
    meta["template_version"] = "0.0.1-old"
    meta_path.write_text(json.dumps(meta, indent=4) + "\n")

    # Simulate a template change that adds a new kamiwaza.json field by
    # stubbing _reconcile_json_merge to inject one. This lets us isolate
    # the C1 regression test from the bundled template's actual content.
    from kamiwaza_extensions.commands import update as upd

    real_merge = upd._reconcile_json_merge

    def merge_with_field(*, rel, target_path, existing_content, new_content, dry_run):
        if rel == "kamiwaza.json" and not dry_run:
            existing = json.loads(existing_content)
            existing["new_template_field"] = "from-merge"
            target_path.write_text(json.dumps(existing, indent=4) + "\n")
            return upd.FileResult(rel, "updated", "json-merge")
        return real_merge(
            rel=rel,
            target_path=target_path,
            existing_content=existing_content,
            new_content=new_content,
            dry_run=dry_run,
        )

    monkeypatch.setattr(upd, "_reconcile_json_merge", merge_with_field)

    # Force a manifest version that differs from recorded so _stamp_version
    # actually fires.
    original = tm.MANIFESTS["tool"]
    bumped = tm.TemplateManifest(
        shape=original.shape,
        template_version="9.9.9-test",
        files=original.files,
        migrations=original.migrations,
    )
    monkeypatch.setitem(tm.MANIFESTS, "tool", bumped)

    monkeypatch.chdir(scaffold)
    upd.run_update(non_interactive=True)

    refreshed = json.loads(meta_path.read_text())
    assert refreshed.get("new_template_field") == "from-merge", (
        "merge-added field was clobbered by _stamp_version (PR-86 C1 regression)"
    )
    assert refreshed.get("template_version") == "9.9.9-test"


def test_update_rewrites_template_version_after_success(tmp_path, monkeypatch):
    """TS-M2-10 — kamiwaza.json's template_version is bumped to current."""
    from kamiwaza_extensions import template_manifest as tm

    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    meta_path = scaffold / "kamiwaza.json"
    meta = json.loads(meta_path.read_text())
    meta["template_version"] = "0.10.0-rc1"
    meta_path.write_text(json.dumps(meta, indent=4) + "\n")
    monkeypatch.chdir(scaffold)

    # Force the manifest to think it's at a higher version so an update
    # actually rewrites the kamiwaza.json field.
    original = tm.MANIFESTS["tool"]
    bumped = tm.TemplateManifest(
        shape=original.shape,
        template_version="0.99.0",
        files=original.files,
        migrations=original.migrations,
    )
    monkeypatch.setitem(tm.MANIFESTS, "tool", bumped)

    run_update(non_interactive=True)

    refreshed = json.loads(meta_path.read_text())
    assert refreshed["template_version"] == "0.99.0"
