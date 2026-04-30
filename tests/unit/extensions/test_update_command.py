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


def test_migrations_apply_in_version_order_regardless_of_tuple_order(tmp_path, monkeypatch):
    """Round-3 H3: migrations are sorted by since_version even if the
    manifest tuple lists them out of order. Without the sort, a manifest
    where the v0.2 migration is declared before the v0.1 migration would
    apply v0.2's rename first — silently breaking projects on v0.1."""
    from kamiwaza_extensions import template_manifest as tm
    from kamiwaza_extensions.commands import update as upd

    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    # Stage two files matching the OLD paths for both migrations.
    (scaffold / "src" / "a_v01.py").write_text("# v0.1 file")
    (scaffold / "src" / "b_v02.py").write_text("# v0.2 file")
    monkeypatch.chdir(scaffold)

    # Inject migrations in REVERSE version order (v0.2 first, then v0.1).
    original = tm.MANIFESTS["tool"]
    later = tm.TemplateMigration(
        old_path="src/b_v02.py", new_path="src/b_renamed.py", since_version="0.2.0"
    )
    earlier = tm.TemplateMigration(
        old_path="src/a_v01.py", new_path="src/a_renamed.py", since_version="0.1.0"
    )
    patched = tm.TemplateManifest(
        shape=original.shape,
        template_version=original.template_version,
        files=original.files,
        migrations=(later, earlier),  # intentionally reversed
    )
    monkeypatch.setitem(tm.MANIFESTS, "tool", patched)

    summary = upd.run_update(non_interactive=True)

    # Both renames happened.
    assert (scaffold / "src" / "a_renamed.py").exists()
    assert (scaffold / "src" / "b_renamed.py").exists()
    # Order in summary.migrations reflects sorted (semver) order:
    # a_v01.py (since 0.1) before b_v02.py (since 0.2).
    a_idx = next(
        i for i, m in enumerate(summary.migrations) if "a_v01.py" in m
    )
    b_idx = next(
        i for i, m in enumerate(summary.migrations) if "b_v02.py" in m
    )
    assert a_idx < b_idx, (
        f"migrations applied in tuple order, not version order: {summary.migrations}"
    )


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


