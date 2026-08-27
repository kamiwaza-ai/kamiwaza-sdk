"""Runtime-path routing contract tests.

Consumes the canonical vectors at ``docs/extensions/runtime-path/
routing-vectors.json`` — the same file the TypeScript library's
``runtime-parity.test.ts`` consumes — so the Python and TS
implementations cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kamiwaza_extensions_lib.runtime import (
    RuntimeRouting,
    normalize_app_path,
    with_app_path,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VECTORS_PATH = (
    REPO_ROOT / "docs" / "extensions" / "runtime-path" / "routing-vectors.json"
)
VECTORS = json.loads(VECTORS_PATH.read_text())


@pytest.mark.unit
@pytest.mark.parametrize(
    "vector", VECTORS["normalize"], ids=[v["name"] for v in VECTORS["normalize"]]
)
def test_normalize_app_path_vectors(vector):
    if vector.get("expect_error"):
        with pytest.raises(ValueError):
            normalize_app_path(vector["value"])
    else:
        assert normalize_app_path(vector["value"]) == vector["expect"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "vector",
    VECTORS["with_app_path"],
    ids=[v["name"] for v in VECTORS["with_app_path"]],
)
def test_with_app_path_vectors(vector):
    assert with_app_path(vector["path"], vector["app_path"]) == vector["expect"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "vector", VECTORS["routing"], ids=[v["name"] for v in VECTORS["routing"]]
)
def test_runtime_routing_vectors(vector):
    if vector.get("expect_error"):
        with pytest.raises(ValueError):
            RuntimeRouting.from_env(vector["env"])
        return

    routing = RuntimeRouting.from_env(vector["env"])
    expect = vector["expect"]
    assert routing.routing_mode == expect["routing_mode"]
    assert routing.app_path == expect["app_path"]
    assert routing.app_path_url == expect["app_path_url"]
    assert routing.app_url == expect["app_url"]
    assert routing.deployment_id == expect["deployment_id"]
    assert routing.app_port == expect["app_port"]


@pytest.mark.unit
def test_root_path_and_cookie_path():
    routing = RuntimeRouting.from_env(
        {"KAMIWAZA_ROUTING_MODE": "path", "KAMIWAZA_APP_PATH": "/runtime/apps/x"}
    )
    assert routing.root_path == "/runtime/apps/x"
    assert routing.cookie_path == "/runtime/apps/x"

    port = RuntimeRouting.from_env({"KAMIWAZA_ROUTING_MODE": "port"})
    assert port.root_path == ""
    assert port.cookie_path == "/"


@pytest.mark.unit
def test_from_env_defaults_to_process_env(monkeypatch):
    monkeypatch.setenv("KAMIWAZA_ROUTING_MODE", "path")
    monkeypatch.setenv("KAMIWAZA_APP_PATH", "/runtime/apps/envtest")
    routing = RuntimeRouting.from_env()
    assert routing.app_path == "/runtime/apps/envtest"


@pytest.mark.unit
def test_runtime_routing_is_frozen():
    routing = RuntimeRouting.from_env({})
    with pytest.raises(Exception):
        routing.app_path = "/x"  # type: ignore[misc]
