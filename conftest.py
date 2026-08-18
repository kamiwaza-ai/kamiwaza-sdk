from __future__ import annotations

import pytest

from _kamiwaza_pytest_options import add_live_options


pytest.register_assert_rewrite("_kamiwaza_cli_live")


def pytest_addoption(parser: pytest.Parser) -> None:
    add_live_options(parser)
