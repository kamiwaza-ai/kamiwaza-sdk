"""AI-agent-powered conversion for kz-ext convert.

The conversion flow is intentionally AI-led. Deterministic logic is limited to:
- collecting broader repo context
- staging proposed changes in a temporary workspace
- validating the staged output
- asking the model to repair validation failures before applying changes
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from kamiwaza_extensions.app_analyzer import AnalysisResult, AppAnalyzer
from kamiwaza_extensions.constants import COMPOSE_FILENAMES

console = Console(stderr=True)

# Max total content size sent to LLM (characters)
_MAX_CONTEXT_SIZE = 50000
_MAX_REPAIR_ATTEMPTS = 2
_MAX_PREVIOUS_MODIFICATIONS = 20
_STAGING_SKIP_DIRS = (
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".next",
    "build",
    "dist",
    "target",
    "coverage",
)


@dataclass
class FileModification:
    """A single file modification produced by the agent."""

    path: str  # Relative to app_dir
    action: str  # "create", "modify", "append"
    content: str  # Full new content (create/modify) or content to append
    description: str = ""  # What was changed and why


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


def build_prompt(analysis: AnalysisResult) -> str:
    """Backward-compatible entrypoint for prompt inspection tests."""
    return build_strategy_prompt(analysis)


def build_strategy_prompt(analysis: AnalysisResult) -> str:
    """Build the first-pass prompt that asks for a conversion strategy."""
    services_section = _render_services(analysis)
    compat_section = _render_compatibility_issues(analysis)
    files_section = _render_context_files(analysis)
    inventory_section = _render_repo_inventory(analysis)

    return f"""You are planning a best-effort conversion of an existing application into a Kamiwaza extension.

## Goal
Produce a valid Kamiwaza extension repository while preserving the existing application/runtime shape when possible.

## Application
Name: {analysis.app_name}
Current extension type guess: {analysis.extension_type}
Current conversion mode hint: {analysis.conversion_mode}
Description: {analysis.description or 'No description'}

## Repo Inventory
{inventory_section}

## Services
{services_section}

## Compatibility Issues
{compat_section or 'None detected'}

## Current Files
{files_section}

## Kamiwaza Conversion Rules
- Prefer preserving the detected runtime/server and containerization when it already exists.
- If the repo is not containerized, generate the thinnest viable containerization and docker-compose setup.
- Always target a valid repo with `kamiwaza.json`, a compose file, resource limits, and `CONVERT_NOTES.md`.
- Use `type=app` unless there is strong evidence the repo is an MCP/tool or generic background service.
- Kamiwaza deploys extension containers as non-root and expects compatibility with a read-only root filesystem.
- Primary HTTP services should listen on an unprivileged in-container port; prefer `8080` unless the repo clearly requires something else.
- Static/web-server wrappers must use writable runtime paths under `/tmp` when the server needs temp, cache, or pid files.
- Python backend runtime library: `kamiwaza-extensions-lib` is appropriate when there is an actual Python application backend.
- TypeScript runtime library: `@kamiwaza-ai/extensions-lib` is appropriate for real Node/Next frontends.
- Pure static HTML/CSS/JS sites do not need Kamiwaza runtime libraries; they do need a container, compose, resource limits, and a healthable HTTP path.
- If the detected runtime is a poor fit for these constraints, a minimal runtime swap is allowed if it preserves app behavior better than patching a broken root-only setup.
- Keep user source intact. Prefer additive wrappers/config over rewrites.