def test_scaffolder_records_template_file_hashes(tmp_path, monkeypatch):
    """PR-86 C4 / option (b): scaffolder stamps a content hash for every
    preserve_if_modified template file into kamiwaza.json. These hashes
    are what `kz-ext update` consults to detect "clean since scaffold"
    on the next CLI bump."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    meta = json.loads((scaffold / "kamiwaza.json").read_text())
    hashes = meta.get("template_file_hashes")
    assert isinstance(hashes, dict) and hashes, (
        "scaffolder must populate kamiwaza.json.template_file_hashes — "
        "without it, kz-ext update cannot detect clean-since-create files"
    )
    # tool shape's preserve_if_modified files include kamiwaza.json (merge)
    # is excluded since merge has its own reconciliation; src/server.py is
    # the canonical preserve_if_modified file in that shape.
    assert "src/server.py" in hashes
    assert hashes["src/server.py"].startswith("sha256:")


def test_clean_file_auto_updates_on_template_change(tmp_path, monkeypatch):
    """PR-86 C4: a preserve_if_modified file that matches the recorded
    hash (i.e. the author hasn't touched it) auto-updates on a CLI bump
    instead of conflict-prompting. This is the headline behavior the
    `preserve_if_modified` strategy is supposed to deliver."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    target = scaffold / "src" / "server.py"
    monkeypatch.chdir(scaffold)

    # Simulate a CLI bump: monkeypatch _render so the "new" template render
    # for src/server.py differs from what the scaffolder originally wrote.
    from kamiwaza_extensions.commands import update as upd

    real_render = upd._render

    def render_with_new_line(template_path, context):
        text = real_render(template_path, context)
        if str(template_path).endswith("src/server.py"):
            return text + "\n# v0.2 — added comment line\n"
        return text

    monkeypatch.setattr(upd, "_render", render_with_new_line)

    summary = upd.run_update(non_interactive=True)
    server_result = next(
        fr for fr in summary.files if fr.relative_path == "src/server.py"
    )
    # Auto-updated, no conflict (new behavior). Reason mentions "clean".
    assert server_result.action == "updated"
    assert "clean" in server_result.reason.lower()
    # File on disk now has the new line.
    assert "# v0.2 — added comment line" in target.read_text()
    # Recorded hash was refreshed.
    refreshed_meta = json.loads((scaffold / "kamiwaza.json").read_text())
    new_recorded = refreshed_meta["template_file_hashes"]["src/server.py"]
    import hashlib
    expected = "sha256:" + hashlib.sha256(target.read_text().encode()).hexdigest()
    assert new_recorded == expected


def test_modified_file_conflicts_on_template_change(tmp_path, monkeypatch):
    """PR-86 C4: a preserve_if_modified file whose on-disk hash diverges
    from the recorded hash is a real conflict — the author edited it.
    Existing --non-interactive (now exits on conflict per C3) still fires."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    target = scaffold / "src" / "server.py"
    target.write_text("# my edits\n")
    monkeypatch.chdir(scaffold)
    with pytest.raises(typer.Exit):
        run_update(non_interactive=True)
    # File untouched.
    assert target.read_text() == "# my edits\n"


def test_non_interactive_failure_does_not_partially_write(tmp_path, monkeypatch):
    """PR-86 round-2 C2: a failing --non-interactive update must not leave
    the scaffold in a partially-updated state. Specifically:
      * kamiwaza.json.template_version must NOT have been bumped
      * clean preserve_if_modified files (not the conflicting one) must
        NOT have been re-written

    The previous implementation ran the full _reconcile (writing files +
    bumping version) before checking conflicts, leaving silent corruption.
    """
    from kamiwaza_extensions import template_manifest as tm

    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    meta_path = scaffold / "kamiwaza.json"
    pre_meta_text = meta_path.read_text()
    pre_meta = json.loads(pre_meta_text)
    pre_version = pre_meta["template_version"]

    # File 1: untouched (would auto-update via clean-since-record path).
    clean_target = scaffold / "src" / "server.py"
    # File 2: conflicting (author edited).
    # Tool template only has src/server.py as preserve_if_modified, so
    # conflict it via author-edit. Use README.md (also preserve_if_modified)
    # for the would-be-clean target.
    readme = scaffold / "README.md"
    readme_pre_text = readme.read_text()

    # Edit src/server.py to create a conflict.
    clean_target.write_text("# my edits — conflict\n")

    # Now bump the manifest's template_version so a "clean" file would
    # otherwise get its hash refreshed (which would still leave evidence
    # of writes even if version-bump alone is preserved).
    original = tm.MANIFESTS["tool"]
    bumped = tm.TemplateManifest(
        shape=original.shape,
        template_version="9.9.9-test",
        files=original.files,
        migrations=original.migrations,
    )
    monkeypatch.setitem(tm.MANIFESTS, "tool", bumped)

    monkeypatch.chdir(scaffold)
    with pytest.raises(typer.Exit):
        run_update(non_interactive=True)

    # Post-failure invariants:
    # 1. kamiwaza.json's template_version is unchanged (NOT 9.9.9-test).
    refreshed_meta = json.loads(meta_path.read_text())
    assert refreshed_meta["template_version"] == pre_version, (
        "non-interactive failure must not have bumped template_version"
    )
    # 2. README.md (would-be-clean) was NOT rewritten.
    assert readme.read_text() == readme_pre_text, (
        "non-interactive failure must not write any files, including "
        "would-be-clean preserve_if_modified files"
    )
    # 3. The author's edit on src/server.py is preserved (sanity).
    assert clean_target.read_text() == "# my edits — conflict\n"


def test_bootstrap_records_hashes_from_on_disk_content(tmp_path, monkeypatch):
    """PR-86 C4: --bootstrap stamps hashes from the *current* on-disk
    content (the user is adopting whatever they have as the baseline).
    This lets old scaffolds opt into hash-aware updates."""
    scaffold = _make_scaffold(tmp_path, monkeypatch, type_="tool")
    # Strip both template_version and template_file_hashes to simulate an
    # old scaffold that predates the hash mechanism.
    meta_path = scaffold / "kamiwaza.json"
    meta = json.loads(meta_path.read_text())
    meta.pop("template_version", None)
    meta.pop("template_file_hashes", None)
    meta_path.write_text(json.dumps(meta, indent=4) + "\n")

    # Author has customized the file pre-bootstrap.
    target = scaffold / "src" / "server.py"
    target.write_text("# bespoke\n")
    monkeypatch.chdir(scaffold)
    run_update(bootstrap=True)

    refreshed = json.loads(meta_path.read_text())
    hashes = refreshed["template_file_hashes"]
    import hashlib
    expected = "sha256:" + hashlib.sha256(b"# bespoke\n").hexdigest()
    assert hashes["src/server.py"] == expected, (
        "bootstrap must hash on-disk content (the user's actual baseline), "
        "not a freshly-rendered template"
    )


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
