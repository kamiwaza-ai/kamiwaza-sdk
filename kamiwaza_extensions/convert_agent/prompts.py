"""Prompt construction for the convert agent.

Each prompt has two sections of content:

1. **Standing rules** — delivered out-of-band via ``CLAUDE.md`` /
   ``AGENTS.md`` (CLI agents) or system prompt (API agents). See
   ``providers.py`` for how the ``agent_guidance.md`` text is wired in.
2. **Per-call context** — the analyzer's findings about the specific
   repo, plus task-specific data (strategy, validation feedback,
   previous attempt). That's what these builders assemble.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from kamiwaza_extensions.app_analyzer import AnalysisResult
from kamiwaza_extensions.convert_agent.models import (
    ConversionPlan,
    ConversionStrategy,
    ValidationSummary,
)

# Max total content size sent to LLM (characters). Cap on the
# ``Current Files`` section, not the whole prompt.
_MAX_CONTEXT_SIZE = 50000
# Cap on how many previous-plan modifications we summarize back to the
# model on a repair attempt — keeps the prompt bounded.
_MAX_PREVIOUS_MODIFICATIONS = 20


def build_prompt(analysis: AnalysisResult) -> str:
    """Backward-compatible entrypoint for prompt inspection tests."""
    return build_strategy_prompt(analysis)


def build_strategy_prompt(analysis: AnalysisResult) -> str:
    """Build the first-pass prompt that asks for a conversion strategy."""
    services_section = _render_services(analysis)
    compat_section = _render_compatibility_issues(analysis)
    files_section = _render_context_files(analysis)
    inventory_section = _render_repo_inventory(analysis)
    monorepo_section = _render_monorepo_inventory(analysis)

    return f"""You are planning a best-effort conversion of an existing application into a Kamiwaza extension.

Follow the standing rules in the Kamiwaza Extension Authoring Guidance you have been given as system context (delivered as `CLAUDE.md` / `AGENTS.md` for CLI agents, or the system prompt for API agents). The data below is the per-call context.

## Application
Name: {analysis.app_name}
Current extension type guess: {analysis.extension_type}
Current conversion mode hint: {analysis.conversion_mode}
Description: {analysis.description or 'No description'}

## Repo Inventory
{inventory_section}
{monorepo_section}
## Services
{services_section}

## Compatibility Issues
{compat_section or 'None detected'}

## Current Files
{files_section}

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


