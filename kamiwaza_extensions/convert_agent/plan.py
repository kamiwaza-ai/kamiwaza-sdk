"""Plan parsing, application, and post-processing.

Lifecycle of a ConversionPlan:

1. ``parse_strategy_response`` / ``parse_response`` decode the LLM's
   JSON envelope into typed dataclasses.
2. ``_preserve_existing_kamiwaza_json`` strips LLM-proposed manifest
   changes when the existing one is already valid (the LLM tends to
   suggest cosmetic edits we don't need).
3. ``_ensure_supporting_files`` adds ``kamiwaza.json`` and
   ``CONVERT_NOTES.md`` modifications if the LLM didn't produce them.
4. ``_dedupe_manual_items_against_modifications`` strips stale
   ``manual_items`` that restate work already encoded as a
   modification.
5. ``apply_plan`` writes the result to disk.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from kamiwaza_extensions.app_analyzer import AnalysisResult, AppAnalyzer
from kamiwaza_extensions.convert_agent.models import (
    ConversionPlan,
    ConversionStrategy,
    FileModification,
)

console = Console(stderr=True)

_ALLOWED_ACTIONS = ("create", "modify", "append", "copy")


# ----------------------------------------------------------------------
# Parsing — LLM JSON envelopes → dataclasses
# ----------------------------------------------------------------------


def parse_strategy_response(response_text: str) -> Optional[ConversionStrategy]:
    """Parse the strategy response from the LLM."""
    data = _parse_json_payload(response_text)
    if not isinstance(data, dict):
        return None

    extension_type = str(data.get("extension_type", "app"))
    if extension_type not in {"app", "tool", "service"}:
        extension_type = "app"

    return ConversionStrategy(
        extension_type=extension_type,
        conversion_mode=str(data.get("conversion_mode", "preserve_existing_runtime")),
        primary_service=str(data.get("primary_service", "app")),
        required_files=[str(item) for item in data.get("required_files", []) if item],
        runtime_summary=str(data.get("runtime_summary", "")),
        manual_items=[str(item) for item in data.get("manual_items", []) if item],
    )


def parse_response(response_text: str) -> ConversionPlan:
    """Parse the LLM response into a ConversionPlan."""
    data = _parse_json_payload(response_text)
    if not isinstance(data, dict):
        return ConversionPlan(
            success=False,
            manual_items=["LLM response could not be parsed. Review the app manually."],
            summary="Conversion plan could not be generated automatically.",
            errors=["LLM response could not be parsed."],
        )

    try:
        modifications = []
        for mod in data.get("modifications", []):
            modifications.append(
                FileModification(
                    path=mod.get("path", ""),
                    action=mod.get("action", "modify"),
                    content=mod.get("content", ""),
                    description=mod.get("description", ""),
                    source_path=mod.get("source_path"),
                )
            )

        return ConversionPlan(
            modifications=modifications,
            manual_items=[str(item) for item in data.get("manual_items", []) if item],
            summary=str(data.get("summary", "")),
        )
    except (TypeError, AttributeError, KeyError):
        return ConversionPlan(
            success=False,
            manual_items=["LLM returned unexpected response shape. Review the app manually."],
            summary="Conversion plan could not be generated automatically.",
            errors=["LLM returned unexpected response shape."],
        )


def _parse_json_payload(response_text: str) -> Any:
    json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", response_text, re.DOTALL)
    json_str = json_match.group(1) if json_match else response_text.strip()
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


# ----------------------------------------------------------------------
# Plan application — write the modifications to disk
# ----------------------------------------------------------------------


def apply_plan(
    plan: ConversionPlan,
    app_dir: Path,
    dry_run: bool = False,
    *,
    source_root: Optional[Path] = None,
) -> List[str]:
    """Apply the conversion plan to the filesystem.

    ``source_root`` is the root used to resolve ``copy`` action source
    paths. Defaults to ``app_dir``; pass the original CLI path
    (``analysis.rebased_from``) when monorepo detection rebased so the
    LLM can vendor binaries from elsewhere in the source tree.

    Returns list of applied change descriptions.
    """
    applied: List[str] = []
    resolved_app_dir = app_dir.resolve()
    resolved_source_root = (source_root or app_dir).resolve()

    for mod in plan.modifications:
        target = _resolve_modification_target(mod, resolved_app_dir)
        if target is None:
            continue

        action_desc = f"{mod.action}: {mod.path}"
        if mod.description:
            action_desc += f" ({mod.description})"

        if dry_run:
            applied.append(f"[dry-run] {action_desc}")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)

        executed = (
            _execute_copy(mod, target, resolved_source_root)
            if mod.action == "copy"
            else _execute_text(mod, target)
        )
        if executed:
            applied.append(action_desc)

    return applied


def _resolve_modification_target(
    mod: FileModification, resolved_app_dir: Path
) -> Optional[Path]:
    """Validate a modification and return its resolved target path.

    Returns ``None`` (with a warning) when the modification is malformed
    or escapes the extension directory.
    """
    if not mod.path:
        return None

    if mod.action not in _ALLOWED_ACTIONS:
        console.print(
            f"[yellow]Warning:[/yellow] Unknown action '{mod.action}' for "
            f"'{mod.path}' — skipping"
        )
        return None

    if mod.action == "copy":
        if not mod.source_path:
            console.print(
                f"[yellow]Warning:[/yellow] copy action for '{mod.path}' "
                "missing source_path — skipping"
            )
            return None
    elif not mod.content:
        return None

    target = (resolved_app_dir / mod.path).resolve()
    if not target.is_relative_to(resolved_app_dir):
        console.print(
            f"[yellow]Warning:[/yellow] Skipping '{mod.path}' — path escapes "
            "app directory"
        )
        return None

    return target


def _execute_copy(
    mod: FileModification, target: Path, resolved_source_root: Path
) -> bool:
    """Execute a ``copy`` action. Returns True iff the copy succeeded."""
    source = (resolved_source_root / (mod.source_path or "")).resolve()
    if not source.is_relative_to(resolved_source_root):
        console.print(
            f"[yellow]Warning:[/yellow] copy source '{mod.source_path}' "
            "escapes source tree — skipping"
        )
        return False
    if not source.exists() or not source.is_file():
        console.print(
            f"[yellow]Warning:[/yellow] copy source '{mod.source_path}' "
            "does not exist — skipping"
        )
        return False
    shutil.copy2(source, target)
    return True


def _execute_text(mod: FileModification, target: Path) -> bool:
    """Execute a text-content action (create/modify/append). Always succeeds
    once the target was validated."""
    if mod.action == "append" and target.exists():
        existing = target.read_text(encoding="utf-8")
        target.write_text(existing + "\n" + mod.content, encoding="utf-8")
    else:
        target.write_text(mod.content, encoding="utf-8")
    return True


# ----------------------------------------------------------------------
# Plan post-processing — kamiwaza.json preservation, supporting files,
# manual_items dedup
# ----------------------------------------------------------------------


def _default_metadata(analysis: AnalysisResult, strategy: ConversionStrategy) -> Dict[str, Any]:
    analyzer = AppAnalyzer()
    metadata = analyzer.generate_kamiwaza_json(analysis)
    existing_metadata = _load_existing_kamiwaza_json(analysis.app_dir / "kamiwaza.json")
    if existing_metadata:
        metadata.update(existing_metadata)
    metadata["type"] = strategy.extension_type
    return metadata


def _preserve_existing_kamiwaza_json(plan: ConversionPlan, analysis: AnalysisResult) -> None:
    """Preserve an existing valid manifest, but allow repairs for invalid ones."""
    metadata_path = analysis.app_dir / "kamiwaza.json"
    if not metadata_path.exists():
        return
    if not _is_valid_existing_kamiwaza_json(metadata_path):
        if any(mod.path == "kamiwaza.json" for mod in plan.modifications):
            plan.manual_items = _merge_manual_items(
                plan.manual_items,
                [
                    "Existing kamiwaza.json is invalid; keeping AI-proposed metadata "
                    "repairs so conversion can pass validation.",
                ],
            )
        return

    kept: List[FileModification] = []
    skipped_metadata_change = False
    for mod in plan.modifications:
        if mod.path == "kamiwaza.json":
            skipped_metadata_change = True
            continue
        kept.append(mod)

    if skipped_metadata_change:
        plan.modifications = kept
        # Don't pollute manual_items with a no-action notification; surface
        # in the summary so it lands in the convert run output and notes
        # without prompting the user to "do" anything.
        note = "Preserved existing kamiwaza.json (skipped AI-proposed metadata changes)."
        plan.summary = f"{plan.summary} {note}".strip() if plan.summary else note


def _load_existing_kamiwaza_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _is_valid_existing_kamiwaza_json(path: Path) -> bool:
    from kamiwaza_extensions.validators.metadata import MetadataValidator

    return MetadataValidator().validate(path).passed


def _ensure_supporting_files(
    plan: ConversionPlan,
    analysis: AnalysisResult,
    metadata_seed: Dict[str, Any],
    strategy: ConversionStrategy,
) -> None:
    by_path = {mod.path: mod for mod in plan.modifications}

    if "kamiwaza.json" not in by_path and not (analysis.app_dir / "kamiwaza.json").exists():
        plan.modifications.insert(
            0,
            FileModification(
                path="kamiwaza.json",
                action="create",
                content=json.dumps(metadata_seed, indent=4) + "\n",
                description="generated extension metadata scaffold",
            ),
        )

    if "CONVERT_NOTES.md" not in by_path:
        notes_path = analysis.app_dir / "CONVERT_NOTES.md"
        plan.modifications.append(
            FileModification(
                path="CONVERT_NOTES.md",
                action="modify" if notes_path.exists() else "create",
                content=_build_convert_notes(plan, strategy),
                description="summarized conversion decisions and follow-ups",
            )
        )


def _build_convert_notes(plan: ConversionPlan, strategy: ConversionStrategy) -> str:
    lines = [
        "# Convert Notes",
        "",
        "## Summary",
        plan.summary or "Best-effort Kamiwaza conversion generated by `kz-ext convert`.",
        "",
        "## Strategy",
        f"- Extension type: {strategy.extension_type}",
        f"- Conversion mode: {strategy.conversion_mode}",
        f"- Primary service: {strategy.primary_service}",
    ]
    if strategy.runtime_summary:
        lines.append(f"- Runtime summary: {strategy.runtime_summary}")

    items = _merge_manual_items(strategy.manual_items, plan.manual_items)
    if items:
        lines.extend(["", "## Manual Follow-ups"])
        lines.extend(f"- {item}" for item in items)
    else:
        lines.extend(["", "## Manual Follow-ups", "- None noted by the AI conversion pass."])
    return "\n".join(lines) + "\n"


def _merge_manual_items(*groups: List[str]) -> List[str]:
    seen: set = set()
    merged: List[str] = []
    for group in groups:
        for item in group:
            if item and item not in seen:
                seen.add(item)
                merged.append(item)
    return merged


# Verbs that signal "the user must take this action" — when paired with
# a path the LLM has already scheduled as a modification, the manual
# item is a stale leftover (the LLM hedged after deciding to do the
# work itself). Bare verb form; matched as whole words.
_MANUAL_ACTION_VERBS = (
    "vendor",
    "copy",
    "move",
    "create",
    "add",
    "rebase",
    "rewrite",
    "drop",
    "switch",
    "update",
    "modify",
    "ensure",
    "install",
    "place",
)
_MANUAL_ACTION_VERB_RE = re.compile(
    r"\b(?:" + "|".join(_MANUAL_ACTION_VERBS) + r")\b",
    re.IGNORECASE,
)
# Tokens shorter than this are too generic to reliably distinguish
# "this token is in the manual item because the LLM is referring to my
# scheduled modification" from "this token happens to appear as a
# normal word." E.g., a modification path of ``app.py`` would otherwise
# dedupe a manual item mentioning "the app's behavior."
_MIN_DEDUPE_TOKEN_LEN = 4


def _dedupe_manual_items_against_modifications(plan: ConversionPlan) -> None:
    """Drop manual_items the LLM left despite scheduling the same work.

    LLMs often hedge: they emit a `copy` modification for a wheel AND
    write a manual_item telling the user to "vendor the wheel". The
    manual_item is then misleading — the user thinks the convert didn't
    finish. Strip such items when they (a) name a verb implying the
    user must act, AND (b) reference a path or filename already present
    in the modifications list as a whole-word match.

    Word-boundary matching plus a minimum token length avoids false
    positives like a modification path of ``backend/Dockerfile``
    swallowing "Add Dockerfile to .gitignore" or short basenames
    (``go``, ``web``, ``app``) colliding with words like "goal",
    "webhook", "appears".
    """
    if not plan.manual_items or not plan.modifications:
        return

    referenced: set = set()
    for mod in plan.modifications:
        for raw in (mod.path, mod.source_path):
            if not raw:
                continue
            full = raw.lower()
            base = Path(raw).name.lower()
            for token in (full, base):
                if len(token) >= _MIN_DEDUPE_TOKEN_LEN:
                    referenced.add(token)

    if not referenced:
        return

    referenced_re = re.compile(
        r"\b(?:" + "|".join(re.escape(t) for t in referenced) + r")\b",
        re.IGNORECASE,
    )

    kept: List[str] = []
    for item in plan.manual_items:
        looks_actionable = bool(_MANUAL_ACTION_VERB_RE.search(item))
        mentions_handled_path = bool(referenced_re.search(item))
        if looks_actionable and mentions_handled_path:
            continue
        kept.append(item)
    plan.manual_items = kept
