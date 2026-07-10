"""Tests for the shared ASGI launcher (``python -m kamiwaza_extensions_lib.asgi``)."""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from kamiwaza_extensions_lib.asgi import main


@contextmanager
def patched_uvicorn():
    """Install a stub ``uvicorn`` module (the SDK itself does not depend on
    uvicorn; the launcher imports it lazily from the app's environment)."""
    stub = types.ModuleType("uvicorn")
    stub.run = MagicMock()
    previous = sys.modules.get("uvicorn")
    sys.modules["uvicorn"] = stub
    try:
        yield stub.run
    finally:
        if previous is None:
            del sys.modules["uvicorn"]
        else:
            sys.modules["uvicorn"] = previous


@pytest.mark.unit
def test_launches_uvicorn_with_root_path(monkeypatch):
    monkeypatch.setenv("KAMIWAZA_ROUTING_MODE", "path")
    monkeypatch.setenv("KAMIWAZA_APP_PATH", "/runtime/apps/550e8400")
    with patched_uvicorn() as run:
        code = main(["app.main:app", "--host", "0.0.0.0", "--port", "8000"])
    assert code == 0
    run.assert_called_once_with(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        root_path="/runtime/apps/550e8400",
    )


@pytest.mark.unit
def test_port_mode_has_empty_root_path(monkeypatch):
    monkeypatch.setenv("KAMIWAZA_ROUTING_MODE", "port")
    monkeypatch.delenv("KAMIWAZA_APP_PATH", raising=False)
    with patched_uvicorn() as run:
        code = main(["app.main:app"])
    assert code == 0
    kwargs = run.call_args.kwargs
    assert kwargs["root_path"] == ""
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 8000


@pytest.mark.unit
def test_invalid_routing_env_fails_before_uvicorn(monkeypatch):
    monkeypatch.setenv("KAMIWAZA_ROUTING_MODE", "path")
    monkeypatch.setenv("KAMIWAZA_APP_PATH", "/runtime/../etc")
    with patched_uvicorn() as run:
        code = main(["app.main:app"])
    assert code != 0
    run.assert_not_called()


@pytest.mark.unit
def test_requires_app_argument():
    with patched_uvicorn() as run:
        code = main([])
    assert code != 0
    run.assert_not_called()
