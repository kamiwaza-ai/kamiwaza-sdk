from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _context_live_source() -> str:
    tests_root = Path(__file__).resolve().parents[1]
    return (tests_root / "integration" / "test_context_live.py").read_text()


def _function_def(source: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _argument_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {
        argument.arg
        for argument in [
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        ]
    }


def _call_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    calls: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
    return calls


def _call_keyword_names(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    call_name: str,
) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            current_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            current_name = node.func.attr
        else:
            continue
        if current_name != call_name:
            continue
        names.update(keyword.arg for keyword in node.keywords if keyword.arg is not None)
    return names


def _assert_live_test_uses_isolated_vectordb(
    source: str,
    test_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    function = _function_def(source, test_name)
    argument_names = _argument_names(function)
    call_names = _call_names(function)
    assert "shared_workroom_vectordb" not in argument_names
    assert "_create_temp_vectordb" in call_names
    assert "_safe_delete_vectordb" in call_names
    return function


def test_mutating_vectordb_live_tests_use_isolated_backends() -> None:
    source = _context_live_source()

    for test_name in (
        "test_context_vectordb_update_accepts_config_and_redacts_public_response",
        "test_context_vectordb_scale_reflects_requested_replicas",
    ):
        _assert_live_test_uses_isolated_vectordb(source, test_name)


@pytest.mark.parametrize(
    ("test_name", "contract_call_name", "collection_keyword_name"),
    [
        ("test_context_search_contract", "search", "collection_name"),
        ("test_context_retrieve_contract", "retrieve", "collection_names"),
        ("test_context_agentic_search_contract", "agentic_search", "collection_name"),
    ],
)
def test_search_family_contracts_use_seeded_isolated_collections(
    test_name: str,
    contract_call_name: str,
    collection_keyword_name: str,
) -> None:
    source = _context_live_source()
    function = _assert_live_test_uses_isolated_vectordb(
        source,
        test_name,
    )

    call_names = _call_names(function)
    contract_keywords = _call_keyword_names(function, contract_call_name)
    assert "_seed_searchable_context_collection" in call_names
    assert collection_keyword_name in contract_keywords
