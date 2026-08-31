"""Remote development must reject manifests this kz-ext cannot honor."""

from unittest.mock import MagicMock, patch

import pytest
import typer

from kamiwaza_extensions.commands import dev as dev_command


@pytest.mark.unit
def test_run_dev_remote_checks_cli_contract_before_compose() -> None:
    info = MagicMock(
        metadata={"kz_ext_version": ">=0.3.0,<1.0.0"},
        compose_data=None,
    )
    detector = MagicMock()
    detector.detect.return_value = info

    with patch(
        "kamiwaza_extensions.extension_detector.ExtensionDetector",
        return_value=detector,
    ):
        with pytest.raises(typer.Exit) as exc_info:
            dev_command.run_dev_remote()

    assert exc_info.value.exit_code == 1


@pytest.mark.unit
def test_dev_contract_accepts_current_capability() -> None:
    dev_command._enforce_cli_contract({"kz_ext_version": ">=0.2.0,<1.0.0"})
