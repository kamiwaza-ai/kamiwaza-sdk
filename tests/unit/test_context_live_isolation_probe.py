"""Unit coverage for the T14 cross-workroom isolation probe (ENG-12477).

The live T14 test proves an indexed document is not visible from another
workroom. Its verdict helper has to tell three outcomes apart — a denial, a
room with no VectorDB bound at all, and a genuine outage — so it gets unit
coverage here rather than only on a live deployment.
"""

from collections.abc import Callable
from typing import Any

import pytest

from kamiwaza_sdk.exceptions import APIError
from tests.integration.test_context_live import _assert_foreign_search_misses

pytestmark = pytest.mark.unit


def _search_raising(error: APIError) -> Callable[[], dict[str, Any]]:
    def search() -> dict[str, Any]:
        raise error

    return search


def test_foreign_search_tolerates_workroom_with_no_vectordb_bound() -> None:
    """A freshly created room has no backend, so core answers a retryable 503."""
    error = APIError(
        'API request failed with status 503: {"code":"vectordb_instance_not_found"}',
        status_code=503,
        response_data={
            "code": "vectordb_instance_not_found",
            "message": "no VectorDB instance is provisioned for this workroom",
            "retry_after_seconds": 30,
        },
    )

    _assert_foreign_search_misses(_search_raising(error), "t14probe")


def test_foreign_search_tolerates_denial() -> None:
    _assert_foreign_search_misses(
        _search_raising(APIError("forbidden", status_code=403)), "t14probe"
    )


def test_foreign_search_rejects_unrelated_server_error() -> None:
    error = APIError(
        "context service is unavailable",
        status_code=503,
        response_data={"code": "search_backend_unreachable"},
    )

    with pytest.raises(AssertionError):
        _assert_foreign_search_misses(_search_raising(error), "t14probe")


def test_foreign_search_rejects_unrelated_503_that_quotes_the_vectordb_code() -> None:
    """A parsed body wins over diagnostic text that merely names the code."""
    error = APIError(
        'API request failed with status 503: {"code":"search_backend_unreachable",'
        '"message":"upstream reported vectordb_instance_not_found"}',
        status_code=503,
        response_data={
            "code": "search_backend_unreachable",
            "message": "upstream reported vectordb_instance_not_found",
        },
    )

    with pytest.raises(AssertionError):
        _assert_foreign_search_misses(_search_raising(error), "t14probe")


def test_foreign_search_rejects_a_leaked_document() -> None:
    def search() -> dict[str, Any]:
        return {"results": [{"content": "The verification phrase is t14probe."}]}

    with pytest.raises(AssertionError):
        _assert_foreign_search_misses(search, "t14probe")


def test_foreign_search_accepts_results_without_the_needle() -> None:
    def search() -> dict[str, Any]:
        return {"results": [{"content": "an unrelated document"}]}

    _assert_foreign_search_misses(search, "t14probe")
