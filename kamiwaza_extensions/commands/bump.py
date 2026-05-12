"""Bump command — propagate an extension's new version across well-known files.

Updates ``kamiwaza.json`` plus image tags in compose files, ``ARG`` defaults
in ``Dockerfile``, and the ``[project] version`` line in ``pyproject.toml`` /
``package.json`` so the manifest doesn't drift from the rest of the tree.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import typer
from rich.console import Console

console = Console(stderr=True)


@dataclass
class FileUpdate:
    """A pending rewrite of a single file."""

    path: Path
    before: str
    after: str
    summary: str


def run_bump(*, level: str = "patch", dry_run: bool = False) -> None:
    """Bump the version in kamiwaza.json and propagate to sibling files."""
    from packaging.version import Version

    from kamiwaza_extensions.extension_detector import ExtensionDetector

    detector = ExtensionDetector()
    info = detector.detect()

    ext_dir = info.path
    kamiwaza_json_path = ext_dir / "kamiwaza.json"
    if not kamiwaza_json_path.exists():
        console.print("[red]Error:[/red] kamiwaza.json not found.")
        raise typer.Exit(code=1)

    try:
        current = Version(info.version)
    except Exception:
        console.print(
            f"[red]Error:[/red] Current version '{info.version}' is not valid semver."
        )
        raise typer.Exit(code=1)

    old_version = info.version
    new_version = _compute_new_version(current, level)
    if new_version is None:
        console.print(
            f"[red]Error:[/red] Unknown level '{level}'. Use: major, minor, patch"
        )
        raise typer.Exit(code=1)

    updates: List[FileUpdate] = []
    for update in (
        _update_kamiwaza_json(kamiwaza_json_path, old_version, new_version),
        *_update_compose_files(ext_dir, old_version, new_version),
        _update_dockerfile(ext_dir / "Dockerfile", old_version, new_version),
        _update_pyproject(ext_dir / "pyproject.toml", old_version, new_version),
        *_update_package_json_files(ext_dir, old_version, new_version),
    ):
        if update is not None:
            updates.append(update)

    if dry_run:
        for update in updates:
            rel = _relative(update.path, ext_dir)
            diff = "".join(
                difflib.unified_diff(
                    update.before.splitlines(keepends=True),
                    update.after.splitlines(keepends=True),
                    fromfile=f"a/{rel}",
                    tofile=f"b/{rel}",
                )
            )
            # Diff body goes to stdout (so `kz-ext bump --dry-run > patch.diff`
            # captures it) and bypasses Rich markup so TOML/Dockerfile syntax
            # like `[project]` isn't parsed as a style tag.
            sys.stdout.write(diff)
        sys.stdout.flush()
        console.print(
            f"[yellow]Would bump:[/yellow] "
            f"[bold]{old_version}[/bold] → [bold]{new_version}[/bold] "
            f"({level}) — {len(updates)} file(s)"
        )
        return

    _commit_updates(updates)

    extras = [u for u in updates if u.path != kamiwaza_json_path]
    console.print(
        f"[green]✓[/green] Bumped version: "
        f"[bold]{old_version}[/bold] → [bold]{new_version}[/bold] ({level})"
    )
    for update in extras:
        console.print(f"  [dim]→[/dim] {_relative(update.path, ext_dir)}: {update.summary}")


def _compute_new_version(current, level: str) -> Optional[str]:
    major, minor, patch = current.major, current.minor, current.micro
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return None


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _commit_updates(updates: List[FileUpdate]) -> None:
    """Two-phase write: stage every tmp file first, then rename all.

    Single-file atomicity is preserved by ``Path.replace``; the two-phase
    ordering reduces the window in which a half-bumped tree is observable.
    If any tmp write fails, no rename has happened yet, so the tree is
    unchanged and the caller sees the original exception.
    """
    staged: List[Tuple[Path, Path]] = []
    try:
        for update in updates:
            tmp = update.path.with_suffix(update.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                f.write(update.after)
            staged.append((tmp, update.path))
    except Exception:
        # Best-effort cleanup of already-staged tmp files.
        for tmp, _ in staged:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    for tmp, dest in staged:
        tmp.replace(dest)


# ---------------------------------------------------------------------------
# Image-ref parsing
# ---------------------------------------------------------------------------


def _split_image_ref(ref: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Split a Docker image reference into ``(repo, tag, digest)``.

    Handles registry ports (``localhost:5000/app``) by splitting tag from
    repo on the **last** ``:`` that isn't inside the digest suffix, and
    preserves any trailing ``@sha256:...`` digest so callers can re-attach
    it after rewriting the tag.
    """
    digest: Optional[str] = None
    if "@" in ref:
        ref, _, digest = ref.partition("@")
        digest = "@" + digest

    # If there's a `/`, tag (if any) lives after the last `/`'s segment.
    if "/" in ref:
        head, _, last = ref.rpartition("/")
        if ":" in last:
            name, _, tag = last.rpartition(":")
            return f"{head}/{name}", tag, digest
        return ref, None, digest
    # No `/` — single-segment image like `app:2.0.14` or `postgres:16`.
    if ":" in ref:
        repo, _, tag = ref.rpartition(":")
        return repo, tag, digest
    return ref, None, digest


