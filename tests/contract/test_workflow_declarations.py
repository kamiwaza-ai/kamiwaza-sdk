"""A workflow may not declare less than the operations it calls derive.

``WorkflowSpec`` is what a host reads before it runs anything: ``idempotent``
is published as ``safe_to_retry``, and ``approval_step`` is the only reason a
host asks a member anything. Both are declared by the workflow's author, while
the same two facts are derived for every operation in ``descriptors.py``. When
a declaration is the weaker of the two, a host retries something that creates a
second thing, or runs an approval-bearing operation without asking.

The comparison is cheap because neither side has to be invented: the operations
a workflow calls are read out of its own source by AST, and the derived hints
come from the same descriptor table the published surface uses. Only direct
``client.<path>(...)`` calls in a workflow's own body are read. The three
helpers those bodies share were measured at this revision and none of them
changes a verdict: ``_await_deployment`` calls
``serving.wait_deployment_ready`` (idempotent, approval-bearing) and
``serving.list_model_instances`` (a read), and all three workflows using it
declare an approval step; ``DatasetTarget.register`` calls
``catalog.create_dataset`` (not idempotent, approval-bearing) in two workflows
that already declare ``idempotent=False`` and an approval step; ``_grants_of``
calls ``subjects.grants``, a read that needs neither.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from kamiwaza_sdk.agent_tools import workflows
from kamiwaza_sdk.agent_tools.descriptors import describe
from kamiwaza_sdk.agent_tools.spec_index import OperationIndex, build_index
from kamiwaza_sdk.agent_tools.workflows._contract import WORKFLOWS, WorkflowSpec

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def index() -> OperationIndex:
    """The operation index, built from a client that never issues a request."""
    from kamiwaza_sdk.client import KamiwazaClient

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = KamiwazaClient(base_url="https://kamiwaza.test/api")
    return build_index(client)


def _registered_functions() -> dict[str, Any]:
    """Return every registered workflow function, keyed by its published name.

    Read off the functions rather than a list of imports, because
    :func:`register` attaches the spec: a workflow renamed at registration is
    found here under its new name.

    Returns:
        The callables, keyed by the name their spec registers.
    """
    return {
        spec.name: value
        for value in vars(workflows).values()
        if (spec := getattr(value, "workflow", None)) is not None
        and spec.name in WORKFLOWS
    }


_FUNCTIONS = _registered_functions()


def _dotted_target(node: ast.Call) -> str | None:
    """Return the ``client``-rooted dotted path one call names.

    Args:
        node: A call node from a workflow body.

    Returns:
        The path with ``client.`` removed — ``serving.deploy_model``,
        ``gates.packages.install`` — or ``None`` when the call is not made on
        the client. A call on the *result* of a client call, such as
        ``client.subjects.grants(name).list()``, is not rooted at ``client``
        and yields ``None``; the inner call is collected on its own.
    """
    segments: list[str] = []
    current: ast.expr = node.func
    while isinstance(current, ast.Attribute):
        segments.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name) or current.id != "client":
        return None
    if len(segments) < 2:
        return None
    return ".".join(reversed(segments))


def _called_selectors(function: Any) -> tuple[str, ...]:
    """Return the operation selectors one workflow calls, in source order.

    Args:
        function: The workflow function.

    Returns:
        Dotted ``service.method`` selectors, deduplicated.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _dotted_target(node)
        if target is not None and target not in found:
            found.append(target)
    return tuple(found)


def _comparison(index: OperationIndex, name: str) -> tuple[WorkflowSpec, dict[str, Any]]:
    """Return one workflow's spec and the derived hints of what it calls.

    Args:
        index: The operation index.
        name: Published workflow name.

    Returns:
        The spec, and a mapping of selector to the operation's descriptor.

    Raises:
        AssertionError: A selector the workflow calls is not in the index, so
            the comparison would silently cover less than the workflow does.
    """
    # The index's own ``get`` refuses a withheld operation, and a workflow may
    # legitimately call one — ``subjects.grants`` is withheld because an agent
    # cannot use the accessor it returns. The entries are walked directly so a
    # withheld operation is still compared rather than raising here.
    entries = {entry.selector: entry for entry in index}
    descriptors: dict[str, Any] = {}
    for selector in _called_selectors(_FUNCTIONS[name]):
        entry = entries.get(selector)
        assert entry is not None, (
            f"{name} calls {selector}, which the index has no entry for"
        )
        descriptors[selector] = describe(entry)
    return WORKFLOWS[name], descriptors


def test_every_workflow_is_compared() -> None:
    """Every registered workflow is reachable as a function to read."""
    assert set(_FUNCTIONS) == set(WORKFLOWS)


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_workflow_calls_at_least_one_operation(index: OperationIndex, name: str) -> None:
    """A workflow with no resolved operation would pass both rules vacuously.

    Args:
        index: The operation index.
        name: Workflow under comparison.
    """
    _, descriptors = _comparison(index, name)

    assert descriptors, f"{name} resolved no client operation, so nothing was compared"


@dataclass(frozen=True, slots=True)
class _Rule:
    """One comparison between a workflow's declaration and its operations.

    A rule object rather than six parameters on a shared assertion: the two
    rules differ only in which field they read and which hint contradicts it,
    and that difference is the abstraction the arguments were missing.

    Attributes:
        subject: Which declaration this rule reads, naming the test case.
        exempt: Whether the workflow already declares the stronger thing, in
            which case there is nothing to compare.
        weaker: Whether one operation contradicts the workflow's claim.
        claim: What the workflow declared, for the message.
        derives: What the offending operations derive, for the message.
    """

    subject: str
    exempt: Callable[[WorkflowSpec], bool]
    weaker: Callable[[Any], bool]
    claim: str
    derives: str


#: FR-016 and FR-018 in the only form a test can check: a workflow may declare
#: something stronger than the operations it calls, never weaker. Declaring
#: idempotence over a write that is not idempotent tells a host a retry is
#: safe, and omitting an approval over an operation that requires one means a
#: host never asks.
_RULES = (
    _Rule(
        subject="idempotence",
        exempt=lambda spec: not spec.idempotent,
        weaker=lambda entry: not entry.hints.idempotent,
        claim="declares idempotent=True",
        derives="derive idempotent=False",
    ),
    _Rule(
        subject="approval",
        exempt=lambda spec: bool(spec.approval_step),
        weaker=lambda entry: entry.requires_approval,
        claim="declares no approval_step",
        derives="derive requires_approval=True",
    ),
)


@pytest.mark.parametrize("rule", _RULES, ids=lambda rule: rule.subject)
@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_a_workflow_declaration_is_not_weaker_than_its_operations(
    index: OperationIndex, name: str, rule: _Rule
) -> None:
    """Every operation a workflow calls must agree with what it declared.

    Args:
        index: The operation index.
        name: Workflow under comparison.
        rule: Which declaration is being compared.
    """
    spec, descriptors = _comparison(index, name)
    if rule.exempt(spec):
        return

    offenders = [
        selector for selector, entry in descriptors.items() if rule.weaker(entry)
    ]

    assert not offenders, (
        f"{name} {rule.claim}; compared against {sorted(descriptors)}, "
        f"these {rule.derives}: {offenders}"
    )