Return ONLY JSON with this shape:
```json
{{
  "extension_type": "app|tool|service",
  "conversion_mode": "preserve_existing_runtime|add_minimal_wrapper|containerize_repo_root|multi_service",
  "primary_service": "service-name",
  "required_files": ["path/to/file"],
  "runtime_summary": "short summary of the runtime/layout you detected",
  "manual_items": ["manual follow-up if truly unavoidable"]
}}
```
"""


def build_modification_prompt(
    analysis: AnalysisResult,
    strategy: ConversionStrategy,
    metadata_seed: Dict[str, Any],
    *,
    validation: Optional[ValidationSummary] = None,
    previous_plan: Optional[ConversionPlan] = None,
) -> str:
    """Build the prompt that asks the LLM for concrete file changes."""
    files_section = _render_context_files(analysis)
    inventory_section = _render_repo_inventory(analysis)
    services_section = _render_services(analysis)
    compat_section = _render_compatibility_issues(analysis)

    validation_section = ""
    if validation is not None:
        error_lines = "\n".join(f"- {err}" for err in validation.errors) or "- None"
        warning_lines = "\n".join(f"- {warn}" for warn in validation.warnings) or "- None"
        validation_section = f"""
## Validation Feedback From Previous Attempt
Errors:
{error_lines}

Warnings:
{warning_lines}
"""

    previous_plan_section = ""
    if previous_plan is not None and previous_plan.modifications:
        previous_plan_section = f"""
## Previous Proposed Modifications
```json
{json.dumps(_summarize_previous_plan(previous_plan), indent=2)}
```
"""

    return f"""You are converting an existing application into a valid Kamiwaza extension repository.

## Application
Name: {analysis.app_name}
Detected conversion mode: {analysis.conversion_mode}

## Strategy
```json
{json.dumps(_strategy_to_dict(strategy), indent=2)}
```

## Metadata Seed
```json
{json.dumps(metadata_seed, indent=2)}
```

## Repo Inventory
{inventory_section}

## Services
{services_section}

## Compatibility Issues
{compat_section or 'None detected'}

## Current Files
{files_section}
{validation_section}
{previous_plan_section}

## Conversion Requirements
- Preserve the app/runtime shape when possible.
- Create or update `kamiwaza.json`.
- Create or update a compose file so `kz-ext validate` will not fail and `kz-ext dev local` has a clear path.
- Ensure each compose service defines `deploy.resources.limits`.
- Ensure the primary HTTP service has a healthable HTTP path at `/health`.
- Generated services must be compatible with Kamiwaza's non-root, read-only-root-filesystem runtime contract.
- Prefer an unprivileged in-container HTTP port such as `8080`.
- For nginx/static web servers, configure writable temp/pid paths under `/tmp` when needed.
- Generate `CONVERT_NOTES.md` summarizing what changed and any remaining manual follow-ups.
- Only add Kamiwaza runtime libraries when they fit the actual stack.
- Avoid deleting user source files unless a generated wrapper/config file supersedes them.
- Keep the output small and practical: wrappers/config/dockerization are fine; full framework rewrites are not.
- Safe minimal runtime swaps are allowed when they materially improve deploy success on Kamiwaza.

