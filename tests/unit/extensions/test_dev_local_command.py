"""Tests for the ``kz-ext dev local`` command boundary."""

from unittest.mock import MagicMock, patch

import pytest
import typer

from kamiwaza_extensions.commands.dev_local import run_dev_local
from kamiwaza_extensions.exit_codes import ExitCode


@pytest.mark.unit
def test_incompatible_manifest_fails_before_runner_starts():
    info = MagicMock()
    info.metadata = {
        "name": "future-extension",
        "kz_ext_version": ">=99.0.0",
    }

    with (
        patch(
            "kamiwaza_extensions.extension_detector.ExtensionDetector"
        ) as detector_cls,
        patch("kamiwaza_extensions.dev_local.DevLocalRunner") as runner_cls,
    ):
        detector_cls.return_value.detect.return_value = info
        with pytest.raises(typer.Exit) as exc_info:
            run_dev_local(detach=False)

    assert exc_info.value.exit_code == int(ExitCode.VALIDATION)
    runner_cls.assert_not_called()