# ---------------------------------------------------------------------------
# Per-file updaters
# ---------------------------------------------------------------------------


def _update_kamiwaza_json(path: Path, old: str, new: str) -> Optional[FileUpdate]:
    before = path.read_text(encoding="utf-8")
    data = json.loads(before)

    data["version"] = new

    image = data.get("image")
    image_changed = False
    if isinstance(image, str):
        repo, tag, digest = _split_image_ref(image)
        if tag == old:
            data["image"] = f"{repo}:{new}{digest or ''}"
            image_changed = True

    after = json.dumps(data, indent=4) + "\n"
    if after == before:
        return None

    summary = "version" + (" + image tag" if image_changed else "")
    return FileUpdate(path=path, before=before, after=after, summary=summary)


def _update_compose_files(ext_dir: Path, old: str, new: str) -> List[Optional[FileUpdate]]:
    from kamiwaza_extensions.constants import ALL_COMPOSE_FILENAMES

    seen: set = set()
    results: List[Optional[FileUpdate]] = []
    for name in ALL_COMPOSE_FILENAMES:
        path = ext_dir / name
        if not path.exists() or path in seen:
            continue
        seen.add(path)
        results.append(_update_compose_file(path, old, new))
    return results


# Capture the full image reference token (everything between optional quotes
# and any trailing whitespace/comment) so a ref like
# `localhost:5000/app:2.0.14@sha256:...` is handed off to `_split_image_ref`
# intact rather than mangled by a regex that doesn't understand digests or
# registry ports.
_COMPOSE_IMAGE_RE = re.compile(
    r"""(?P<prefix>^\s*image\s*:\s*)(?P<quote>['"]?)(?P<ref>\S+?)(?P=quote)(?P<suffix>\s*(?:\#.*)?)$""",
    re.MULTILINE,
)


def _update_compose_file(path: Path, old: str, new: str) -> Optional[FileUpdate]:
    before = path.read_text(encoding="utf-8")

    count = 0

    def repl(match: re.Match) -> str:
        nonlocal count
        ref = match.group("ref")
        repo, tag, digest = _split_image_ref(ref)
        if tag != old:
            return match.group(0)
        count += 1
        new_ref = f"{repo}:{new}{digest or ''}"
        return (
            f"{match.group('prefix')}{match.group('quote')}"
            f"{new_ref}{match.group('quote')}{match.group('suffix')}"
        )

    after = _COMPOSE_IMAGE_RE.sub(repl, before)
    if count == 0 or after == before:
        return None
    return FileUpdate(
        path=path,
        before=before,
        after=after,
        summary=f"{count} image tag(s)",
    )


