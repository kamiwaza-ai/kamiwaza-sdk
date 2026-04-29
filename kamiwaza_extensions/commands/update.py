"""kz-ext update — reconcile a scaffold against the current template (ENG-3890).

The command opens an existing scaffolded extension (one with a
``kamiwaza.json`` and a ``template_shape`` recorded), looks up the matching
``TemplateManifest``, re-renders each template-owned file with the project's
current scaffold context, and applies a per-file strategy:

* ``overwrite`` — replace unconditionally; write a ``.orig`` backup if the
  on-disk copy differs from the new template.
* ``preserve_if_modified`` — replace only if the on-disk copy is identical
  to the *previous* template render (i.e. the author hasn't edited it). If
  it's been edited, skip; the unified diff is shown in interactive mode and
  the user can choose ``apply`` (with ``.orig`` backup) or ``keep``.
* ``merge`` — reserved for future smart-merge; v1 == ``preserve_if_modified``.

Modes:

* (default — interactive) prompts on each conflict.
* ``--dry-run`` — print planned changes only; no writes.
* ``--force`` — apply every template-owned update, write ``.orig`` on
  conflicts. Skips prompts.
* ``--non-interactive`` — fail with non-zero exit on the first conflict.
  CI-friendly.
* ``--bootstrap`` — when the scaffold has no recorded ``template_version``
  yet, treat the current state as baseline and stamp the version without
  touching any other files.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from kamiwaza_extensions import __version__
from kamiwaza_extensions.exit_codes import ExitCode
from kamiwaza_extensions.scaffolder import Scaffolder
from kamiwaza_extensions.template_manifest import (
    AUTHOR_OWNED_DENYLIST,
    MANIFESTS,
    TemplateManifest,
    TemplateOwnedFile,
    current_template_version,
    get_manifest,
)

console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Errors — surfaced via ExitCode.VALIDATION (2) per design §4.2.3.
# ---------------------------------------------------------------------------


class UpdateError(Exception):
    """Base class for update-command failures."""


class TemplateVersionMissing(UpdateError):
    """Scaffold has no template_version recorded; require --bootstrap."""


class TemplateShapeMismatch(UpdateError):
    """Scaffold's template_shape doesn't match its declared type."""


class UnsupportedMigrationPath(UpdateError):
    """Scaffold is at a version older than the oldest known migration."""


# ---------------------------------------------------------------------------
# Result types — tracked across files so the summary is precise.
# ---------------------------------------------------------------------------


@dataclass
class FileResult:
    relative_path: str
    action: str  # "updated", "skipped", "kept", "applied", "renamed", "no-change", "missing"
    reason: str = ""


@dataclass
class UpdateSummary:
    updated: int = 0
    conflicts: int = 0
    skipped: int = 0
    no_change: int = 0
    migrations: list[str] = None  # type: ignore[assignment]
    files: list[FileResult] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.migrations is None:
            self.migrations = []
        if self.files is None:
            self.files = []


# ---------------------------------------------------------------------------
# Top-level entry — called from cli.py.
# ---------------------------------------------------------------------------


def run_update(
    *,
    dry_run: bool = False,
    force: bool = False,
    non_interactive: bool = False,
    bootstrap: bool = False,
) -> UpdateSummary:
    """Reconcile the scaffold rooted at the current working directory."""
    cwd = Path.cwd()
    metadata_path = cwd / "kamiwaza.json"
    if not metadata_path.exists():
        console.print(
            "[red]Error:[/red] kamiwaza.json not found in current directory."
        )
        raise typer.Exit(code=int(ExitCode.VALIDATION))

    try:
        metadata = json.loads(metadata_path.read_text())
    except json.JSONDecodeError as exc:
        console.print(f"[red]Error:[/red] kamiwaza.json is not valid JSON: {exc}")
        raise typer.Exit(code=int(ExitCode.VALIDATION)) from exc

    template_shape = metadata.get("template_shape") or metadata.get("type")
    if template_shape not in MANIFESTS:
        console.print(
            f"[red]Error:[/red] kamiwaza.json declares template_shape/type "
            f"{template_shape!r}, which is not one of {sorted(MANIFESTS)}."
        )
        raise typer.Exit(code=int(ExitCode.VALIDATION))

    recorded_version = metadata.get("template_version")
    if not recorded_version:
        if not bootstrap:
            console.print(
                "[red]Error:[/red] No template_version recorded in kamiwaza.json. "
                "Run with [bold]--bootstrap[/bold] to adopt the current state as "
                "baseline (this stamps the version without overwriting any files)."
            )
            raise typer.Exit(code=int(ExitCode.VALIDATION))
        # Bootstrap path — stamp version + shape and exit.
        return _bootstrap(metadata_path, metadata, template_shape, dry_run=dry_run)

    if bootstrap:
        # Already bootstrapped — bootstrap is only for first-time adoption.
        console.print(
            "[yellow]Note:[/yellow] kamiwaza.json already records "
            f"template_version={recorded_version!r}. Skipping --bootstrap; "
            "running normal update flow."
        )

    manifest = get_manifest(template_shape)
    return _reconcile(
        cwd=cwd,
        metadata_path=metadata_path,
        metadata=metadata,
        manifest=manifest,
        recorded_version=recorded_version,
        dry_run=dry_run,
        force=force,
        non_interactive=non_interactive,
    )


# ---------------------------------------------------------------------------
# Bootstrap path — stamp template_version + template_shape; touch nothing else.
# ---------------------------------------------------------------------------


def _bootstrap(
    metadata_path: Path,
    metadata: dict,
    shape: str,
    *,
    dry_run: bool,
) -> UpdateSummary:
    target_version = current_template_version()
    metadata["template_version"] = target_version
    metadata["template_shape"] = shape
    summary = UpdateSummary()
    if dry_run:
        console.print(
            f"[cyan]--dry-run:[/cyan] would stamp template_version="
            f"{target_version!r} + template_shape={shape!r} into kamiwaza.json."
        )
        summary.files.append(
            FileResult("kamiwaza.json", "would-bootstrap", "dry-run")
        )
        return summary
    metadata_path.write_text(json.dumps(metadata, indent=4) + "\n", encoding="utf-8")
    console.print(
        f"[green]✓ Bootstrapped[/green] kamiwaza.json — template_version stamped "
        f"as {target_version!r}, template_shape={shape!r}. Run "
        "[bold]kz-ext update[/bold] without --bootstrap on the next CLI bump."
    )
    summary.files.append(FileResult("kamiwaza.json", "bootstrap", target_version))
    return summary


# ---------------------------------------------------------------------------
# Reconcile path — render templates and diff against the scaffold.
# ---------------------------------------------------------------------------


def _reconcile(
    *,
    cwd: Path,
    metadata_path: Path,
    metadata: dict,
    manifest: TemplateManifest,
    recorded_version: str,
    dry_run: bool,
    force: bool,
    non_interactive: bool,
) -> UpdateSummary:
    summary = UpdateSummary()

    # Apply migrations — for v1, MANIFESTS have no migrations registered, so
    # this is a no-op. The hook is in place for future template renames.
    for mig in manifest.migrations:
        old = cwd / mig.old_path
        new = cwd / mig.new_path
        if old.exists() and not new.exists():
            if dry_run:
                summary.migrations.append(
                    f"would-mv {mig.old_path} -> {mig.new_path}"
                )
            else:
                new.parent.mkdir(parents=True, exist_ok=True)
                old.rename(new)
                summary.migrations.append(f"mv {mig.old_path} -> {mig.new_path}")

    # Reuse the scaffolder's render context so substitutions match what
    # `kz-ext create` would produce today.
    scaffolder = Scaffolder()
    context = scaffolder._build_context(  # noqa: SLF001 — same package, intentional
        name=metadata.get("name", "extension"),
        type_=manifest.shape,
    )

    # Author-owned deny-list — files in this set are not reconciled.
    author_owned = set(AUTHOR_OWNED_DENYLIST.get(manifest.shape, ()))
    template_root = _template_root(manifest.shape)

    for owned in manifest.files:
        if owned.relative_path in author_owned:
            continue
        result = _reconcile_file(
            owned=owned,
            template_root=template_root,
            target_root=cwd,
            context=context,
            dry_run=dry_run,
            force=force,
            non_interactive=non_interactive,
        )
        summary.files.append(result)
        if result.action in ("updated", "applied", "would-update", "would-apply"):
            summary.updated += 1
        elif result.action in ("kept", "skipped", "would-keep"):
            summary.skipped += 1
        elif result.action == "no-change":
            summary.no_change += 1
        elif result.action == "missing":
            summary.skipped += 1
        if result.reason == "conflict":
            summary.conflicts += 1

    # Stamp the new template_version into kamiwaza.json after reconciling.
    target_version = manifest.template_version
    if target_version != recorded_version and not dry_run:
        metadata["template_version"] = target_version
        metadata_path.write_text(
            json.dumps(metadata, indent=4) + "\n", encoding="utf-8"
        )

    _print_summary(summary, dry_run=dry_run)
    return summary


def _template_root(shape: str) -> Path:
    pkg = importlib_resources.files("kamiwaza_extensions") / "templates" / shape
    return Path(str(pkg))


def _render(template_path: Path, context: dict) -> str:
    """Render a template file using the same substitution rules as Scaffolder."""
    text = template_path.read_text(encoding="utf-8")
    for key, val in context.items():
        text = text.replace(key, val)
    return text


def _is_text(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _reconcile_file(
    *,
    owned: TemplateOwnedFile,
    template_root: Path,
    target_root: Path,
    context: dict,
    dry_run: bool,
    force: bool,
    non_interactive: bool,
) -> FileResult:
    rel = owned.relative_path
    template_path = template_root / rel
    target_path = target_root / rel

    if not template_path.exists():
        return FileResult(rel, "missing", "template gone")

    # Binary asset — copy bytes, no diff/merge logic needed.
    if not _is_text(template_path):
        new_bytes = template_path.read_bytes()
        existing = target_path.read_bytes() if target_path.exists() else b""
        if existing == new_bytes:
            return FileResult(rel, "no-change")
        if dry_run:
            return FileResult(rel, "would-update", "binary")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(new_bytes)
        return FileResult(rel, "updated", "binary")

    new_content = _render(template_path, context)
    existing_content = target_path.read_text(encoding="utf-8") if target_path.exists() else None

    # Identical — nothing to do.
    if existing_content == new_content:
        return FileResult(rel, "no-change")

    # File is missing on disk → create it.
    if existing_content is None:
        if dry_run:
            return FileResult(rel, "would-update", "creating")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_content, encoding="utf-8")
        return FileResult(rel, "updated", "created")

    # Merge strategy — currently only used for kamiwaza.json. The text-diff
    # treatment of "merge" was preserve_if_modified in v1, but that misreads
    # an author-stamped scaffold as "modified" because the scaffolder writes
    # template_version + template_shape AFTER rendering. Do a JSON-aware
    # field merge instead: existing values win, except template_* which the
    # manifest controls.
    if owned.strategy == "merge" and rel.endswith(".json"):
        return _reconcile_json_merge(
            rel=rel,
            target_path=target_path,
            existing_content=existing_content,
            new_content=new_content,
            dry_run=dry_run,
        )

    # Existing diverges — apply per strategy.
    if owned.strategy == "overwrite":
        if dry_run:
            return FileResult(rel, "would-update", "overwrite")
        _backup(target_path, existing_content)
        target_path.write_text(new_content, encoding="utf-8")
        return FileResult(rel, "updated", "overwrite (.orig backup)")

    # preserve_if_modified or merge (treated identically in v1).
    if force:
        if dry_run:
            return FileResult(rel, "would-apply", "force")
        _backup(target_path, existing_content)
        target_path.write_text(new_content, encoding="utf-8")
        return FileResult(rel, "applied", "force (.orig backup)")

    if non_interactive:
        return FileResult(rel, "skipped", "conflict")

    if dry_run:
        return FileResult(rel, "would-keep", "conflict")

    # Interactive prompt.
    return _prompt_conflict(
        rel=rel,
        target_path=target_path,
        existing_content=existing_content,
        new_content=new_content,
    )


def _reconcile_json_merge(
    *,
    rel: str,
    target_path: Path,
    existing_content: str,
    new_content: str,
    dry_run: bool,
) -> FileResult:
    """Field-level merge for JSON files (kamiwaza.json today).

    Author-set fields win; template-controlled fields (template_version,
    template_shape) get stamped to the manifest's current values. New
    fields the template added since the scaffold was rendered are
    inherited from the rendered template.
    """
    try:
        existing = json.loads(existing_content)
        rendered = json.loads(new_content)
    except json.JSONDecodeError:
        # Malformed JSON on disk — fall back to preserve_if_modified.
        return FileResult(rel, "skipped", "malformed-json")

    if not (isinstance(existing, dict) and isinstance(rendered, dict)):
        return FileResult(rel, "skipped", "non-object-json")

    merged = {**rendered, **existing}
    # Manifest-controlled fields are always reset to the current values.
    if "template_version" in existing or "template_version" in rendered:
        merged["template_version"] = current_template_version()
    if "template_shape" in existing or "template_shape" in rendered:
        # Use the existing value if present (the file's been classified
        # before); otherwise infer from the rendered template's "type" field
        # which is shape-equivalent.
        merged["template_shape"] = existing.get(
            "template_shape", rendered.get("type", existing.get("type"))
        )

    new_text = json.dumps(merged, indent=4) + "\n"
    if new_text == existing_content:
        return FileResult(rel, "no-change")
    if dry_run:
        return FileResult(rel, "would-update", "json-merge")
    target_path.write_text(new_text, encoding="utf-8")
    return FileResult(rel, "updated", "json-merge")


def _prompt_conflict(
    *,
    rel: str,
    target_path: Path,
    existing_content: str,
    new_content: str,
) -> FileResult:
    diff = "".join(
        difflib.unified_diff(
            existing_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
    console.print(f"\n[yellow]Conflict:[/yellow] {rel}")
    console.print(diff or "(no textual diff)")
    choice = typer.prompt(
        "[a]pply / [k]eep / [s]kip", default="k", show_default=True
    ).strip().lower()
    if choice in ("a", "apply"):
        _backup(target_path, existing_content)
        target_path.write_text(new_content, encoding="utf-8")
        return FileResult(rel, "applied", "interactive (.orig backup)")
    return FileResult(rel, "kept", "interactive")


def _backup(target_path: Path, existing_content: str) -> None:
    backup = target_path.with_suffix(target_path.suffix + ".orig")
    backup.write_text(existing_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------


def _print_summary(summary: UpdateSummary, *, dry_run: bool) -> None:
    table = Table(title="kz-ext update summary" + (" (dry-run)" if dry_run else ""))
    table.add_column("Path")
    table.add_column("Action")
    table.add_column("Reason")
    for fr in summary.files:
        table.add_row(fr.relative_path, fr.action, fr.reason or "—")
    console.print(table)
    if summary.migrations:
        console.print("[bold]Migrations:[/bold]")
        for m in summary.migrations:
            console.print(f"  {m}")
    console.print(
        f"[bold]Updated:[/bold] {summary.updated}  "
        f"[bold]Conflicts:[/bold] {summary.conflicts}  "
        f"[bold]Skipped:[/bold] {summary.skipped}  "
        f"[bold]Unchanged:[/bold] {summary.no_change}"
    )
