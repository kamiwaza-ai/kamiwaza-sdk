"""Dev local command implementation."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()


def run_dev_local(*, detach: bool, use_auth: bool, use_auth_bridge: bool) -> None:
    """Run extension locally with Docker Compose."""
    from kamiwaza_extensions.dev_local import DevLocalRunner

    runner = DevLocalRunner()
    exit_code = runner.run(
        detach=detach,
        use_auth=use_auth,
        use_auth_bridge=use_auth_bridge,
    )
    raise typer.Exit(code=exit_code)
