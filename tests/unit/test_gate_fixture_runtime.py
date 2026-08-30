"""Session-lifecycle contracts for automatic live gate fixtures."""

from __future__ import annotations

import os

import pytest

from tests.integration import conftest as integration_conftest

pytestmark = pytest.mark.unit


def test_runtime_exports_provisioned_values_and_restores_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("M5_TEST_WHEEL_DIR", "/manual/wheels")
    monkeypatch.delenv("M5_TEST_INDEX_URL", raising=False)
    provisioned = {
        "M5_TEST_WHEEL_DIR": "/automatic/wheels",
        "M5_TEST_INDEX_URL": "file:///automatic/simple",
    }
    monkeypatch.setattr(
        integration_conftest._gate_fixture,
        "auto_provision_from_env",
        lambda: provisioned,
    )

    runtime_factory = getattr(
        integration_conftest.gate_fixture_runtime, "__wrapped__", None
    )
    assert runtime_factory is not None
    runtime = runtime_factory()
    next(runtime)

    assert os.environ["M5_TEST_WHEEL_DIR"] == "/automatic/wheels"
    assert os.environ["M5_TEST_INDEX_URL"] == "file:///automatic/simple"

    with pytest.raises(StopIteration):
        next(runtime)
    assert os.environ["M5_TEST_WHEEL_DIR"] == "/manual/wheels"
    assert "M5_TEST_INDEX_URL" not in os.environ
