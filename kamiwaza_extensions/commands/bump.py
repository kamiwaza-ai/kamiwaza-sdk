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
from typing import TYPE_CHECKING, List, Optional, Tuple

import typer
from rich.console import Console

if TYPE_CHECKING:
    from packaging.version import Version

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

    # Scope compose/drift rewrites to the extension's own image repo so we
    # don't accidentally retag a sidecar that happens to share `old`
    # (e.g. `vendor/sidecar:2.0.14` while bumping our `app` to 2.1.0).
    manifest = json.loads(kamiwaza_json_path.read_text(encoding="utf-8"))
    ext_repo = extension_image_repo(manifest.get("image"))

    updates: List[FileUpdate] = []
    for update in (
        _update_kamiwaza_json(kamiwaza_json_path, old_version, new_version),
        *_update_compose_files(ext_dir, old_version, new_version, ext_repo),
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


def _compute_new_version(current: "Version", level: str) -> Optional[str]:
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
            # Use ``with_name`` instead of ``with_suffix(suffix + ".tmp")``
            # — Python 3.13 tightened ``with_suffix`` to reject suffixes
            # that contain internal dots (e.g. ``.json.tmp``).
            tmp = update.path.with_name(update.path.name + ".tmp")
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
    if data.get("version") != old:
        # Manifest already drifted (or its detector value was different);
        # fall back to writing through json.dumps so the file is at least
        # internally consistent post-bump.
        data["version"] = new
        return FileUpdate(
            path=path,
            before=before,
            after=json.dumps(data, indent=4) + "\n",
            summary="version",
        )

    # Targeted rewrite of the top-level ``"version"`` string. Mirrors the
    # package.json approach so a manifest with non-default indentation,
    # key order, or non-ASCII content doesn't get a full-file reformat
    # diff on bump.
    span = _find_top_level_string_value_span(before, "version")
    if span is None or before[span[0] : span[1]] != old:
        # Pathological: top-level version isn't a string. Re-serialize.
        data["version"] = new
        return FileUpdate(
            path=path,
            before=before,
            after=json.dumps(data, indent=4) + "\n",
            summary="version",
        )
    after = before[: span[0]] + new + before[span[1] :]

    # Conditional image-tag rewrite.
    image = data.get("image")
    image_changed = False
    if isinstance(image, str):
        repo, tag, digest = _split_image_ref(image)
        if tag == old:
            new_image = f"{repo}:{new}{digest or ''}"
            image_span = _find_top_level_string_value_span(after, "image")
            if image_span is not None and after[image_span[0] : image_span[1]] == image:
                after = after[: image_span[0]] + new_image + after[image_span[1] :]
                image_changed = True

    if after == before:
        return None
    summary = "version" + (" + image tag" if image_changed else "")
    return FileUpdate(path=path, before=before, after=after, summary=summary)


def extension_image_repo(manifest_image: object) -> Optional[str]:
    """Return the manifest image's repo (e.g. ``ghcr.io/x/y``) or ``None``.

    Used to scope compose rewrites and drift warnings to images that belong
    to the extension, so a sidecar with a coincidentally matching tag isn't
    silently retagged.
    """
    if not isinstance(manifest_image, str):
        return None
    repo, _, _ = _split_image_ref(manifest_image)
    return repo or None


def _update_compose_files(
    ext_dir: Path, old: str, new: str, ext_repo: Optional[str]
) -> List[Optional[FileUpdate]]:
    from kamiwaza_extensions.constants import ALL_COMPOSE_FILENAMES

    seen: set = set()
    results: List[Optional[FileUpdate]] = []
    for name in ALL_COMPOSE_FILENAMES:
        path = ext_dir / name
        if not path.exists() or path in seen:
            continue
        seen.add(path)
        results.append(_update_compose_file(path, old, new, ext_repo))
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


def _update_compose_file(
    path: Path, old: str, new: str, ext_repo: Optional[str]
) -> Optional[FileUpdate]:
    before = path.read_text(encoding="utf-8")

    count = 0

    def repl(match: re.Match) -> str:
        nonlocal count
        ref = match.group("ref")
        repo, tag, digest = _split_image_ref(ref)
        if tag != old:
            return match.group(0)
        # If the manifest declares an image repo, only retag images that
        # belong to the extension. Without a manifest repo we fall back to
        # tag-equality alone (the original behavior, exercised by tests
        # for manifests that omit `image`).
        if ext_repo is not None and repo != ext_repo:
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

    # ARG <NAME>_VERSION=<old> — only retag ARGs whose name ends in
    # ``_VERSION`` so we don't silently rewrite an unrelated pin like
    # ``ARG PYTHON_VERSION=3.11.9`` when the extension itself is at
    # ``3.11.9``. Matches the drift detector's heuristic and keeps the
    # blast radius narrow.
    arg_re = re.compile(
        rf"""(?m)^(?P<prefix>\s*ARG\s+[A-Za-z_][A-Za-z0-9_]*_VERSION\s*=\s*)(?P<q>["']?){re.escape(old)}(?P=q)(?P<suffix>\s*(?:\#.*)?)$"""
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


# TOML allows a comment after a table header (`[project]  # main metadata`),
# so match `]` followed by optional whitespace and an optional `# comment`.
_TOML_SECTION_RE = re.compile(r"(?m)^\[([^\]\n]+)\]\s*(?:#.*)?$")


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
    # Targeted rewrite of the **top-level** `"version"` key. A bare regex
    # match-first would rewrite the first textual `"version"` — which could
    # be a nested one like `engines.version` or `config.version` — and
    # leave the real package version stale. Walk the source with brace
    # depth + string awareness to locate the depth-1 value span and splice
    # in the new version, preserving the file's indentation and key order.
    span = _find_top_level_string_value_span(before, "version")
    if span is None or before[span[0] : span[1]] != old:
        return None
    after = before[: span[0]] + new + before[span[1] :]
    if after == before:
        return None
    return FileUpdate(path=path, before=before, after=after, summary='"version"')


def _find_top_level_string_value_span(text: str, key: str) -> Optional[Tuple[int, int]]:
    """Return ``(start, end)`` of the string value bound to top-level *key*.

    Walks the JSON text tracking brace/bracket depth so nested objects
    that happen to use the same key name (e.g. ``engines.version``) are
    ignored. Returns ``None`` if the key isn't found at depth 1 with a
    string value.
    """
    depth = 0
    i = 0
    n = len(text)
    in_string = False
    string_start = -1
    pending_key: Optional[str] = None
    awaiting_value = False
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                token = text[string_start + 1 : i]
                in_string = False
                if awaiting_value and depth == 1:
                    if pending_key == key:
                        return string_start + 1, i
                    awaiting_value = False
                    pending_key = None
                else:
                    pending_key = token
            i += 1
            continue
        if ch == '"':
            in_string = True
            string_start = i
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            awaiting_value = False
            pending_key = None
        elif ch == ":":
            if depth == 1 and pending_key is not None:
                awaiting_value = True
        elif ch == ",":
            awaiting_value = False
            pending_key = None
        i += 1
    return None
