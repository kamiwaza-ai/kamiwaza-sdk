"""Remote development must reject manifests this kz-ext cannot honor."""

from unittest.mock import MagicMock, patch

import pytest
import typer

from kamiwaza_extensions.commands import dev as dev_command


@pytest.mark.unit
def test_run_dev_remote_checks_cli_contract_before_compose(capsys) -> None:
    info = MagicMock(
        metadata={"kz_ext_version": ">=0.3.0,<1.0.0"},
        compose_data={"services": {"app": {}}},
    )
    detector = MagicMock()
    detector.detect.return_value = info

    with (
        patch(
            "kamiwaza_extensions.extension_detector.ExtensionDetector",
            return_value=detector,
        ),
        patch("kamiwaza_extensions.connections.ConnectionManager") as connection,
        pytest.raises(typer.Exit) as exc_info,
    ):
        dev_command.run_dev_remote()

    assert exc_info.value.exit_code == 2
    assert "CLI version 0.2.0 is not compatible" in capsys.readouterr().err
    connection.assert_not_called()


@pytest.mark.unit
def test_dev_contract_accepts_current_capability() -> None:
    dev_command._enforce_cli_contract({"kz_ext_version": ">=0.2.0,<1.0.0"})


@pytest.mark.unit
def test_run_dev_remote_rejects_compose_capability_before_connection() -> None:
    info = MagicMock(
        metadata={"kz_ext_version": ">=0.1.0,<1.0.0"},
        compose_data={"services": {"app": {"command": ["serve"]}}},
    )
    detector = MagicMock()
    detector.detect.return_value = info

    with (
        patch(
            "kamiwaza_extensions.extension_detector.ExtensionDetector",
            return_value=detector,
        ),
        patch("kamiwaza_extensions.connections.ConnectionManager") as connection,
        pytest.raises(typer.Exit) as exc_info,
    ):
        dev_command.run_dev_remote()

    assert exc_info.value.exit_code == 2
    connection.assert_not_called()
