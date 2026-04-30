"""Dev local command implementation."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

console = Console()


def run_dev_local(
    *,
    detach: bool,
    sdk_repo: Optional[str] = None,
    auth: bool = False,
) -> None:
    """Run extension locally with Docker Compose."""
    from kamiwaza_extensions.dev_local import DevLocalRunner
    from kamiwaza_extensions_lib.local_dev import LocalDevAuthError

    runner = DevLocalRunner()
    try:
        exit_code = runner.run(detach=detach, sdk_repo=sdk_repo, auth=auth)
    except LocalDevAuthError as exc:
        # Surface the developer-facing message and exit non-zero so the user
        # sees a clear "run kz-ext login" hint instead of a stack trace.
        console.print(f"[red]--auth bridge unavailable:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    raise typer.Exit(code=exit_code)
