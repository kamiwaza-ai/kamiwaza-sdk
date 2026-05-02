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
import os
import re
import shutil
import subprocess
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

    monorepo_section = _render_monorepo_inventory(analysis)

    return f"""You are converting an existing application into a valid Kamiwaza extension repository.

Follow the standing rules in the Kamiwaza Extension Authoring Guidance you have been given as system context (CLAUDE.md / AGENTS.md for CLI agents, or system prompt for API agents). It defines the runtime contract (Chainguard distroless images, exec-form CMD/ENTRYPOINT, read-only root, non-root user, port conventions), the `copy` action for vendoring binary deps, and the `manual_items` discipline. The data below is the per-call context.

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
{monorepo_section}
## Services
{services_section}

## Compatibility Issues
{compat_section or 'None detected'}

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


# Subprocess timeout for CLI provider calls. Larger than the SDK timeout
# (120s) because CLI invocations include cold-start, OAuth refresh, and
# the model's full reasoning budget for a single one-shot prompt that may
# carry up to ``_MAX_CONTEXT_SIZE`` characters of repo context.
_CLI_TIMEOUT_SECONDS = 300
# Provider tags used by ``KZ_CONVERT_PROVIDER`` to force one path.
_VALID_PROVIDERS = ("auto", "claude-cli", "codex-cli", "openai", "anthropic")
# Canonical Kamiwaza extension authoring rules. Loaded once and used by
# all providers — written as ``CLAUDE.md``/``AGENTS.md`` to the CLI
# providers' working directories so claude/codex auto-discover it, and
# prepended to API-provider prompts as a ``# System Guidance`` section.
_GUIDANCE_PATH = Path(__file__).resolve().parent / "agent_guidance.md"


def _load_agent_guidance() -> str:
    """Return the canonical extension authoring rules, or '' if missing.

    Cached at import-time via the module-level ``_AGENT_GUIDANCE``
    constant below. A missing guidance file is non-fatal — providers
    still get the per-call prompt.
    """
    try:
        return _GUIDANCE_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


_AGENT_GUIDANCE = _load_agent_guidance()


def call_llm(prompt: str) -> Optional[str]:
    """Call an LLM and return the response text.

    Provider order (in ``auto`` mode, the default):

    1. ``claude`` CLI (Claude Code) if installed — uses the developer's
       Claude Pro/Max subscription, no API key required.
    2. ``codex`` CLI if installed — uses the developer's ChatGPT
       Plus subscription, no API key required.
    3. OpenAI-compatible API (if ``OPENAI_API_KEY`` is set and ``openai``
       is installed). Works with OpenAI, Azure, Kamiwaza, vLLM, Ollama,
       or any OpenAI-compatible endpoint via ``OPENAI_BASE_URL``.
    4. Anthropic API (if ``ANTHROPIC_API_KEY`` is set and ``anthropic``
       is installed).

    Set ``KZ_CONVERT_PROVIDER`` to one of ``claude-cli``, ``codex-cli``,
    ``openai``, ``anthropic`` to force a specific provider. Override the
    API model with ``KZ_CONVERT_MODEL``.

    Returns ``None`` if no provider is available.
    """
    provider = (os.environ.get("KZ_CONVERT_PROVIDER") or "auto").strip().lower()
    if provider not in _VALID_PROVIDERS:
        console.print(
            f"[yellow]Warning:[/yellow] Unknown KZ_CONVERT_PROVIDER='{provider}'. "
            f"Falling back to auto. Valid values: {', '.join(_VALID_PROVIDERS)}."
        )
        provider = "auto"

    model_override = os.environ.get("KZ_CONVERT_MODEL")

    # --- Claude CLI (subscription, no API key) ---
    if provider in ("auto", "claude-cli"):
        claude_path = shutil.which("claude")
        if claude_path:
            result = _call_claude_cli(prompt, binary=claude_path, model=model_override)
            if result is not None:
                return result
        elif provider == "claude-cli":
            console.print(
                "[yellow]Warning:[/yellow] KZ_CONVERT_PROVIDER=claude-cli but `claude` "
                "is not on PATH. Install Claude Code or use a different provider."
            )

    # --- Codex CLI (subscription, no API key) ---
    if provider in ("auto", "codex-cli"):
        codex_path = shutil.which("codex")
        if codex_path:
            result = _call_codex_cli(prompt, binary=codex_path, model=model_override)
            if result is not None:
                return result
        elif provider == "codex-cli":
            console.print(
                "[yellow]Warning:[/yellow] KZ_CONVERT_PROVIDER=codex-cli but `codex` "
                "is not on PATH. Install the Codex CLI or use a different provider."
            )

    # --- OpenAI-compatible API ---
    if provider in ("auto", "openai"):
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
        elif provider == "openai":
            console.print(
                "[yellow]Warning:[/yellow] KZ_CONVERT_PROVIDER=openai but "
                "OPENAI_API_KEY is not set."
            )

    # --- Anthropic API ---
    if provider in ("auto", "anthropic"):
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
        elif provider == "anthropic":
            console.print(
                "[yellow]Warning:[/yellow] KZ_CONVERT_PROVIDER=anthropic but "
                "ANTHROPIC_API_KEY is not set."
            )

    return None


def _call_claude_cli(
    prompt: str,
    *,
    binary: str,
    model: Optional[str] = None,
) -> Optional[str]:
    """Invoke Claude Code in non-interactive mode using the user's subscription auth.

    Runs ``claude --print --no-session-persistence`` with the prompt piped
    on stdin. Executes from a temp cwd so the CLI does not pick up the
    user's project ``CLAUDE.md`` / ``AGENTS.md``. We then seed our own
    ``CLAUDE.md`` (and ``AGENTS.md``, harmlessly) with the canonical
    Kamiwaza extension authoring rules so claude auto-discovers them.
    """
    cmd = [binary, "--print", "--no-session-persistence", "--output-format", "text"]
    if model:
        cmd += ["--model", model]
    return _run_cli_subprocess(
        cmd,
        prompt,
        label="claude CLI",
        context_files={"CLAUDE.md": _AGENT_GUIDANCE, "AGENTS.md": _AGENT_GUIDANCE},
    )


def _call_codex_cli(
    prompt: str,
    *,
    binary: str,
    model: Optional[str] = None,
) -> Optional[str]:
    """Invoke Codex CLI non-interactively using the user's subscription auth.

    Runs ``codex exec --skip-git-repo-check -`` so the prompt is read from
    stdin. Executes from a temp cwd to avoid the CLI surfacing
    repo-specific context — that temp cwd is not a git repo, hence the
    ``--skip-git-repo-check`` flag. We seed ``AGENTS.md`` (and ``CLAUDE.md``,
    harmlessly) with the canonical Kamiwaza authoring rules so codex
    auto-discovers them.
    """
    cmd = [binary, "exec", "--skip-git-repo-check"]
    if model:
        cmd += ["--model", model]
    cmd.append("-")
    return _run_cli_subprocess(
        cmd,
        prompt,
        label="codex CLI",
        context_files={"AGENTS.md": _AGENT_GUIDANCE, "CLAUDE.md": _AGENT_GUIDANCE},
    )


def _run_cli_subprocess(
    cmd: List[str],
    prompt: str,
    *,
    label: str,
    context_files: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Shared subprocess shape for CLI providers. Returns stdout or None.

    ``context_files`` maps filename → content; each is written into the
    temp cwd before the CLI is invoked. CLI agents (claude, codex)
    auto-discover ``CLAUDE.md`` / ``AGENTS.md`` and use them as durable
    system guidance, so this is the canonical way to pass standing
    instructions without bloating the per-call prompt.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="kz-ext-llm-") as tmp_cwd:
            for filename, content in (context_files or {}).items():
                if not content:
                    continue
                (Path(tmp_cwd) / filename).write_text(content, encoding="utf-8")
            completed = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=_CLI_TIMEOUT_SECONDS,
                cwd=tmp_cwd,
                check=False,
            )
    except subprocess.TimeoutExpired:
        console.print(
            f"[yellow]Warning:[/yellow] {label} timed out after "
            f"{_CLI_TIMEOUT_SECONDS}s — falling through."
        )
        return None
    except OSError as exc:
        console.print(f"[yellow]Warning:[/yellow] {label} failed to launch: {exc}")
        return None

    if completed.returncode != 0:
        stderr_tail = (completed.stderr or "").strip().splitlines()[-3:]
        detail = " | ".join(stderr_tail) if stderr_tail else "no stderr"
        console.print(
            f"[yellow]Warning:[/yellow] {label} exited {completed.returncode}: {detail}"
        )
        return None

    output = (completed.stdout or "").strip()
    if not output:
        console.print(f"[yellow]Warning:[/yellow] {label} returned empty output.")
        return None
    return output


def _call_anthropic(prompt: str, *, api_key: str, model: str) -> Optional[str]:
    """Call the Anthropic Messages API.

    Passes the canonical Kamiwaza authoring rules as the ``system``
    prompt so the model gets the same standing guidance the CLI agents
    receive via ``CLAUDE.md`` / ``AGENTS.md``.
    """
    import anthropic

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        }
        if _AGENT_GUIDANCE:
            kwargs["system"] = _AGENT_GUIDANCE
        response = client.messages.create(**kwargs)
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
    """Call any OpenAI-compatible chat completions API.

    Passes the canonical Kamiwaza authoring rules as a leading
    ``system`` message so the model gets the same standing guidance
    the CLI agents receive via ``CLAUDE.md`` / ``AGENTS.md``.
    """
    import openai

    try:
        kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": 120.0}
        if base_url:
            kwargs["base_url"] = base_url

        client = openai.OpenAI(**kwargs)
        messages: List[Dict[str, str]] = []
        if _AGENT_GUIDANCE:
            messages.append({"role": "system", "content": _AGENT_GUIDANCE})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model=model,
            max_tokens=8192,
            messages=messages,
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
    applied = []
    resolved_app_dir = app_dir.resolve()
    resolved_source_root = (source_root or app_dir).resolve()
    for mod in plan.modifications:
        if not mod.path:
            continue

        allowed_actions = ("create", "modify", "append", "copy")
        if mod.action not in allowed_actions:
            console.print(
                f"[yellow]Warning:[/yellow] Unknown action '{mod.action}' for '{mod.path}' — skipping"
            )
            continue

        # text-content actions require content; copy requires source_path
        if mod.action == "copy":
            if not mod.source_path:
                console.print(
                    f"[yellow]Warning:[/yellow] copy action for '{mod.path}' missing source_path — skipping"
                )
                continue
        elif not mod.content:
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

        if mod.action == "copy":
            source = (resolved_source_root / (mod.source_path or "")).resolve()
            # Source must live under the source tree we were handed.
            if not source.is_relative_to(resolved_source_root):
                console.print(
                    f"[yellow]Warning:[/yellow] copy source '{mod.source_path}' "
                    "escapes source tree — skipping"
                )
                continue
            if not source.exists() or not source.is_file():
                console.print(
                    f"[yellow]Warning:[/yellow] copy source '{mod.source_path}' "
                    "does not exist — skipping"
                )
                continue
            shutil.copy2(source, target)
        elif mod.action == "append" and target.exists():
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
        _dedupe_manual_items_against_modifications(plan)
        last_validation = _validate_plan_in_staging(
            plan,
            analysis.app_dir,
            source_root=analysis.rebased_from or analysis.app_dir,
        )
        if last_validation.passed:
            plan.success = True
            plan.warnings = last_validation.warnings
            apply_plan(
                plan,
                analysis.app_dir,
                dry_run=dry_run,
                source_root=analysis.rebased_from or analysis.app_dir,
            )
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
            "Install the `claude` (Claude Code) or `codex` CLI to use your existing "
            "subscription — no API key required.",
            "Or set ANTHROPIC_API_KEY / OPENAI_API_KEY for full AI-powered conversion.",
            "For other providers, set OPENAI_API_KEY + OPENAI_BASE_URL (any OpenAI-compatible API).",
            "Review kamiwaza.json, then rerun convert with an LLM to attempt compose and runtime integration.",
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


def _validate_plan_in_staging(
    plan: ConversionPlan,
    app_dir: Path,
    *,
    source_root: Optional[Path] = None,
) -> ValidationSummary:
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
        # Copy actions resolve their source against the original source
        # tree (the monorepo root in rebased cases), not the staged copy.
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


# Verbs that signal "the user must take this action" — when paired with a
# path the LLM has already scheduled as a modification, the manual item is
# a stale leftover (the LLM hedged after deciding to do the work itself).
_MANUAL_ACTION_VERBS = (
    "vendor",
    "copy",
    "move",
    "create",
    "add ",
    "rebase",
    "rewrite",
    "drop ",
    "switch ",
    "update ",
    "modify ",
    "ensure ",
    "install ",
    "place ",
)


def _dedupe_manual_items_against_modifications(plan: ConversionPlan) -> None:
    """Drop manual_items the LLM left despite scheduling the same work.

    LLMs often hedge: they emit a `copy` modification for a wheel AND
    write a manual_item telling the user to "vendor the wheel". The
    manual_item is then misleading — the user thinks the convert didn't
    finish. Strip such items when they (a) name a verb implying the user
    must act, and (b) reference a path or filename already present in
    the modifications list.
    """
    if not plan.manual_items or not plan.modifications:
        return

    referenced = set()
    for mod in plan.modifications:
        if mod.path:
            referenced.add(mod.path.lower())
            referenced.add(Path(mod.path).name.lower())
        if mod.source_path:
            referenced.add(mod.source_path.lower())
            referenced.add(Path(mod.source_path).name.lower())

    kept: List[str] = []
    for item in plan.manual_items:
        lowered = item.lower()
        looks_actionable = any(verb in lowered for verb in _MANUAL_ACTION_VERBS)
        mentions_handled_path = any(token and token in lowered for token in referenced)
        if looks_actionable and mentions_handled_path:
            continue
        kept.append(item)
    plan.manual_items = kept
