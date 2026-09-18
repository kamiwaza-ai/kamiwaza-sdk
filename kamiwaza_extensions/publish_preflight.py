"""Reject unsupported catalog batches before publishing has side effects."""

from __future__ import annotations

from collections.abc import Iterable

import typer
from rich.console import Console

from kamiwaza_extensions.catalog_publisher import SUPPORTED_CATALOG_SCHEMAS
from kamiwaza_extensions.exit_codes import ExitCode
from kamiwaza_extensions.extension_detector import ExtensionInfo, infer_extension_type


def preflight_catalog_types(
    infos: Iterable[ExtensionInfo], catalog_schema: int | str
) -> None:
    """Validate the whole batch before profile, image, or catalog operations."""
    error = None
    if catalog_schema not in SUPPORTED_CATALOG_SCHEMAS:
        error = f"Unsupported catalog schema: {catalog_schema!r}"
    elif catalog_schema == "compat-v1":
        unsupported = [
            info.name
            for info in infos
            if infer_extension_type(info.metadata) not in {"app", "service", "tool"}
        ]
        if unsupported:
            error = (
                "compat-v1 supports apps, services, and tools only; "
                f"unsupported extensions: {', '.join(unsupported)}"
            )
    if error:
        Console(stderr=True).print(f"Error: {error}", markup=False)
        raise typer.Exit(code=int(ExitCode.VALIDATION))
