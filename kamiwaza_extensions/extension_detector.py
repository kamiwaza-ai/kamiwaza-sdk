"""Shared extension auto-detection and metadata loading."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from kamiwaza_extensions.constants import COMPOSE_FILENAMES


@dataclass
class ExtensionInfo:
    """Detected extension metadata."""

    path: Path
    name: str
    version: str
    metadata: Dict[str, Any]
    compose_path: Optional[Path] = None
    compose_data: Optional[Dict[str, Any]] = field(default=None, repr=False)


def infer_extension_type(metadata: Dict[str, Any]) -> str:
    """Determine extension type from metadata or naming convention.

    Checks the modern ``type`` field first, then the legacy
    ``template_type`` field, and finally falls back to name-prefix
    heuristics. Returns ``"app"`` only when no signal points elsewhere.

    Single source of truth for type inference — kept here so both the
    publish path (``commands/publish.py``) and the local-dev runner
    (``dev_local.py``) honor the same legacy fallbacks. PR #87 round-6
    review (claude + comprehensive consensus) flagged these two paths
    drifting; consolidated here.
    """
    explicit = metadata.get("type") or metadata.get("template_type")
    if explicit in ("app", "tool", "service"):
        return explicit

    name = metadata.get("name", "")
    if name.startswith("tool-") or name.startswith("mcp-"):
        return "tool"
    if name.startswith("service-"):
        return "service"

    return "app"


class ExtensionNotFoundError(FileNotFoundError):
    """No kamiwaza.json found in expected locations."""

    pass


class MultipleExtensionsError(FileNotFoundError):
    """Ambiguous — multiple kamiwaza.json found one level deep."""

    pass


class ExtensionDetector:
    """Find extension root directory and load metadata + compose data."""

    def detect(self, start_dir: Optional[Path] = None) -> ExtensionInfo:
        """Find the extension and load its metadata.

        Search order:
        1. ``start_dir`` (default: cwd) for ``kamiwaza.json``
        2. One level deep (``*/kamiwaza.json``)

        Returns an ``ExtensionInfo`` with metadata and, if present, compose
        data loaded from the first matching compose filename.
        """
        root = start_dir or Path.cwd()
        ext_dir = self._find_root(root)
        metadata = self._load_metadata(ext_dir)
        compose_path, compose_data = self._load_compose(ext_dir)
        return ExtensionInfo(
            path=ext_dir,
            name=metadata.get("name", ext_dir.name),
            version=metadata.get("version", "0.0.0"),
            metadata=metadata,
            compose_path=compose_path,
            compose_data=compose_data,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_root(self, start: Path) -> Path:
        if (start / "kamiwaza.json").exists():
            return start

        found = sorted(
            (d.parent for d in start.glob("*/kamiwaza.json")),
            key=lambda p: p.name,
        )
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            dirs = ", ".join(str(d.name) for d in found)
            raise MultipleExtensionsError(
                f"Multiple kamiwaza.json found: {dirs}. "
                "Run from inside a specific extension directory."
            )

        raise ExtensionNotFoundError(
            "No kamiwaza.json found. "
            "Run this in an extension directory or use `kz-ext create`."
        )

    def _load_metadata(self, ext_dir: Path) -> Dict[str, Any]:
        path = ext_dir / "kamiwaza.json"
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, FileNotFoundError) as exc:
            raise ExtensionNotFoundError(
                f"Cannot read kamiwaza.json in {ext_dir}: {exc}"
            ) from exc

    def _load_compose(
        self, ext_dir: Path
    ) -> tuple[Optional[Path], Optional[Dict[str, Any]]]:
        for name in COMPOSE_FILENAMES:
            candidate = ext_dir / name
            if candidate.exists():
                try:
                    data = yaml.safe_load(candidate.read_text())
                    return candidate, data
                except yaml.YAMLError as exc:
                    from rich.console import Console
                    console = Console(stderr=True)
                    console.print(f"[yellow]Warning:[/yellow] Failed to parse {candidate}: {exc}")
                    return candidate, None
        return None, None
