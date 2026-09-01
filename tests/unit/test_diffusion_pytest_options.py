from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from _kamiwaza_pytest_options import _env_flag, mark_skipped_diffusion_items


def test_env_flag_accepts_only_explicit_truthy_values(monkeypatch) -> None:
    monkeypatch.setenv("TEST_FLAG", "YES")
    assert _env_flag("TEST_FLAG") is True
    monkeypatch.setenv("TEST_FLAG", "false")
    assert _env_flag("TEST_FLAG") is False


def test_skip_diffusion_marks_only_diffusion_items() -> None:
    config = cast(pytest.Config, SimpleNamespace(getoption=lambda _name: True))
    diffusion_marker = Mock()
    regular_marker = Mock()
    diffusion_item = cast(
        pytest.Item,
        SimpleNamespace(keywords={"diffusion": True}, add_marker=diffusion_marker),
    )
    regular_item = cast(
        pytest.Item,
        SimpleNamespace(keywords={"integration": True}, add_marker=regular_marker),
    )

    mark_skipped_diffusion_items(config, [diffusion_item, regular_item])

    diffusion_marker.assert_called_once()
    marker = diffusion_marker.call_args.args[0]
    assert marker.name == "skip"
    assert marker.kwargs["reason"] == "disabled by explicit --skip-diffusion"
    regular_marker.assert_not_called()


def test_skip_diffusion_is_noop_without_opt_out() -> None:
    config = cast(pytest.Config, SimpleNamespace(getoption=lambda _name: False))
    add_marker = Mock()
    item = cast(
        pytest.Item,
        SimpleNamespace(keywords={"diffusion": True}, add_marker=add_marker),
    )

    mark_skipped_diffusion_items(config, [item])

    add_marker.assert_not_called()