def _update_dockerfile(path: Path, old: str, new: str) -> Optional[FileUpdate]:
    if not path.exists():
        return None
    before = path.read_text(encoding="utf-8")

    # ARG NAME=<old> or ARG NAME="<old>" — default value must exactly equal
    # `old`. Allows trailing whitespace and `# comment` so Dockerfiles using
    # the common `ARG FOO=1.0.0  # bumped` style aren't silently skipped.
    arg_re = re.compile(
        rf"""(?m)^(?P<prefix>\s*ARG\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*)(?P<q>["']?){re.escape(old)}(?P=q)(?P<suffix>\s*(?:\#.*)?)$"""
    )

    count = 0

    def repl(match: re.Match) -> str:
        nonlocal count
        count += 1
        q = match.group("q")
        return f"{match.group('prefix')}{q}{new}{q}{match.group('suffix')}"

    after = arg_re.sub(repl, before)
    if count == 0 or after == before:
        return None
    return FileUpdate(
        path=path,
        before=before,
        after=after,
        summary=f"{count} ARG default(s)",
    )


def _update_pyproject(path: Path, old: str, new: str) -> Optional[FileUpdate]:
    """Rewrite ``[project] version`` while preserving comments and formatting.

    Locates the ``[project]`` table by header line, then scans only that
    table's body for the ``version`` key — sidesteps the lazy-regex trap
    where a TOML array like ``classifiers = [...]`` before the version
    line defeats a single anchored pattern.
    """
    if not path.exists():
        return None
    before = path.read_text(encoding="utf-8")
    span = _find_project_table_span(before)
    if span is None:
        return None
    start, end = span
    version_re = re.compile(
        r"""(?m)^(?P<prefix>version\s*=\s*["'])(?P<value>[^"']+)(?P<tail>["'])"""
    )
    body = before[start:end]
    match = version_re.search(body)
    if not match or match.group("value") != old:
        return None
    new_body = body[: match.start("value")] + new + body[match.end("value") :]
    after = before[:start] + new_body + before[end:]
    return FileUpdate(path=path, before=before, after=after, summary="[project] version")


_TOML_SECTION_RE = re.compile(r"(?m)^\[([^\]\n]+)\]\s*$")


def _find_project_table_span(text: str) -> Optional[Tuple[int, int]]:
    """Return ``(start, end)`` indices of the ``[project]`` table body."""
    for header in _TOML_SECTION_RE.finditer(text):
        if header.group(1).strip() != "project":
            continue
        body_start = header.end()
        next_header = _TOML_SECTION_RE.search(text, body_start)
        body_end = next_header.start() if next_header else len(text)
        return body_start, body_end
    return None


def _update_package_json_files(
    ext_dir: Path, old: str, new: str
) -> List[Optional[FileUpdate]]:
    """Update root and any first-level ``package.json`` (e.g. ``frontend/``).

    Scaffolded app extensions keep their JS package file under
    ``frontend/package.json``; a root-only check would silently leave it
    stale. Restricting to depth 1 keeps the scan bounded and avoids
    crawling ``node_modules``.
    """
    candidates: List[Path] = []
    root = ext_dir / "package.json"
    if root.exists():
        candidates.append(root)
    for child in sorted(ext_dir.iterdir() if ext_dir.exists() else []):
        if not child.is_dir() or child.name in {"node_modules", ".git"} or child.name.startswith("."):
            continue
        nested = child / "package.json"
        if nested.exists():
            candidates.append(nested)
    return [_update_package_json(p, old, new) for p in candidates]


def _update_package_json(path: Path, old: str, new: str) -> Optional[FileUpdate]:
    before = path.read_text(encoding="utf-8")
    try:
        data = json.loads(before)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("version") != old:
        return None
    # Targeted line rewrite preserves the source file's indentation,
    # key order, and trailing newline — re-serializing via json.dumps
    # would normalize 4-space or tab indentation to 2 and create
    # gratuitous diffs.
    version_re = re.compile(r'("version"\s*:\s*")[^"]+(")')
    after, count = version_re.subn(rf"\g<1>{new}\g<2>", before, count=1)
    if count == 0 or after == before:
        return None
    return FileUpdate(path=path, before=before, after=after, summary='"version"')
