"""Scaffolder — template rendering and directory creation for kz-ext create."""

from __future__ import annotations

import re
import subprocess
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Dict

from rich.console import Console

from kamiwaza_extensions import __version__

console = Console(stderr=True)

# Name validation: lowercase alphanumeric + hyphens
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

VALID_TYPES = ("app", "tool", "service")


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
        self._git_init(target)

        return target

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
        major = __version__.split(".")[0]
        next_major = str(int(major) + 1)
        return {
            "{{name}}": name,
            "{{version}}": "0.1.0",
            "{{kz_ext_version}}": f">={__version__},<{next_major}.0.0",
            "{{python_runtime_lib_version}}": ">=0.1.0",
            "{{ts_runtime_lib_version}}": "^0.2.0",
            "{{description}}": f"A Kamiwaza {type_} extension",
            "{{type}}": type_,
        }

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
            # Apply context substitution to file path
            rel_str = str(rel)
            for key, val in context.items():
                rel_str = rel_str.replace(key, val)
            dest = target / rel_str

            dest.parent.mkdir(parents=True, exist_ok=True)

            # Render templated text files and preserve binary assets byte-for-byte.
            try:
                content = src.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                dest.write_bytes(src.read_bytes())
                continue

            for key, val in context.items():
                content = content.replace(key, val)
            dest.write_text(content, encoding="utf-8")

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
