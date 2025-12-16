from __future__ import annotations

import importlib
import sys

import pytest


def test_kamiwaza_client_aliases_sdk(monkeypatch) -> None:
    """
    Legacy ``kamiwaza_client`` imports should transparently proxy to the SDK package.
    """

    import kamiwaza_sdk

    # Ensure the alias module is re-imported for this test.
    monkeypatch.delitem(sys.modules, "kamiwaza_client", raising=False)

    with pytest.deprecated_call():
        alias = importlib.import_module("kamiwaza_client")

    assert alias is kamiwaza_sdk

    from kamiwaza_client import KamiwazaClient  # noqa: WPS433

    assert KamiwazaClient is kamiwaza_sdk.KamiwazaClient