_MODIFICATION_PROMPT_TEMPLATE = """You are converting an existing application into a valid Kamiwaza extension repository.

Follow the standing rules in the Kamiwaza Extension Authoring Guidance you have been given as system context (CLAUDE.md / AGENTS.md for CLI agents, or system prompt for API agents). It defines the runtime contract (Chainguard distroless images, exec-form CMD/ENTRYPOINT, read-only root, non-root user, port conventions), the `copy` action for vendoring binary deps, and the `manual_items` discipline. The data below is the per-call context.

## Application
Name: {app_name}
Detected conversion mode: {conversion_mode}

## Strategy
```json
{strategy_json}
```

## Metadata Seed
```json
{metadata_json}
```

## Repo Inventory
{inventory_section}
{monorepo_section}
## Services
{services_section}

## Compatibility Issues
{compat_section}

## Current Files
{files_section}
{validation_section}
{previous_plan_section}

Return ONLY JSON with this shape:
```json
{{
  "modifications": [
    {{
      "path": "relative/path/to/file",
      "action": "create" | "modify" | "append" | "copy",
      "content": "full file content (omit for action=copy)",
      "source_path": "for action=copy: source path within the source tree",
      "description": "what changed and why"
    }}
  ],
  "manual_items": [
    "manual follow-up genuinely required of the user (leave empty if everything is encoded as modifications)"
  ],
  "summary": "brief summary"
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
    return _MODIFICATION_PROMPT_TEMPLATE.format(
        app_name=analysis.app_name,
        conversion_mode=analysis.conversion_mode,
        strategy_json=json.dumps(_strategy_to_dict(strategy), indent=2),
        metadata_json=json.dumps(metadata_seed, indent=2),
        inventory_section=_render_repo_inventory(analysis),
        monorepo_section=_render_monorepo_inventory(analysis),
        services_section=_render_services(analysis),
        compat_section=_render_compatibility_issues(analysis) or "None detected",
        files_section=_render_context_files(analysis),
        validation_section=_render_validation_feedback(validation),
        previous_plan_section=_render_previous_plan(previous_plan),
    )


# ----------------------------------------------------------------------
# Section renderers
# ----------------------------------------------------------------------


def _render_services(analysis: AnalysisResult) -> str:
    if not analysis.services:
        return "- No services detected yet"
    return "\n".join(
        f"- **{svc.name}**: language={svc.language or 'unknown'}, "
        f"ports={svc.ports}, dockerfile={'yes' if svc.dockerfile else 'no'}"
        for svc in analysis.services
    )


def _render_compatibility_issues(analysis: AnalysisResult) -> str:
    compat_lines: List[str] = []
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
    sections: List[str] = []
    if analysis.repo_tree:
        sections.append("Top level:\n" + "\n".join(f"- {entry}" for entry in analysis.repo_tree))
    if analysis.detected_manifests:
        sections.append(
            "Detected manifests:\n"
            + "\n".join(f"- {entry}" for entry in analysis.detected_manifests[:20])
        )
    if analysis.candidate_entrypoints:
        sections.append(
            "Candidate entrypoints:\n"
            + "\n".join(f"- {entry}" for entry in analysis.candidate_entrypoints[:20])
        )
    if analysis.runtime_hints:
        sections.append("Runtime hints:\n" + "\n".join(f"- {entry}" for entry in analysis.runtime_hints))
    return "\n\n".join(sections) or "- No notable repo inventory discovered"


def _render_monorepo_inventory(analysis: AnalysisResult) -> str:
    """Surface files outside the rebased ext root that the LLM can ``copy``.

    Returns an empty string when no monorepo rebase happened. Otherwise
    renders a Markdown section listing the broader source tree (capped)
    and any vendor-able binary artifacts (.whl / .tgz / etc.) so the LLM
    knows what to vendor in via ``copy`` modifications.
    """
    if not analysis.monorepo_inventory and not analysis.vendorable_artifacts:
        return ""

    sections: List[str] = ["", "## Source Tree Outside Extension Root"]
    sections.append(
        "Files below live in the original CLI directory but outside the "
        "rebased extension root. You may reference them as `source_path` "
        "in `copy` modifications to vendor binaries (wheels, tarballs, "
        "images) or import shared text files into the extension."
    )

    if analysis.vendorable_artifacts:
        sections.append("")
        sections.append("**Vendorable binary artifacts:**")
        sections.extend(f"- {item}" for item in analysis.vendorable_artifacts[:50])

    if analysis.monorepo_inventory:
        sections.append("")
        sections.append("**Other files in the source tree (capped):**")
        sections.extend(f"- {item}" for item in analysis.monorepo_inventory[:60])

    sections.append("")
    return "\n".join(sections)


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


def _render_validation_feedback(validation: Optional[ValidationSummary]) -> str:
    if validation is None:
        return ""
    error_lines = "\n".join(f"- {err}" for err in validation.errors) or "- None"
    warning_lines = "\n".join(f"- {warn}" for warn in validation.warnings) or "- None"
    info_lines = "\n".join(f"- {info}" for info in validation.info) or "- None"
    return f"""
## Validation Feedback From Previous Attempt
Errors:
{error_lines}

Warnings:
{warning_lines}

Info:
{info_lines}
"""


def _render_previous_plan(previous_plan: Optional[ConversionPlan]) -> str:
    if previous_plan is None or not previous_plan.modifications:
        return ""
    return f"""
## Previous Proposed Modifications
```json
{json.dumps(_summarize_previous_plan(previous_plan), indent=2)}
```
"""


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
