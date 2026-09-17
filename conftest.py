"""Root pytest configuration: repo-wide plugins, options, and collection.

This file is the rootdir conftest, which is the only place pytest honours
``pytest_plugins`` — so registration that looks repo-wide is a requirement
rather than a preference.
"""

from __future__ import annotations

import pytest

from _kamiwaza_pytest_options import add_live_options, mark_skipped_diffusion_items
from tests.e2e import _evidence_emitter

# pytester powers the in-process pytest runs in
# tests/e2e/test_evidence_emitter.py (ENG-10026). It has to live here, not
# beside those tests: pytest only honors `pytest_plugins` in the rootdir
# conftest, so repo-wide registration is a requirement, not a preference.
pytest_plugins = [
    "pytester",
    "tests.integration.required_delegated_workload_edge",
]


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the live-cluster and evidence-emission command-line options.

    Args:
        parser: The parser pytest is building.
    """
    add_live_options(parser)
    _evidence_emitter.add_evidence_options(parser)


def pytest_configure(config: pytest.Config) -> None:
    """Register the evidence emitter when the run asked for it.

    Opt-in through ``--emit-evidence``: a no-op without the flag, and a refusal
    without a build identity rather than emitting evidence nobody can trace.

    Args:
        config: The session's configuration.
    """
    _evidence_emitter.maybe_register(config)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Mark the diffusion tests that this host cannot run.

    Args:
        config: The session's configuration.
        items: The collected items, marked in place.
    """
    mark_skipped_diffusion_items(config, items)
