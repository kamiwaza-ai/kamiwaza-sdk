"""AI-agent-powered conversion for kz-ext convert.

Uses Claude API to intelligently modify existing apps for Kamiwaza
extension compatibility. Falls back to basic kamiwaza.json generation
when the LLM is unavailable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from kamiwaza_extensions.app_analyzer import AnalysisResult

console = Console(stderr=True)

# Max total content size sent to LLM (characters)
_MAX_CONTEXT_SIZE = 50000


@dataclass
class FileModification:
    """A single file modification produced by the agent."""

    path: str  # Relative to app_dir
    action: str  # "create", "modify", "append"
    content: str  # Full new content (create/modify) or content to append
    description: str = ""  # What was changed and why


@dataclass
class ConversionPlan:
    """The agent's plan for converting an app."""

    modifications: List[FileModification] = field(default_factory=list)
    manual_items: List[str] = field(default_factory=list)
    summary: str = ""


def build_prompt(analysis: AnalysisResult) -> str:
    """Build the structured prompt for the conversion agent."""
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

    services_section = ""
    for svc in analysis.services:
        services_section += f"- **{svc.name}**: language={svc.language or 'unknown'}, ports={svc.ports}, dockerfile={'yes' if svc.dockerfile else 'no'}\n"

    compat_section = ""
    if analysis.has_host_ports:
        compat_section += f"- Host port bindings (auto-stripped during deploy): {', '.join(analysis.has_host_ports)}\n"
    if analysis.has_bind_mounts:
        compat_section += f"- Bind mounts (auto-stripped during deploy): {', '.join(analysis.has_bind_mounts)}\n"
    if analysis.missing_resource_limits:
        compat_section += f"- Missing resource limits: {', '.join(analysis.missing_resource_limits)}\n"
    if not analysis.has_health_endpoint:
        compat_section += "- No health endpoint detected\n"
    if not analysis.has_python_runtime_lib:
        compat_section += "- Python runtime library (kamiwaza-extensions-lib) not installed\n"
    if not analysis.has_ts_runtime_lib:
        compat_section += "- TypeScript runtime library (@kamiwaza-ai/extensions-lib) not installed\n"

    return f"""You are converting an existing containerized application into a Kamiwaza extension.

## Application: {analysis.app_name}
Type: {analysis.extension_type}
Description: {analysis.description or 'No description'}

## Services
{services_section}

## Compatibility Issues
{compat_section or 'None detected'}

## Current Files
{files_section}

## Kamiwaza Extension Requirements

### Python Backend Integration (kamiwaza-extensions-lib)
Add to requirements.txt:
```
kamiwaza-extensions-lib>=0.1.0
```

Add to FastAPI main.py:
```python
from kamiwaza_extensions_lib import create_session_router, require_auth, Identity
from fastapi import Depends

# Add session endpoints (required for frontend auth)
app.include_router(create_session_router())

# Add health endpoint if missing
@app.get("/health")
async def health():
    return {{"status": "ok"}}

# Example protected endpoint:
# @app.get("/api/protected")
# async def protected(identity: Identity = Depends(require_auth)):
#     return {{"user": identity.email}}
```

### TypeScript Frontend Integration (@kamiwaza-ai/extensions-lib)
Add to package.json dependencies:
```
"@kamiwaza-ai/extensions-lib": "^0.2.0"
```

Update the root layout to wrap with SessionProvider and AuthGuard:
```tsx
import {{ SessionProvider, AuthGuard }} from '@kamiwaza-ai/extensions-lib/client';

export default function RootLayout({{ children }}: {{ children: React.ReactNode }}) {{
    return (
        <html lang="en">
            <body>
                <SessionProvider>
                    <AuthGuard>
                        {{children}}
                    </AuthGuard>
                </SessionProvider>
            </body>
        </html>
    );
}}
```

### Docker Compose Resource Limits
Add deploy.resources.limits to each service:
```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: "1G"
```

## Instructions

Analyze the application and produce modifications to make it a Kamiwaza extension. Output ONLY a JSON object with this structure:

```json
{{
    "modifications": [
        {{
            "path": "relative/path/to/file",
            "action": "create" | "modify",
            "content": "full file content",
            "description": "what was changed"
        }}
    ],
    "manual_items": [
        "Description of something that needs manual attention"
    ],
    "summary": "Brief summary of all changes"
}}
```

Rules:
1. For "modify" actions, output the COMPLETE new file content (not a diff).
2. For "create" actions, output the full file content.
3. Only modify files that need changes. Don't touch files that are already correct.
4. Add kamiwaza-extensions-lib to Python backends. Add @kamiwaza-ai/extensions-lib to Node.js frontends.
5. Add a health endpoint if one doesn't exist.
6. Add create_session_router() to FastAPI apps.
7. Add resource limits to docker-compose.yml services that are missing them.
8. Wrap frontend layouts with SessionProvider + AuthGuard if not already wrapped.
9. Keep existing functionality intact — add SDK integration, don't remove existing code.
10. If you're unsure about a change, add it to manual_items instead.
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
        try:
            import openai as _openai  # noqa: F811
        except ImportError:
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
            # OpenAI call failed — fall through to Anthropic

    # --- Anthropic (fallback) ---
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic
        except ImportError:
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
        client = anthropic.Anthropic(api_key=api_key)
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
        kwargs: Dict[str, Any] = {"api_key": api_key}
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


def parse_response(response_text: str) -> ConversionPlan:
    """Parse the LLM response into a ConversionPlan."""
    # Extract JSON from the response (may be wrapped in markdown code block)
    json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", response_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try parsing the whole response as JSON
        json_str = response_text.strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return ConversionPlan(
            manual_items=["LLM response could not be parsed. Review the app manually."],
            summary="Conversion plan could not be generated automatically.",
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
            manual_items=data.get("manual_items", []),
            summary=data.get("summary", ""),
        )
    except (TypeError, AttributeError, KeyError):
        return ConversionPlan(
            manual_items=["LLM returned unexpected response shape. Review the app manually."],
            summary="Conversion plan could not be generated automatically.",
        )


def apply_plan(plan: ConversionPlan, app_dir: Path, dry_run: bool = False) -> List[str]:
    """Apply the conversion plan to the filesystem.

    Returns list of applied change descriptions.
    """
    applied = []
    # Resolve once to avoid TOCTOU with symlinks changing between iterations
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
        # Security: reject paths that escape the app directory
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
    """Run the full conversion agent: build prompt → call LLM → parse → apply.

    Returns the ConversionPlan (applied or not depending on dry_run).
    """
    prompt = build_prompt(analysis)
    console.print("  [dim]Calling AI agent...[/dim]")
    response = call_llm(prompt)

    if response is None:
        return ConversionPlan(
            summary="LLM unavailable — basic conversion only.",
            manual_items=[
                "Set ANTHROPIC_API_KEY or OPENAI_API_KEY for full AI-powered conversion.",
                "For other providers, set OPENAI_API_KEY + OPENAI_BASE_URL (any OpenAI-compatible API).",
                "Manually add kamiwaza-extensions-lib to your Python backend.",
                "Manually add @kamiwaza-ai/extensions-lib to your frontend.",
                "Add a health endpoint at GET /health.",
                "Add resource limits to docker-compose.yml.",
            ],
        )

    plan = parse_response(response)
    applied = apply_plan(plan, analysis.app_dir, dry_run=dry_run)

    if dry_run:
        plan.summary = f"[Dry run] Would apply {len(applied)} modifications. " + (plan.summary or "")

    return plan
