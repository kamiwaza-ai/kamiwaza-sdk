"""AI-agent-powered conversion for kz-ext convert.

The conversion flow is intentionally AI-led. Deterministic logic is
limited to:

- collecting broader repo context (``app_analyzer``)
- staging proposed changes in a temporary workspace (``agent``)
- validating the staged output (``agent`` + ``validators``)
- asking the model to repair validation failures before applying changes

Module map:

- ``models`` — typed dataclasses (FileModification, ConversionPlan, …)
- ``prompts`` — strategy + modification prompt builders
- ``providers`` — LLM provider dispatch (CLI subscriptions + APIs)
- ``plan`` — parse / apply / post-process the LLM's plan
- ``agent`` — top-level ``run_agent`` orchestration

Public surface re-exports below preserve the historical
``from kamiwaza_extensions.convert_agent import X`` import paths so
callers and tests don't need to learn the new module layout.

The ``shutil`` and ``subprocess`` re-imports keep
``monkeypatch.setattr(convert_agent.shutil, "which", ...)`` and
``monkeypatch.setattr(convert_agent.subprocess, "run", ...)`` working.
Patching the ``shutil`` / ``subprocess`` module's attributes here
patches them for every consumer (Python imports are cached), so
``providers.py`` sees the monkeypatched values without further wiring.
"""

from __future__ import annotations

# Module-level imports preserved for monkeypatch attribute access in
# tests (see docstring above). These also serve as the canonical
# imports for the package — anything in providers/plan/agent that
# needs them imports from the stdlib directly.
import importlib.util  # noqa: F401
import shutil  # noqa: F401
import subprocess  # noqa: F401

from kamiwaza_extensions.convert_agent.agent import (
    _MAX_REPAIR_ATTEMPTS,
    _STAGING_SKIP_DIRS,
    _apply_basic_fallback,
    _apply_validated_plan,
    _find_compose_file,
    _llm_unavailable_failure,
    _print_run_banner,
    _repair_exhausted_failure,
    _run_modification_round,
    _strategy_unparseable_failure,
    _validate_plan_in_staging,
    run_agent,
)
from kamiwaza_extensions.convert_agent.models import (
    ConversionPlan,
    ConversionStrategy,
    FileModification,
    ValidationSummary,
)
from kamiwaza_extensions.convert_agent.plan import (
    _ALLOWED_ACTIONS,
    _build_convert_notes,
    _default_metadata,
    _dedupe_manual_items_against_modifications,
    _ensure_supporting_files,
    _execute_copy,
    _execute_text,
    _is_valid_existing_kamiwaza_json,
    _load_existing_kamiwaza_json,
    _MANUAL_ACTION_VERB_RE,
    _MANUAL_ACTION_VERBS,
    _MIN_DEDUPE_TOKEN_LEN,
    _merge_manual_items,
    _parse_json_payload,
    _preserve_existing_kamiwaza_json,
    _resolve_modification_target,
    apply_plan,
    parse_response,
    parse_strategy_response,
)
from kamiwaza_extensions.convert_agent.prompts import (
    _MAX_CONTEXT_SIZE,
    _MAX_PREVIOUS_MODIFICATIONS,
    _render_compatibility_issues,
    _render_context_files,
    _render_monorepo_inventory,
    _render_previous_plan,
    _render_repo_inventory,
    _render_services,
    _render_validation_feedback,
    _strategy_to_dict,
    _summarize_previous_plan,
    build_modification_prompt,
    build_prompt,
    build_strategy_prompt,
)
from kamiwaza_extensions.convert_agent.providers import (
    _AGENT_GUIDANCE,
    _CLI_TIMEOUT_SECONDS,
    _GUIDANCE_PATH,
    _PROVIDERS,
    _VALID_PROVIDERS,
    _call_anthropic,
    _call_openai_compatible,
    _load_agent_guidance,
    _provider_anthropic,
    _provider_claude_cli,
    _provider_codex_cli,
    _provider_openai,
    _run_cli_subprocess,
    _safe_api_call,
    call_llm,
    console,
)

__all__ = [
    # Public API
    "ConversionPlan",
    "ConversionStrategy",
    "FileModification",
    "ValidationSummary",
    "apply_plan",
    "build_modification_prompt",
    "build_prompt",
    "build_strategy_prompt",
    "call_llm",
    "parse_response",
    "parse_strategy_response",
    "run_agent",
]
