from __future__ import annotations

import pytest

from _kamiwaza_pytest_options import add_live_options
from tests.e2e import _evidence_emitter

# pytester powers the in-process pytest runs in
# tests/e2e/test_evidence_emitter.py (ENG-10026).
pytest_plugins = ["pytester"]


def pytest_addoption(parser: pytest.Parser) -> None:
    add_live_options(parser)
    _evidence_emitter.add_evidence_options(parser)


def pytest_configure(config: pytest.Config) -> None:
    # Opt-in scenario-evidence.v2 emission (--emit-evidence); no-op without
    # the flag, refuses without a build identity.
    _evidence_emitter.maybe_register(config)