Return ONLY JSON with this shape:
```json
{{
  "modifications": [
    {{
      "path": "relative/path/to/file",
      "action": "create" | "modify" | "append",
      "content": "full file content",
      "description": "what changed and why"
    }}
  ],
  "manual_items": [
    "manual follow-up if still required"
  ],
  "summary": "brief summary"
}}
```
"""


def call_llm(prompt: str) -> Optional[str]:
    """Call an LLM and return the response text.

    Tries providers in order:
    1. OpenAI-compatible (if OPENAI_API_KEY is set and openai is installed)
       — works with OpenAI, Azure, Kamiwaza, vLLM, Ollama, or any
         OpenAI-compatible endpoint. Set OPENAI_BASE_URL to override.
    2. Anthropic (if ANTHROPIC_API_KEY is set and anthropic is installed)
    3. Returns None if no provider is available.

    Override the model with KZ_CONVERT_MODEL env var.
    """
    import os

    model_override = os.environ.get("KZ_CONVERT_MODEL")

    # --- OpenAI-compatible (priority — covers OpenAI, Kamiwaza, vLLM, Ollama, etc.) ---
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        if importlib.util.find_spec("openai") is None:
            console.print(
                "[dim]OPENAI_API_KEY is set but openai package is not installed. "
                "Install with: pip install openai[/dim]"
            )
        else:
            result = _call_openai_compatible(
                prompt,
                api_key=openai_key,
                base_url=os.environ.get("OPENAI_BASE_URL"),
                model=model_override or os.environ.get("OPENAI_MODEL", "gpt-4o"),
            )
            if result is not None:
                return result

    # --- Anthropic (fallback) ---
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        if importlib.util.find_spec("anthropic") is None:
            console.print(
                "[dim]ANTHROPIC_API_KEY is set but anthropic package is not installed. "
                "Install with: pip install kamiwaza-sdk[convert][/dim]"
            )
        else:
            result = _call_anthropic(
                prompt,
                api_key=anthropic_key,
                model=model_override or "claude-sonnet-4-20250514",
            )
            if result is not None:
                return result

    return None


def _call_anthropic(prompt: str, *, api_key: str, model: str) -> Optional[str]:
    """Call the Anthropic Messages API."""
    import anthropic

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
        response = client.messages.create(
            model=model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except (anthropic.APIError, anthropic.APIConnectionError) as exc:
        console.print(f"[yellow]Warning:[/yellow] Anthropic API call failed: {exc}")
        return None
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] Unexpected error in Anthropic call: {exc}")
        return None


def _call_openai_compatible(
    prompt: str,
    *,
    api_key: str,
    base_url: Optional[str] = None,
    model: str = "gpt-4o",
) -> Optional[str]:
    """Call any OpenAI-compatible chat completions API."""
    import openai

    try:
        kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": 120.0}
        if base_url:
            kwargs["base_url"] = base_url

        client = openai.OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
    except (openai.APIError, openai.APIConnectionError) as exc:
        console.print(f"[yellow]Warning:[/yellow] OpenAI-compatible API call failed: {exc}")
        return None
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] Unexpected error in OpenAI call: {exc}")
        return None


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


def apply_plan(plan: ConversionPlan, app_dir: Path, dry_run: bool = False) -> List[str]:
    """Apply the conversion plan to the filesystem.

    Returns list of applied change descriptions.
    """
    applied = []
    resolved_app_dir = app_dir.resolve()
    for mod in plan.modifications:
        if not mod.path or not mod.content:
            continue

        allowed_actions = ("create", "modify", "append")
        if mod.action not in allowed_actions:
            console.print(
                f"[yellow]Warning:[/yellow] Unknown action '{mod.action}' for '{mod.path}' — skipping"
            )
            continue

        target = (resolved_app_dir / mod.path).resolve()
        if not target.is_relative_to(resolved_app_dir):
            console.print(
                f"[yellow]Warning:[/yellow] Skipping '{mod.path}' — path escapes app directory"
            )
            continue

        action_desc = f"{mod.action}: {mod.path}"
        if mod.description:
            action_desc += f" ({mod.description})"

        if dry_run:
            applied.append(f"[dry-run] {action_desc}")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)

        if mod.action == "append" and target.exists():
            existing = target.read_text(encoding="utf-8")
            target.write_text(existing + "\n" + mod.content, encoding="utf-8")
        else:
            target.write_text(mod.content, encoding="utf-8")

        applied.append(action_desc)

    return applied


def run_agent(analysis: AnalysisResult, dry_run: bool = False) -> ConversionPlan:
    """Run the full conversion agent with staged validation and repair."""
    console.print(
        "  [dim]Note: size-capped source context will be sent to an external LLM provider for analysis; "
        "common secret-bearing files such as .env, credentials, and key files are excluded.[/dim]"
    )
    console.print(f"  [dim]Conversion mode: {analysis.conversion_mode}[/dim]")
    console.print("  [dim]Calling AI agent...[/dim]")

    strategy_text = call_llm(build_strategy_prompt(analysis))
    if strategy_text is None:
        return _apply_basic_fallback(analysis, dry_run=dry_run)

    strategy = parse_strategy_response(strategy_text)
    if strategy is None:
        return ConversionPlan(
            success=False,
            mode=analysis.conversion_mode,
            summary="The conversion strategy could not be parsed automatically.",
            manual_items=["Review the repo manually and re-run convert after tightening the app shape."],
            errors=["Conversion strategy could not be parsed."],
        )

    metadata_seed = _default_metadata(analysis, strategy)
    previous_plan: Optional[ConversionPlan] = None
    last_validation = ValidationSummary(passed=False, errors=["No validated plan produced."])

    for attempt in range(_MAX_REPAIR_ATTEMPTS + 1):
        validation = last_validation if attempt > 0 else None
        prompt = build_modification_prompt(
            analysis,
            strategy,
            metadata_seed,
            validation=validation,
            previous_plan=previous_plan,
        )
        response = call_llm(prompt)
        if response is None:
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

        plan = parse_response(response)
        plan.mode = analysis.conversion_mode
        plan.strategy = strategy
        plan.manual_items = _merge_manual_items(strategy.manual_items, plan.manual_items)
        if not plan.success:
            return plan

        _preserve_existing_kamiwaza_json(plan, analysis)
        _ensure_supporting_files(plan, analysis, metadata_seed, strategy)
        last_validation = _validate_plan_in_staging(plan, analysis.app_dir)
        if last_validation.passed:
            plan.success = True
            plan.warnings = last_validation.warnings
            apply_plan(plan, analysis.app_dir, dry_run=dry_run)
            if dry_run:
                plan.summary = f"[Dry run] Validated {len(plan.modifications)} proposed modifications. {plan.summary}".strip()
            return plan

        previous_plan = plan

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


def _parse_json_payload(response_text: str) -> Any:
    json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", response_text, re.DOTALL)
    json_str = json_match.group(1) if json_match else response_text.strip()
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def _render_services(analysis: AnalysisResult) -> str:
    if not analysis.services:
        return "- No services detected yet"
    return "\n".join(
        f"- **{svc.name}**: language={svc.language or 'unknown'}, ports={svc.ports}, dockerfile={'yes' if svc.dockerfile else 'no'}"
        for svc in analysis.services
    )


def _render_compatibility_issues(analysis: AnalysisResult) -> str:
    compat_lines = []
    if analysis.has_host_ports:
        compat_lines.append(
            f"- Host port bindings (auto-stripped during deploy): {', '.join(analysis.has_host_ports)}"
        )
    if analysis.has_bind_mounts:
        compat_lines.append(
            f"- Bind mounts (auto-stripped during deploy): {', '.join(analysis.has_bind_mounts)}"
        )
    if analysis.missing_resource_limits:
        compat_lines.append(
            f"- Missing resource limits: {', '.join(analysis.missing_resource_limits)}"
        )
    if not analysis.has_health_endpoint:
        compat_lines.append("- No health endpoint detected")
    if not analysis.has_python_runtime_lib:
        compat_lines.append("- Python runtime library (kamiwaza-extensions-lib) not installed")
    if not analysis.has_ts_runtime_lib:
        compat_lines.append("- TypeScript runtime library (@kamiwaza-ai/extensions-lib) not installed")
    return "\n".join(compat_lines)


def _render_repo_inventory(analysis: AnalysisResult) -> str:
    sections = []
    if analysis.repo_tree:
        sections.append("Top level:\n" + "\n".join(f"- {entry}" for entry in analysis.repo_tree))
    if analysis.detected_manifests:
        sections.append(
            "Detected manifests:\n" + "\n".join(f"- {entry}" for entry in analysis.detected_manifests[:20])
        )
    if analysis.candidate_entrypoints:
        sections.append(
            "Candidate entrypoints:\n" + "\n".join(f"- {entry}" for entry in analysis.candidate_entrypoints[:20])
        )
    if analysis.runtime_hints:
        sections.append("Runtime hints:\n" + "\n".join(f"- {entry}" for entry in analysis.runtime_hints))
    return "\n\n".join(sections) or "- No notable repo inventory discovered"


def _render_context_files(analysis: AnalysisResult) -> str:
    files_section = ""
    total_chars = 0
    total_files = len(analysis.file_contents)
    included_files = 0
    for rel_path, content in sorted(analysis.file_contents.items()):
        entry = f"\n### {rel_path}\n```\n{content}\n```\n"
        if total_chars + len(entry) > _MAX_CONTEXT_SIZE:
            break
        files_section += entry
        total_chars += len(entry)
        included_files += 1

    omitted = total_files - included_files
    if omitted > 0:
        files_section += f"\n*Note: {omitted} file(s) omitted due to context size limits.*\n"
    return files_section or "\n*(No file contents gathered.)*\n"


def _default_metadata(analysis: AnalysisResult, strategy: ConversionStrategy) -> Dict[str, Any]:
    analyzer = AppAnalyzer()
    metadata = analyzer.generate_kamiwaza_json(analysis)
    existing_metadata = _load_existing_kamiwaza_json(analysis.app_dir / "kamiwaza.json")
    if existing_metadata:
        metadata.update(existing_metadata)
    metadata["type"] = strategy.extension_type
    return metadata


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
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY for full AI-powered conversion.",
            "For other providers, set OPENAI_API_KEY + OPENAI_BASE_URL (any OpenAI-compatible API).",
            "Review kamiwaza.json, then rerun convert with an LLM to attempt compose and runtime integration.",
        ],
    )
    _ensure_supporting_files(plan, analysis, metadata_seed, strategy)
    apply_plan(plan, analysis.app_dir, dry_run=dry_run)
    if dry_run:
        plan.summary = f"[Dry run] {plan.summary}"
    return plan


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
                    "Existing kamiwaza.json is invalid; keeping AI-proposed metadata repairs so conversion can pass validation.",
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
        plan.manual_items = _merge_manual_items(
            plan.manual_items,
            [
                "kamiwaza.json already exists; preserving the existing manifest and skipping AI-proposed metadata changes.",
            ],
        )


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


def _validate_plan_in_staging(plan: ConversionPlan, app_dir: Path) -> ValidationSummary:
    from kamiwaza_extensions.validators.compose import (
        ComposeValidator,
        is_missing_resource_limits_warning,
    )
    from kamiwaza_extensions.validators.metadata import MetadataValidator
    from kamiwaza_extensions.validators.platform_runtime import PlatformRuntimeValidator

    with tempfile.TemporaryDirectory(prefix="kz-ext-convert-") as tmp_dir:
        staged_root = Path(tmp_dir) / app_dir.name
        shutil.copytree(
            app_dir,
            staged_root,
            ignore=shutil.ignore_patterns(*_STAGING_SKIP_DIRS),
            dirs_exist_ok=True,
        )
        apply_plan(plan, staged_root, dry_run=False)

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


def _find_compose_file(ext_dir: Path) -> Optional[Path]:
    for name in COMPOSE_FILENAMES:
        candidate = ext_dir / name
        if candidate.exists():
            return candidate
    return None


def _summarize_previous_plan(plan: ConversionPlan) -> Dict[str, Any]:
    summarized_mods = []
    for mod in plan.modifications[:_MAX_PREVIOUS_MODIFICATIONS]:
        summarized_mods.append(
            {
                "path": mod.path,
                "action": mod.action,
                "description": mod.description,
                "content_chars": len(mod.content or ""),
            }
        )
    omitted = max(0, len(plan.modifications) - len(summarized_mods))
    summary: Dict[str, Any] = {
        "modifications": summarized_mods,
        "manual_items": plan.manual_items,
        "summary": plan.summary,
    }
    if omitted:
        summary["omitted_modifications"] = omitted
    return summary


def _strategy_to_dict(strategy: ConversionStrategy) -> Dict[str, Any]:
    return {
        "extension_type": strategy.extension_type,
        "conversion_mode": strategy.conversion_mode,
        "primary_service": strategy.primary_service,
        "required_files": strategy.required_files,
        "runtime_summary": strategy.runtime_summary,
        "manual_items": strategy.manual_items,
    }


def _merge_manual_items(*groups: List[str]) -> List[str]:
    seen = set()
    merged: List[str] = []
    for group in groups:
        for item in group:
            if item and item not in seen:
                seen.add(item)
                merged.append(item)
    return merged
