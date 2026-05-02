"""LLM provider dispatch for the convert agent.

Provider order in ``auto`` mode (default):

1. ``claude`` CLI (Claude Code subscription, no API key)
2. ``codex`` CLI (ChatGPT subscription, no API key)
3. ``OPENAI_API_KEY`` (OpenAI-compatible API)
4. ``ANTHROPIC_API_KEY`` (Anthropic API)

``KZ_CONVERT_PROVIDER`` forces a single provider; ``KZ_CONVERT_MODEL``
overrides the model when the provider takes one.

All providers receive the canonical Kamiwaza extension authoring rules
(``agent_guidance.md``) — CLI agents via ``CLAUDE.md``/``AGENTS.md`` in
the temp cwd, API providers via the SDK ``system`` parameter.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.console import Console

console = Console(stderr=True)

# Subprocess timeout for CLI provider calls. Larger than the SDK timeout
# (120s) because CLI invocations include cold-start, OAuth refresh, and
# the model's full reasoning budget for a single one-shot prompt that
# may carry up to ``_MAX_CONTEXT_SIZE`` characters of repo context.
_CLI_TIMEOUT_SECONDS = 300

# Provider tags used by ``KZ_CONVERT_PROVIDER`` to force one path.
_VALID_PROVIDERS = ("auto", "claude-cli", "codex-cli", "openai", "anthropic")

# Canonical Kamiwaza extension authoring rules. Lives next to this
# package; loaded once at import time. Empty string when missing
# (treated as a non-fatal degradation — providers still get the
# per-call prompt).
_GUIDANCE_PATH = Path(__file__).resolve().parent.parent / "agent_guidance.md"


def _load_agent_guidance() -> str:
    try:
        return _GUIDANCE_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


_AGENT_GUIDANCE = _load_agent_guidance()


def call_llm(prompt: str) -> Optional[str]:
    """Call an LLM and return the response text, or ``None`` if no
    provider is available."""
    provider = (os.environ.get("KZ_CONVERT_PROVIDER") or "auto").strip().lower()
    if provider not in _VALID_PROVIDERS:
        console.print(
            f"[yellow]Warning:[/yellow] Unknown KZ_CONVERT_PROVIDER='{provider}'. "
            f"Falling back to auto. Valid values: {', '.join(_VALID_PROVIDERS)}."
        )
        provider = "auto"

    model_override = os.environ.get("KZ_CONVERT_MODEL")

    for name, runner in _PROVIDERS:
        if provider not in ("auto", name):
            continue
        explicit = provider == name
        result = runner(prompt, model_override=model_override, explicit=explicit)
        if result is not None:
            return result

    return None


# ----------------------------------------------------------------------
# Per-provider runners
# ----------------------------------------------------------------------


def _provider_claude_cli(
    prompt: str, *, model_override: Optional[str], explicit: bool
) -> Optional[str]:
    """Invoke Claude Code in non-interactive mode using subscription auth.

    Runs ``claude --print --no-session-persistence`` with the prompt
    piped on stdin from an isolated temp cwd. Seeds ``CLAUDE.md`` and
    ``AGENTS.md`` with the canonical authoring rules so claude
    auto-discovers them.
    """
    binary = shutil.which("claude")
    if not binary:
        if explicit:
            console.print(
                "[yellow]Warning:[/yellow] KZ_CONVERT_PROVIDER=claude-cli but `claude` "
                "is not on PATH. Install Claude Code or use a different provider."
            )
        return None
    cmd = [binary, "--print", "--no-session-persistence", "--output-format", "text"]
    if model_override:
        cmd += ["--model", model_override]
    return _run_cli_subprocess(
        cmd,
        prompt,
        label="claude CLI",
        context_files={"CLAUDE.md": _AGENT_GUIDANCE, "AGENTS.md": _AGENT_GUIDANCE},
    )


def _provider_codex_cli(
    prompt: str, *, model_override: Optional[str], explicit: bool
) -> Optional[str]:
    """Invoke Codex CLI non-interactively using subscription auth.

    Runs ``codex exec --skip-git-repo-check -`` from an isolated temp
    cwd (not a git repo, hence the flag). Seeds ``AGENTS.md`` and
    ``CLAUDE.md`` with the canonical rules.
    """
    binary = shutil.which("codex")
    if not binary:
        if explicit:
            console.print(
                "[yellow]Warning:[/yellow] KZ_CONVERT_PROVIDER=codex-cli but `codex` "
                "is not on PATH. Install the Codex CLI or use a different provider."
            )
        return None
    cmd = [binary, "exec", "--skip-git-repo-check"]
    if model_override:
        cmd += ["--model", model_override]
    cmd.append("-")
    return _run_cli_subprocess(
        cmd,
        prompt,
        label="codex CLI",
        context_files={"AGENTS.md": _AGENT_GUIDANCE, "CLAUDE.md": _AGENT_GUIDANCE},
    )


def _provider_openai(
    prompt: str, *, model_override: Optional[str], explicit: bool
) -> Optional[str]:
    """Call any OpenAI-compatible chat completions API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        if explicit:
            console.print(
                "[yellow]Warning:[/yellow] KZ_CONVERT_PROVIDER=openai but "
                "OPENAI_API_KEY is not set."
            )
        return None
    if importlib.util.find_spec("openai") is None:
        console.print(
            "[dim]OPENAI_API_KEY is set but openai package is not installed. "
            "Install with: pip install openai[/dim]"
        )
        return None
    return _call_openai_compatible(
        prompt,
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL"),
        model=model_override or os.environ.get("OPENAI_MODEL", "gpt-4o"),
    )


