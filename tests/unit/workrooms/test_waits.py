"""The bounded waits, and the guard that keeps the token floor honest.

``when_authority_projected`` retries one narrow response and nothing else;
``await_condition`` polls a fixed number of times. The wait budget is derived
from the live tests' own source rather than restated, and the walker refuses any
shape it cannot count instead of undercounting it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from kamiwaza_sdk.exceptions import (
    APIError,
)
from tests.integration import _workroom_support as support
from tests.unit.workrooms._ledger_fakes import (
    make_ledger,
)

pytestmark = pytest.mark.unit


def authority_pending() -> APIError:
    return APIError(
        "unavailable",
        status_code=503,
        response_data={"detail": "authorization_unavailable"},
    )


def test_when_authority_projected_retries_until_the_call_succeeds() -> None:
    outcomes: list[object] = [authority_pending(), authority_pending(), "done"]
    slept: list[float] = []

    def call() -> object:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert support.when_authority_projected(call, sleep=slept.append) == "done"
    assert len(slept) == 2


@pytest.mark.parametrize(
    "error",
    [
        APIError(
            "denied",
            status_code=403,
            response_data={"detail": "Workroom access denied"},
        ),
        APIError("down", status_code=503, response_data={"detail": "something else"}),
    ],
)
def test_when_authority_projected_propagates_any_other_error(error: APIError) -> None:
    calls: list[int] = []

    def call() -> None:
        calls.append(1)
        raise error

    with pytest.raises(APIError) as raised:
        support.when_authority_projected(call, sleep=lambda _: None)
    assert raised.value is error
    assert calls == [1]


def test_when_authority_projected_gives_up_at_the_deadline() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 50:
            raise RuntimeError("the retry did not stop at its deadline")
        now[0] += seconds

    def call() -> None:
        raise authority_pending()

    with pytest.raises(APIError) as raised:
        support.when_authority_projected(
            call, timeout=5.0, clock=lambda: now[0], sleep=sleep
        )
    assert raised.value.status_code == 503
    assert now[0] >= 5.0


def test_await_condition_polls_until_true() -> None:
    results = [False, False, True]
    slept: list[float] = []

    support.await_condition(
        lambda: results.pop(0), "never", attempts=5, delay=1.0, sleep=slept.append
    )

    assert slept == [1.0, 1.0]


def test_await_condition_fails_with_its_message_after_the_last_attempt() -> None:
    slept: list[float] = []

    with pytest.raises(AssertionError, match="still not there"):
        support.await_condition(
            lambda: False, "still not there", attempts=3, delay=0.5, sleep=slept.append
        )
    assert slept == [0.5, 0.5]


def test_create_dataset_waits_out_owner_authority_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wait the guard below credits to each ledger.create_dataset call."""
    ledger, _, _, _ = make_ledger(binds_session=False)
    waited: list[object] = []
    projected = support.when_authority_projected

    def counted(call, **kwargs):
        waited.append(call)
        return projected(call, **kwargs)

    monkeypatch.setattr(support, "when_authority_projected", counted)
    workroom_id = ledger.create_workroom("one-wait")
    assert waited == [], "creating a workroom waits, which the guard does not count"
    ledger.create_dataset("one-wait-data", workroom_id)
    assert len(waited) == 1, "create_dataset no longer waits exactly once"
    ledger.remove_remaining()
    assert len(waited) == 1, "cleanup waits, which the guard does not count"


_LIVE_TESTS = Path(support.__file__).parent


_DISPOSABLE_USER_MODULES = (
    # Every test in these two modules runs as the disposable user, whose token
    # cannot be refreshed; the export test uses the shared admin client.
    "test_workroom_lifecycle_live.py",
    "test_workroom_admin_live.py",
)


_WAITS = ("await_condition", "when_authority_projected")


_HELPER_WAITS = {"create_dataset": "when_authority_projected"}


