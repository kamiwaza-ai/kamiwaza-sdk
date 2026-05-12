"""Bump command — propagate an extension's new version across well-known files.

Updates ``kamiwaza.json`` plus image tags in compose files, ``ARG`` defaults
in ``Dockerfile``, and the ``[project] version`` line in ``pyproject.toml`` /
``package.json`` so the manifest doesn't drift from the rest of the tree.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

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
        _update_package_json(ext_dir / "package.json", old_version, new_version),
    ):
        if update is not None:
            updates.append(update)

    if dry_run:
        for update in updates:
            rel = _relative(update.path, ext_dir)
            diff = difflib.unified_diff(
                update.before.splitlines(keepends=True),
                update.after.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
            console.print("".join(diff), end="", highlight=False)
        console.print(
            f"[yellow]Would bump:[/yellow] "
            f"[bold]{old_version}[/bold] → [bold]{new_version}[/bold] "
            f"({level}) — {len(updates)} file(s)"
        )
        return

    for update in updates:
        _atomic_write(update.path, update.after)

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


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(content)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Per-file updaters
# ---------------------------------------------------------------------------


def _update_kamiwaza_json(path: Path, old: str, new: str) -> Optional[FileUpdate]:
    before = path.read_text(encoding="utf-8")
    data = json.loads(before)

    data["version"] = new

    # Rewrite top-level image tag if it matches the old version.
    image = data.get("image")
    image_changed = False
    if isinstance(image, str) and ":" in image:
        repo, _, tag = image.rpartition(":")
        if tag == old:
            data["image"] = f"{repo}:{new}"
            image_changed = True

    after = json.dumps(data, indent=4) + "\n"
    if after == before:
        return None

    summary = "version" + (" + image tag" if image_changed else "")
    return FileUpdate(path=path, before=before, after=after, summary=summary)


def _update_compose_files(ext_dir: Path, old: str, new: str) -> List[Optional[FileUpdate]]:
    from kamiwaza_extensions.constants import COMPOSE_FILENAMES

    # Include appgarden overlay alongside the canonical compose names.
    candidates = list(COMPOSE_FILENAMES) + [
        "docker-compose.appgarden.yml",
        "docker-compose.appgarden.yaml",
    ]
    seen: set = set()
    results: List[Optional[FileUpdate]] = []
    for name in candidates:
        path = ext_dir / name
        if not path.exists() or path in seen:
            continue
        seen.add(path)
        results.append(_update_compose_file(path, old, new))
    return results


# Match `image: <repo>:<tag>` (optionally quoted). Anchored on `:` separator so
# external images like `postgres:16` won't match unless they happen to share
# the bumped version literally.
_COMPOSE_IMAGE_RE = re.compile(
    r"""(?P<prefix>^\s*image\s*:\s*['"]?)(?P<repo>[^\s'":]+):(?P<tag>[^\s'"]+)(?P<suffix>['"]?\s*(?:\#.*)?$)""",
    re.MULTILINE,
)


def _update_compose_file(path: Path, old: str, new: str) -> Optional[FileUpdate]:
    before = path.read_text(encoding="utf-8")

    count = 0

    def repl(match: re.Match) -> str:
        nonlocal count
        if match.group("tag") != old:
            return match.group(0)
        count += 1
        return f"{match.group('prefix')}{match.group('repo')}:{new}{match.group('suffix')}"

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
    # `old` for us to touch it (per ticket heuristic).
    arg_re = re.compile(
        rf"""(?m)^(?P<prefix>\s*ARG\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*)(?P<q>["']?){re.escape(old)}(?P=q)(?P<suffix>\s*)$"""
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


# Anchored on the `[project]` table to avoid touching [tool.poetry] or
# similar sections — those need their own rewriter if we ever support them.
_PYPROJECT_VERSION_RE = re.compile(
    r"""(?ms)^(?P<header>\[project\][^\[]*?\nversion\s*=\s*["'])(?P<value>[^"']+)(?P<tail>["'])"""
)


def _update_pyproject(path: Path, old: str, new: str) -> Optional[FileUpdate]:
    if not path.exists():
        return None
    before = path.read_text(encoding="utf-8")
    match = _PYPROJECT_VERSION_RE.search(before)
    if not match or match.group("value") != old:
        return None
    after = (
        before[: match.start("value")] + new + before[match.end("value") :]
    )
    return FileUpdate(path=path, before=before, after=after, summary="[project] version")


def _update_package_json(path: Path, old: str, new: str) -> Optional[FileUpdate]:
    if not path.exists():
        return None
    before = path.read_text(encoding="utf-8")
    try:
        data = json.loads(before)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("version") != old:
        return None
    data["version"] = new
    after = json.dumps(data, indent=2) + "\n"
    if after == before:
        return None
    return FileUpdate(path=path, before=before, after=after, summary='"version"')
