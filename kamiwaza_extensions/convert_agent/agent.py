"""High-level orchestration for the convert agent.

``run_agent`` drives the full pipeline:

1. Strategy LLM call → ``ConversionStrategy``
2. Up to ``_MAX_REPAIR_ATTEMPTS + 1`` modification rounds. Each round:
   a. Modification LLM call → ``ConversionPlan``
   b. Post-process: preserve existing kamiwaza.json, ensure supporting
      files, dedupe stale manual_items
   c. Stage and validate
   d. If validation passes, apply for real and return
   e. Otherwise feed validation errors back to the LLM for repair
3. If the LLM is unavailable, fall back to a basic
   metadata-only scaffold so the user still gets a starting point.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from rich.console import Console

from kamiwaza_extensions.app_analyzer import AnalysisResult
from kamiwaza_extensions.constants import COMPOSE_FILENAMES
from kamiwaza_extensions.convert_agent.models import (
    ConversionPlan,
    ConversionStrategy,
    ValidationSummary,
)
from kamiwaza_extensions.convert_agent.plan import (
    _default_metadata,
    _dedupe_manual_items_against_modifications,
    _ensure_supporting_files,
    _merge_manual_items,
    _preserve_existing_kamiwaza_json,
    apply_plan,
    parse_response,
    parse_strategy_response,
)
from kamiwaza_extensions.convert_agent.prompts import (
    build_modification_prompt,
    build_strategy_prompt,
)
from kamiwaza_extensions.convert_agent.providers import call_llm
from kamiwaza_extensions.monorepo import SKIP_DIRS

console = Console(stderr=True)

_MAX_REPAIR_ATTEMPTS = 2
# ``shutil.copytree`` ignore-patterns wants positional args; the shared
# SKIP_DIRS frozenset materializes as a tuple at staging time.
_STAGING_SKIP_DIRS = tuple(sorted(SKIP_DIRS))


def run_agent(analysis: AnalysisResult, dry_run: bool = False) -> ConversionPlan:
    """Run the full conversion agent with staged validation and repair."""
    _print_run_banner(analysis)

    strategy_text = call_llm(build_strategy_prompt(analysis))
    if strategy_text is None:
        return _apply_basic_fallback(analysis, dry_run=dry_run)

    strategy = parse_strategy_response(strategy_text)
    if strategy is None:
        return _strategy_unparseable_failure(analysis)

    metadata_seed = _default_metadata(analysis, strategy)
    previous_plan: Optional[ConversionPlan] = None
    last_validation = ValidationSummary(passed=False, errors=["No validated plan produced."])

    for attempt in range(_MAX_REPAIR_ATTEMPTS + 1):
        plan, last_validation = _run_modification_round(
            analysis,
            strategy,
            metadata_seed,
            previous_plan=previous_plan,
            previous_validation=last_validation if attempt > 0 else None,
        )
        if plan is None:
            return _llm_unavailable_failure(analysis, strategy)
        if not plan.success and not last_validation.passed:
            return plan  # parse failure on LLM response — surface as-is
        if last_validation.passed:
            return _apply_validated_plan(plan, analysis, dry_run=dry_run)
        previous_plan = plan

    return _repair_exhausted_failure(analysis, strategy, previous_plan, last_validation)


def _print_run_banner(analysis: AnalysisResult) -> None:
    console.print(
        "  [dim]Note: size-capped source context will be sent to an external LLM "
        "provider for analysis; common secret-bearing files such as .env, "
        "credentials, and key files are excluded.[/dim]"
    )
    console.print(f"  [dim]Conversion mode: {analysis.conversion_mode}[/dim]")
    console.print("  [dim]Calling AI agent...[/dim]")


def _apply_validated_plan(
    plan: ConversionPlan, analysis: AnalysisResult, *, dry_run: bool
) -> ConversionPlan:
    """Write a validated plan to disk and tag the summary in dry-run mode."""
    apply_plan(
        plan,
        analysis.app_dir,
        dry_run=dry_run,
        source_root=analysis.rebased_from or analysis.app_dir,
    )
    if dry_run:
        plan.summary = (
            f"[Dry run] Validated {len(plan.modifications)} proposed "
            f"modifications. {plan.summary}"
        ).strip()
    return plan


def _run_modification_round(
    analysis: AnalysisResult,
    strategy: ConversionStrategy,
    metadata_seed: dict,
    *,
    previous_plan: Optional[ConversionPlan],
    previous_validation: Optional[ValidationSummary],
) -> tuple[Optional[ConversionPlan], ValidationSummary]:
    """Run one modification LLM round + post-processing + staging validation.

    Returns ``(plan, validation)``. ``plan`` is ``None`` when the LLM
    fell through; the caller handles that as "LLM unavailable". When
    the parsed plan itself reports failure, ``plan.success`` is False
    and ``validation`` carries an empty (passed=False) summary.
    """
    prompt = build_modification_prompt(
        analysis,
        strategy,
        metadata_seed,
        validation=previous_validation,
        previous_plan=previous_plan,
    )
    response = call_llm(prompt)
    if response is None:
        return None, ValidationSummary(passed=False, errors=["LLM unavailable."])

    plan = parse_response(response)
    plan.mode = analysis.conversion_mode
    plan.strategy = strategy
    plan.manual_items = _merge_manual_items(strategy.manual_items, plan.manual_items)
    if not plan.success:
        return plan, ValidationSummary(passed=False, errors=plan.errors)

    _preserve_existing_kamiwaza_json(plan, analysis)
    # Dedupe BEFORE rendering CONVERT_NOTES.md so the notes don't carry
    # stale manual_items (the rendered file is consumed by users who
    # may not see the deduped CLI output). _ensure_supporting_files
    # calls _build_convert_notes which materializes manual_items into
    # the notes body — running it after the dedupe keeps the two
    # surfaces consistent.
    _dedupe_manual_items_against_modifications(plan)
    _ensure_supporting_files(plan, analysis, metadata_seed, strategy)
    validation = _validate_plan_in_staging(
        plan,
        analysis.app_dir,
        source_root=analysis.rebased_from or analysis.app_dir,
    )
    if validation.passed:
        plan.success = True
        plan.warnings = validation.warnings
    return plan, validation


def _strategy_unparseable_failure(analysis: AnalysisResult) -> ConversionPlan:
    return ConversionPlan(
        success=False,
        mode=analysis.conversion_mode,
        summary="The conversion strategy could not be parsed automatically.",
        manual_items=[
            "Review the repo manually and re-run convert after tightening the app shape."
        ],
        errors=["Conversion strategy could not be parsed."],
    )


def _repair_exhausted_failure(
    analysis: AnalysisResult,
    strategy: ConversionStrategy,
    previous_plan: Optional[ConversionPlan],
    last_validation: ValidationSummary,
) -> ConversionPlan:
    return ConversionPlan(
        modifications=previous_plan.modifications if previous_plan else [],
        manual_items=_merge_manual_items(
            strategy.manual_items,
            ["Automatic conversion could not satisfy validation after repair attempts."],
        ),
        summary="Conversion could not be validated automatically.",
        success=False,
        errors=last_validation.errors,
        warnings=last_validation.warnings,
        mode=analysis.conversion_mode,
        strategy=strategy,
    )


def _llm_unavailable_failure(
    analysis: AnalysisResult, strategy: ConversionStrategy
) -> ConversionPlan:
    """Failure plan when the LLM falls through during the modification loop."""
    return ConversionPlan(
        success=False,
        mode=analysis.conversion_mode,
        strategy=strategy,
        summary="LLM became unavailable before a validated conversion could be produced.",
        manual_items=_merge_manual_items(
            strategy.manual_items,
            ["Re-run convert once LLM access is available again."],
        ),
        errors=["LLM unavailable during modification generation."],
    )


def _apply_basic_fallback(analysis: AnalysisResult, *, dry_run: bool) -> ConversionPlan:
    """Apply the documented non-LLM fallback scaffold."""
    strategy = ConversionStrategy(
        extension_type=analysis.extension_type,
        conversion_mode=analysis.conversion_mode,
        primary_service=analysis.services[0].name if analysis.services else "app",
        runtime_summary=analysis.description or "Basic metadata-only fallback",
    )
    metadata_seed = _default_metadata(analysis, strategy)
    plan = ConversionPlan(
        mode=analysis.conversion_mode,
        strategy=strategy,
        summary="LLM unavailable — created a basic Kamiwaza scaffold only.",
        manual_items=[
            "Install the `claude` (Claude Code) or `codex` CLI to use your existing "
            "subscription — no API key required.",
            "Or set ANTHROPIC_API_KEY / OPENAI_API_KEY for full AI-powered conversion.",
            "For other providers, set OPENAI_API_KEY + OPENAI_BASE_URL (any "
            "OpenAI-compatible API).",
            "Review kamiwaza.json, then rerun convert with an LLM to attempt compose "
            "and runtime integration.",
        ],
    )
    _ensure_supporting_files(plan, analysis, metadata_seed, strategy)
    apply_plan(
        plan,
        analysis.app_dir,
        dry_run=dry_run,
        source_root=analysis.rebased_from or analysis.app_dir,
    )
    if dry_run:
        plan.summary = f"[Dry run] {plan.summary}"
    return plan


def _validate_plan_in_staging(
    plan: ConversionPlan,
    app_dir: Path,
    *,
    source_root: Optional[Path] = None,
) -> ValidationSummary:
    """Materialize the plan in a temp copy and run the validators against it."""
    from kamiwaza_extensions.validators.compose import (
        ComposeValidator,
        is_missing_resource_limits_warning,
    )
    from kamiwaza_extensions.validators.metadata import MetadataValidator
    from kamiwaza_extensions.validators.platform_runtime import PlatformRuntimeValidator

    with tempfile.TemporaryDirectory(prefix="kz-ext-convert-") as tmp_dir:
        staged_root = Path(tmp_dir) / app_dir.name
        # symlinks=True preserves links instead of byte-copying targets.
        # Without this a symlink to /var/log or any large external tree
        # explodes disk usage in staging, and cycles cause copytree to
        # raise OSError (or, in older versions, recurse).
        shutil.copytree(
            app_dir,
            staged_root,
            ignore=shutil.ignore_patterns(*_STAGING_SKIP_DIRS),
            dirs_exist_ok=True,
            symlinks=True,
        )
        # Strip any symlink whose target resolves OUTSIDE staged_root
        # — those would let validators read external content as if it
        # were the manifest, and any LLM-emitted modify-action would
        # write through them to the real source tree (apply_plan's
        # path-traversal guard catches the write side, but a
        # validator following an outward symlink to e.g. /etc/...
        # is still a sensitive-file leak surface).
        _strip_outward_symlinks(staged_root)
        # Copy actions resolve their source against the original source
        # tree (the monorepo root in rebased cases), not the staged copy.
        # ``apply_plan`` only reads from source_root (never mutates it);
        # any future action that touches source must preserve that
        # invariant.
        apply_plan(plan, staged_root, dry_run=False, source_root=source_root or app_dir)

        errors: List[str] = []
        warnings: List[str] = []

        metadata_path = staged_root / "kamiwaza.json"
        if not metadata_path.exists():
            errors.append("No kamiwaza.json found after conversion.")
        else:
            meta_result = MetadataValidator().validate(metadata_path)
            errors.extend(meta_result.errors)
            warnings.extend(meta_result.warnings)

        compose_path = _find_compose_file(staged_root)
        if compose_path is None:
            errors.append("No docker-compose file found after conversion.")
        else:
            compose_result = ComposeValidator().validate(compose_path, staged_root)
            errors.extend(compose_result.errors)
            warnings.extend(compose_result.warnings)
            for warning in compose_result.warnings:
                if is_missing_resource_limits_warning(warning):
                    errors.append(f"Blocking conversion warning: {warning}")
            if compose_result.passed:
                runtime_result = PlatformRuntimeValidator().validate(compose_path, staged_root)
                errors.extend(runtime_result.errors)
                warnings.extend(runtime_result.warnings)

        if not (staged_root / "CONVERT_NOTES.md").exists():
            errors.append("CONVERT_NOTES.md was not generated.")

        return ValidationSummary(passed=len(errors) == 0, errors=errors, warnings=warnings)


def _strip_outward_symlinks(staged_root: Path) -> None:
    """Remove symlinks under *staged_root* whose targets resolve outside it.

    Defence in depth on the staged validation tree. ``shutil.copytree
    (symlinks=True)`` preserves links as links — fast and disk-safe,
    but a symlink to ``/etc/secret`` would let a validator read that
    content as if it were the manifest. ``apply_plan``'s
    path-traversal guard catches the *write* side (resolves target
    against staged_root before writing), but readers (validators)
    have no such guard. Stripping outward links closes that gap.

    Symlinks whose target resolves *inside* staged_root are kept —
    those represent legitimate intra-extension links that the
    validators may need to read.
    """
    resolved_root = staged_root.resolve()
    for dirpath, dirnames, filenames in os.walk(staged_root, followlinks=False):
        for name in list(dirnames) + list(filenames):
            entry = Path(dirpath) / name
            if not entry.is_symlink():
                continue
            try:
                target_resolved = entry.resolve()
            except (OSError, RuntimeError):
                # Broken / unreadable link, or symlink cycle (resolve()
                # raises RuntimeError on loops, not OSError) — safest
                # to remove.
                _safe_unlink(entry)
                continue
            if not target_resolved.is_relative_to(resolved_root):
                _safe_unlink(entry)


def _safe_unlink(path: Path) -> None:
    """Best-effort unlink that tolerates missing-file races."""
    try:
        path.unlink()
    except (OSError, FileNotFoundError):
        pass


def _find_compose_file(ext_dir: Path) -> Optional[Path]:
    for name in COMPOSE_FILENAMES:
        candidate = ext_dir / name
        if candidate.exists():
            return candidate
    return None