class _BoundedWaits(ast.NodeVisitor):
    """Count the bounded waits one run of a live test makes.

    A call inside a ``for`` over a literal sequence counts once per element.
    Any other loop, and a comprehension holding a wait, raises rather than
    undercounting: a shape this cannot read must be counted by hand.

    What it does not see, because it reads one function body and two call
    names: a wait made by a helper other than ``ledger.create_dataset``, or by
    a fixture. Adding either means adding it to ``_HELPER_WAITS`` or counting
    it by hand; the consequence of missing one is a token that expires
    mid-run, which fails loudly in cleanup rather than corrupting evidence.
    """

    def __init__(self) -> None:
        self.waits = dict.fromkeys(_WAITS, 0)
        self._factor = 1

    def visit_For(self, node: ast.For) -> None:
        # The iterable is evaluated once, outside the loop's own multiplier.
        self.visit(node.iter)
        if not isinstance(node.iter, ast.Tuple | ast.List) or any(
            isinstance(element, ast.Starred) for element in node.iter.elts
        ):
            # A starred element, ``(*items, 1)``, has no knowable length.
            raise AssertionError(
                f"line {node.lineno}: this guard cannot count a loop over "
                "anything but a literal sequence of known length"
            )
        outer, self._factor = self._factor, self._factor * len(node.iter.elts)
        for child in node.body:
            self.visit(child)
        self._factor = outer
        for child in node.orelse:
            self.visit(child)

    def visit_While(self, node: ast.While) -> None:
        raise AssertionError(f"line {node.lineno}: a while loop is not bounded here")

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        # visit_For does not run for this node, so refuse rather than skip it.
        raise AssertionError(f"line {node.lineno}: an async for is not counted here")

    def _refuse_deferred(self, node: ast.AST) -> None:
        """A wait the guard would count once however many times it runs."""
        if any(
            isinstance(inner, ast.Call)
            and getattr(inner.func, "id", getattr(inner.func, "attr", ""))
            in (*_WAITS, *_HELPER_WAITS)
            for inner in ast.walk(node)
        ):
            raise AssertionError(
                f"line {node.lineno}: a wait inside a comprehension, lambda or "
                "nested function; this guard counts it once however many times "
                "it runs"
            )

    visit_ListComp = _refuse_deferred
    visit_SetComp = _refuse_deferred
    visit_DictComp = _refuse_deferred
    visit_GeneratorExp = _refuse_deferred
    visit_Lambda = _refuse_deferred
    visit_FunctionDef = _refuse_deferred
    visit_AsyncFunctionDef = _refuse_deferred

    def visit_Call(self, node: ast.Call) -> None:
        called = ""
        if isinstance(node.func, ast.Name):
            called = node.func.id
        elif isinstance(node.func, ast.Attribute):
            called = node.func.attr
        if called in self.waits:
            self.waits[called] += self._factor
        elif called in _HELPER_WAITS:
            self.waits[_HELPER_WAITS[called]] += self._factor
        self.generic_visit(node)


def _waits_in_source(module: str, source: str) -> dict[str, dict[str, int]]:
    """Count the waits each test in one module makes, whatever shape it takes."""
    counted: dict[str, dict[str, int]] = {}
    # ast.walk, not tree.body: a class-based or async test would otherwise be
    # skipped silently, and with it the disposable-user gate below.
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
            node.name.startswith("test_")
        ):
            requested = {arg.arg for arg in node.args.args}
            assert requested & {"workroom_user", "ledger"}, (
                f"{module}::{node.name} does not run as the disposable user, "
                "so this budget no longer describes the module"
            )
            walker = _BoundedWaits()
            for statement in node.body:
                walker.visit(statement)
            counted[f"{module}::{node.name}"] = walker.waits
    return counted


def _waits_per_live_test() -> dict[str, dict[str, int]]:
    counted: dict[str, dict[str, int]] = {}
    for module in _DISPOSABLE_USER_MODULES:
        counted |= _waits_in_source(module, (_LIVE_TESTS / module).read_text())
    return counted


_RECORDED_WAITS = {
    "await_condition": "LISTING_POLLS_PER_RUN",
    "when_authority_projected": "PROJECTION_RETRIES_PER_RUN",
}


