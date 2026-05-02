"""Dataclasses for the convert agent — prompt outputs, plans, summaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FileModification:
    """A single file modification produced by the agent.

    Actions:
    - ``create`` / ``modify`` — write ``content`` to ``path``
    - ``append`` — append ``content`` to existing ``path`` (or create)
    - ``copy`` — copy bytes from ``source_path`` to ``path``. Used to
      vendor binary deps (wheels, tarballs, images) the LLM cannot
      represent as text. ``content`` is unused. ``source_path`` is
      resolved against the original CLI path (``rebased_from``) when
      monorepo detection rebased; otherwise against ``app_dir``.
    """

    path: str  # Relative to app_dir
    action: str  # "create" | "modify" | "append" | "copy"
    content: str = ""  # Full new content (create/modify) or content to append; unused for copy
    description: str = ""  # What was changed and why
    source_path: Optional[str] = None  # For action=copy: source path within the source tree


@dataclass
class ConversionStrategy:
    """High-level approach chosen by the LLM before file generation."""

    extension_type: str = "app"
    conversion_mode: str = "preserve_existing_runtime"
    primary_service: str = "app"
    required_files: List[str] = field(default_factory=list)
    runtime_summary: str = ""
    manual_items: List[str] = field(default_factory=list)


@dataclass
class ValidationSummary:
    """Validation result for staged conversion output."""

    passed: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class ConversionPlan:
    """The agent's plan for converting an app."""

    modifications: List[FileModification] = field(default_factory=list)
    manual_items: List[str] = field(default_factory=list)
    summary: str = ""
    success: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    mode: str = "structured"
    strategy: Optional[ConversionStrategy] = None
