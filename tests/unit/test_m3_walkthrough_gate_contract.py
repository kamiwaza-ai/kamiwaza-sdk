"""The actual live walkthrough must bind a schema shipped by the SDK fixture."""

import ast
import importlib
from pathlib import Path

import jsonschema
import pytest

from kamiwaza_sdk.validation.federation_fixture import GATE_CLASSPATH

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_walkthrough_binding_matches_shipped_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = ast.parse(
        (ROOT / "tests/integration/test_m3_walkthrough_live.py").read_text()
    )
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        if isinstance(node.func, ast.Attribute)
        if node.func.attr == "set_gate"
    ]
    assert len(calls) == 1
    arguments = {item.arg: ast.literal_eval(item.value) for item in calls[0].keywords}
    assert arguments["type"] == GATE_CLASSPATH
    monkeypatch.syspath_prepend(str(ROOT / "tests/integration/fixtures/acme-gates"))
    module, _, name = arguments["type"].rpartition(".")
    gate = getattr(importlib.import_module(module), name)
    schema = gate.config_schema()
    jsonschema.validate(arguments["config"], schema)
    assert arguments["config"] == {"tier_field": "tier"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"retired_field": "tier"}, schema)