def _provider_anthropic(
    prompt: str, *, model_override: Optional[str], explicit: bool
) -> Optional[str]:
    """Call the Anthropic Messages API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        if explicit:
            console.print(
                "[yellow]Warning:[/yellow] KZ_CONVERT_PROVIDER=anthropic but "
                "ANTHROPIC_API_KEY is not set."
            )
        return None
    if importlib.util.find_spec("anthropic") is None:
        console.print(
            "[dim]ANTHROPIC_API_KEY is set but anthropic package is not installed. "
            "Install with: pip install kamiwaza-sdk[convert][/dim]"
        )
        return None
    return _call_anthropic(
        prompt,
        api_key=api_key,
        model=model_override or "claude-sonnet-4-20250514",
    )


# Provider order in ``auto`` mode (CLI subscriptions first, then API
# keys). ``KZ_CONVERT_PROVIDER`` overrides — only the matching provider
# runs.
_PROVIDERS: List[Tuple[str, Callable]] = [
    ("claude-cli", _provider_claude_cli),
    ("codex-cli", _provider_codex_cli),
    ("openai", _provider_openai),
    ("anthropic", _provider_anthropic),
]


# ----------------------------------------------------------------------
# Subprocess + API call wrappers
# ----------------------------------------------------------------------


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
    system guidance.
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


def _safe_api_call(
    label: str, fn: Callable[[], str], sdk_exceptions: tuple
) -> Optional[str]:
    """Wrap an API call in the standard error envelope.

    ``sdk_exceptions`` is the tuple of provider SDK exception types
    expected to surface from ``fn``; these are reported as a normal
    "API call failed" warning. Common Python errors (TypeError,
    AttributeError, etc.) are reported with the exception type included
    so a programming error doesn't masquerade as a transient API failure.
    """
    try:
        return fn()
    except sdk_exceptions as exc:
        console.print(f"[yellow]Warning:[/yellow] {label} API call failed: {exc}")
        return None
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        console.print(
            f"[yellow]Warning:[/yellow] {label} call raised "
            f"{type(exc).__name__}: {exc}"
        )
        return None


def _call_anthropic(prompt: str, *, api_key: str, model: str) -> Optional[str]:
    """Call the Anthropic Messages API with the standing guidance as system."""
    import anthropic

    def _do_call() -> str:
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

    return _safe_api_call(
        "Anthropic",
        _do_call,
        (anthropic.APIError, anthropic.APIConnectionError),
    )


def _call_openai_compatible(
    prompt: str,
    *,
    api_key: str,
    base_url: Optional[str] = None,
    model: str = "gpt-4o",
) -> Optional[str]:
    """Call any OpenAI-compatible chat completions API with guidance as system."""
    import openai

    def _do_call() -> str:
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

    return _safe_api_call(
        "OpenAI-compatible",
        _do_call,
        (openai.APIError, openai.APIConnectionError),
    )
