"""Lifecycle contract diagnostics must render manifest text literally."""

from io import StringIO

import pytest
import typer
from rich.console import Console

from kamiwaza_extensions.contract_enforcement import (
    enforce_cli_contract,
    enforce_cli_contracts,
)
from kamiwaza_extensions.extension_detector import ExtensionInfo


@pytest.mark.unit
def test_single_contract_error_does_not_interpret_rich_markup() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False)

    with pytest.raises(typer.Exit) as exc_info:
        enforce_cli_contract({"kz_ext_version": "[/x]"}, console=console)

    assert exc_info.value.exit_code == 2
    assert "Invalid kz_ext_version range '[/x]'" in output.getvalue()


@pytest.mark.unit
def test_multi_contract_error_does_not_interpret_name_or_range_markup(
    tmp_path,
) -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False)
    info = ExtensionInfo(
        path=tmp_path,
        name="[bold]extension[/bold]",
        version="1.0.0",
        metadata={"kz_ext_version": "[bold]0.2.0"},
    )

    with pytest.raises(typer.Exit) as exc_info:
        enforce_cli_contracts([info], console=console)

    assert exc_info.value.exit_code == 2
    assert "[bold]extension[/bold]" in output.getvalue()
    assert "Invalid kz_ext_version range '[bold]0.2.0'" in output.getvalue()
