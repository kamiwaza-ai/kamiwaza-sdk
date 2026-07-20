from __future__ import annotations

import ast
from pathlib import Path


def _context_live_source() -> str:
    tests_root = Path(__file__).resolve().parents[1]
    return (tests_root / "integration" / "test_context_live.py").read_text()


def _function_def(source: str, name: str) -> ast.FunctionDef:
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def test_vectordb_scale_live_test_uses_an_isolated_backend() -> None:
    source = _context_live_source()
    function = _function_def(
        source,
        "test_context_vectordb_scale_reflects_requested_replicas",
    )

    argument_names = {argument.arg for argument in function.args.args}
    function_source = ast.get_source_segment(source, function) or ""

    assert "shared_workroom_vectordb" not in argument_names
    assert "_create_temp_vectordb" in function_source
    assert "_safe_delete_vectordb" in function_source