@pytest.mark.parametrize("wait", list(_RECORDED_WAITS))
def test_the_recorded_wait_counts_match_the_live_tests(wait: str) -> None:
    """The token budget is only as honest as these counts: derive them."""
    recorded = getattr(support, _RECORDED_WAITS[wait])
    counted = _waits_per_live_test()
    assert counted, "no live test was counted"
    worst, waits = max(counted.items(), key=lambda item: item[1][wait])

    assert waits[wait] == recorded, (
        f"{worst} makes {waits[wait]} {wait} waits, not the {recorded} recorded"
    )


@pytest.mark.parametrize(
    ("source", "refused"),
    [
        ("for item in items:\n    await_condition(check, 'f')\n", "literal sequence"),
        ("while True:\n    await_condition(check, 'f')\n", "while loop"),
        ("[await_condition(check, 'f') for _ in (1, 2)]\n", "comprehension"),
        ("probe = lambda: await_condition(check, 'f')\n", "lambda"),
        ("def probe():\n    await_condition(check, 'f')\n", "nested function"),
        ("{k: when_authority_projected(c) for k in (1, 2)}\n", "comprehension"),
        ("for item in (*items, 1):\n    await_condition(check, 'f')\n", "known length"),
        (
            # The enclosing async def is refused first, as a nested function.
            (
                "async def run():\n    async for item in items:\n"
                "        await_condition(check, 'f')\n"
            ),
            "nested function",
        ),
    ],
    ids=[
        "non-literal-for",
        "while",
        "list-comprehension",
        "lambda",
        "nested-def",
        "dict-comprehension",
        "starred-literal",
        "async-for",
    ],
)
def test_the_wait_guard_refuses_a_shape_it_cannot_count(
    source: str, refused: str
) -> None:
    """Undercounting silently is the failure this guard exists to prevent."""
    walker = _BoundedWaits()

    with pytest.raises(AssertionError, match=refused):
        for statement in ast.parse(source).body:
            walker.visit(statement)


def test_the_wait_guard_counts_a_wait_that_builds_the_loop_iterable() -> None:
    """It runs once, before the loop; counting it zero times understates the bound."""
    walker = _BoundedWaits()

    source = "for value in (await_condition(check, 'f'), other()):\n    pass\n"
    for statement in ast.parse(source).body:
        walker.visit(statement)

    assert walker.waits["await_condition"] == 1


def test_a_class_based_or_async_live_test_is_counted_too() -> None:
    """Walking only the module body would skip both shapes, and the gate with them."""
    source = (
        "class TestRetirement:\n"
        "    def test_in_a_class(self, ledger):\n"
        "        await_condition(check, 'f')\n"
        "\n"
        "async def test_async(workroom_user):\n"
        "    await_condition(check, 'f')\n"
    )

    counted = _waits_in_source("m.py", source)

    assert set(counted) == {"m.py::test_in_a_class", "m.py::test_async"}


def test_a_live_test_that_is_not_the_disposable_user_fails_the_gate() -> None:
    with pytest.raises(AssertionError, match="does not run as the disposable user"):
        _waits_in_source("m.py", "def test_other(live_kamiwaza_client):\n    pass\n")


def test_the_wait_guard_refuses_an_async_for_on_its_own() -> None:
    """Reached directly: an enclosing async def is refused before it."""
    source = "async def run():\n    async for item in items:\n        pass\n"
    async_for = ast.parse(source).body[0].body[0]
    walker = _BoundedWaits()

    with pytest.raises(AssertionError, match="async for"):
        walker.visit(async_for)


def test_the_wait_guard_counts_a_comprehension_that_holds_no_wait() -> None:
    """The refusal is about waits, not about comprehensions."""
    walker = _BoundedWaits()

    for statement in ast.parse("names = [str(w) for w in rooms]\n").body:
        walker.visit(statement)

    assert walker.waits == dict.fromkeys(_WAITS, 0)


def test_the_budget_value_is_pinned() -> None:
    """A canary: changing a constant must be re-derived against the token floor."""
    assert support.BOUNDED_WAIT_BUDGET_SECONDS == 232.0
