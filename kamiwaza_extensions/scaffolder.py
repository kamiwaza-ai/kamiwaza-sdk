"""Scaffolder — template rendering and directory creation for kz-ext create."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from functools import lru_cache
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Dict

from rich.console import Console

from kamiwaza_extensions import __version__
from kamiwaza_extensions.template_manifest import (
    MANIFESTS,
    current_template_version,
)

console = Console(stderr=True)

# Name validation: lowercase alphanumeric + hyphens
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

VALID_TYPES = ("app", "tool", "service")


def build_render_context(name: str, type_: str) -> Dict[str, str]:
    """Return the placeholder→value substitution map for a given scaffold.

    Lifted from ``Scaffolder._build_context`` so ``commands/update.py`` can
    rebuild the same context as ``Scaffolder.create()`` without reaching
    into a private method (the previous ``# noqa: SLF001`` smell — review
    iteration-1 finding I7). Both ``Scaffolder.create()`` and ``UpdateCommand``
    must produce byte-identical rendered output for unmodified files; the
    only way to guarantee that is to share the context source.

    Runtime-lib pins (Py + TS) are read from
    ``kamiwaza_extensions/compatibility.json`` so a fresh scaffold's
    ``requirements.txt`` / ``package.json`` always matches the doctor's
    supported window — no first-run warnings on a brand-new scaffold
    (PR-86 round-2 H3).
    """
    major = __version__.split(".")[0]
    next_major = str(int(major) + 1)
    py_pin, ts_pin = _runtime_lib_pins()
    return {
        "{{name}}": name,
        "{{version}}": "0.1.0",
        "{{kz_ext_version}}": f">={__version__},<{next_major}.0.0",
        "{{python_runtime_lib_version}}": py_pin,
        "{{ts_runtime_lib_version}}": ts_pin,
        "{{description}}": f"A Kamiwaza {type_} extension",
        "{{type}}": type_,
    }


@lru_cache(maxsize=1)
def _runtime_lib_pins() -> tuple[str, str]:
    """Read the bundled ``compatibility.json`` and return (py_pin, ts_pin)
    suitable for direct rendering into ``requirements.txt`` / ``package.json``.

    Falls back to permissive ranges if the bundle is missing or malformed —
    the ``test_compatibility_bundle.py`` invariants guard against that
    actually shipping, so this branch is purely defensive.
    """
    try:
        bundle = json.loads(
            (importlib_resources.files("kamiwaza_extensions") / "compatibility.json")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return (">=0.3.0,<0.4.0", "^0.3.0")
    compat = bundle.get("runtime_lib_compat", {})
    py = compat.get("python", {}).get("kamiwaza-extensions-lib", ">=0.3.0,<0.4.0")
    ts = compat.get("typescript", {}).get("@kamiwaza-ai/extensions-lib", "^0.3.0")
    return (py, ts)


def substitute(text: str, context: Dict[str, str]) -> str:
    """Apply the context's placeholder→value substitutions to ``text``.

    Single source-of-truth replacement helper (review iteration-1 finding
    I8: ``for k, v in context.items(): text = text.replace(k, v)`` was
    duplicated three times across scaffolder.py path / scaffolder.py content
    / update.py render).
    """
    for key, val in context.items():
        text = text.replace(key, val)
    return text


def hash_text(content: str) -> str:
    """Return the canonical content hash used by ``kz-ext update``.

    ``sha256:<hex>`` over the UTF-8 bytes of the rendered file content.
    Used at scaffold-create time to record what was written, then by
    ``update`` to detect "clean since last write" — see PR-86 C4 / option
    (b). Stable across Python versions.
    """
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_rendered_hashes(shape: str, context: Dict[str, str]) -> Dict[str, str]:
    """Hash every ``preserve_if_modified`` file in the shape's manifest,
    rendered with the given context.

    Keys are manifest ``relative_path`` strings; values are
    ``sha256:<hex>``. Binary template files are skipped (no
    preserve-if-modified semantics — they have no diff/merge concept).

    Used by ``Scaffolder.create()`` to seed
    ``kamiwaza.json.template_file_hashes`` so that the *next* ``kz-ext
    update`` can detect which files the author hasn't touched and
    silently sweep them forward to the new template.
    """
    manifest = MANIFESTS[shape]  # type: ignore[index]
    template_root = Path(
        str(importlib_resources.files("kamiwaza_extensions") / "templates" / shape)
    )
    hashes: Dict[str, str] = {}
    for owned in manifest.files:
        if owned.strategy != "preserve_if_modified":
            continue
        path = template_root / owned.relative_path
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Binary: no preserve_if_modified semantics anyway.
            continue
        hashes[owned.relative_path] = hash_text(substitute(text, context))
    return hashes


class Scaffolder:
    """Scaffolds new extension projects from bundled templates."""

    def create(self, *, type_: str, name: str) -> Path:
        if type_ not in VALID_TYPES:
            raise ValueError(f"Invalid type '{type_}'. Must be one of: {', '.join(VALID_TYPES)}")

        name = self._validate_name(name, type_)
        cwd = Path.cwd()
        cwd_visible = [f for f in cwd.iterdir() if not f.name.startswith(".")]

        # Empty cwd → preserve historical behavior: scaffold INTO cwd. Users
        # who already `mkdir foo && cd foo && kz-ext create --name foo` keep
        # working without behavior change.
        #
        # Non-empty cwd → P1 (§4.8 walkthrough): scaffold into cwd/{name},
        # creating the dir if needed. This removes the "you must create the
        # dir yourself first" surprise that bit Preston in the 0.12.1 review.
        if cwd_visible:
            target = cwd / name
            if target.exists():
                target_visible = [
                    f for f in target.iterdir() if not f.name.startswith(".")
                ]
                if target_visible:
                    raise FileExistsError(
                        f"Target directory '{target.name}' already exists and is "
                        f"not empty ({len(target_visible)} file(s) found). "
                        f"Choose a different name or empty the directory."
                    )
            else:
                target.mkdir()
        else:
            target = cwd

        context = self._build_context(name, type_)
        template_dir = self._get_template_dir(type_)
        self._render_template(template_dir, target, context)
        self._stamp_template_metadata(target, type_)
        self._git_init(target)

        return target

    def _stamp_template_metadata(self, target: Path, shape: str) -> None:
        """Stamp template_version + template_shape + template_file_hashes
        into the rendered kamiwaza.json.

        ``template_version`` + ``template_shape`` drive ``kz-ext update``'s
        manifest dispatch (ENG-3890). ``template_file_hashes`` (PR-86 C4
        option b) records the content hash of every preserve_if_modified
        file we just rendered, so the next update can detect "unchanged
        since scaffold" and silently sweep clean files forward instead of
        prompting on every CLI bump.

        Done as a post-render JSON write rather than as template
        placeholders because the values are CLI-version metadata, not
        author-visible scaffold fields — adding placeholders would couple
        every template kamiwaza.json file to the fields we want to set.
        """
        meta_path = target / "kamiwaza.json"
        if not meta_path.exists():
            return  # Defensive — every shape ships kamiwaza.json today.
        try:
            data = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            return
        data["template_version"] = current_template_version()
        data["template_shape"] = shape
        # Build the same context the scaffolder used so the hashes match
        # what's actually on disk byte-for-byte.
        context = build_render_context(
            name=data.get("name", "extension"), type_=shape
        )
        data["template_file_hashes"] = compute_rendered_hashes(shape, context)
        meta_path.write_text(
            json.dumps(data, indent=4) + "\n",
            encoding="utf-8",
        )

    def _validate_name(self, name: str, type_: str) -> str:
        name = name.lower().strip()

        if not _NAME_RE.match(name):
            raise ValueError(
                f"Invalid name '{name}'. Must be lowercase alphanumeric with hyphens, starting with a letter."
            )

        # Auto-apply convention prefix if missing
        if type_ == "tool" and not (name.startswith("tool-") or name.startswith("mcp-")):
            name = f"tool-{name}"
            console.print(f"[dim]Auto-prefixed name to '{name}' per tool naming convention[/dim]")
        elif type_ == "service" and not name.startswith("service-"):
            name = f"service-{name}"
            console.print(f"[dim]Auto-prefixed name to '{name}' per service naming convention[/dim]")

        return name

    def _build_context(self, name: str, type_: str) -> Dict[str, str]:
        # Thin wrapper around the module-level build_render_context — kept
        # for backward-compat with anything that still calls it via the
        # instance. New callers should use build_render_context directly.
        return build_render_context(name, type_)

    def _get_template_dir(self, type_: str) -> Path:
        pkg = importlib_resources.files("kamiwaza_extensions") / "templates" / type_
        # importlib_resources returns a Traversable; convert to Path
        return Path(str(pkg))

    def _render_template(self, template_dir: Path, target: Path, context: Dict[str, str]) -> None:
        if not template_dir.exists():
            raise FileNotFoundError(f"Template directory not found: {template_dir}")

        for src in sorted(template_dir.rglob("*")):
            if src.is_dir():
                continue

            rel = src.relative_to(template_dir)
            dest = target / substitute(str(rel), context)

            dest.parent.mkdir(parents=True, exist_ok=True)

            # Render templated text files and preserve binary assets byte-for-byte.
            try:
                content = src.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                dest.write_bytes(src.read_bytes())
                continue

            dest.write_text(substitute(content, context), encoding="utf-8")

    def _git_init(self, target: Path) -> None:
        try:
            subprocess.run(
                ["git", "init"],
                cwd=str(target),
                capture_output=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            console.print("[dim]git not found — skipping git init[/dim]")
