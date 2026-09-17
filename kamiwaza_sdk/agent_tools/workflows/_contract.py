"""The contract every workflow tool obeys, and the registry that records it.

Split from the workflows themselves so the rules live in one place and each
domain module carries only its own behaviour.

Searching the registry lives here too, next to the dict it reads. A workflow is
found by the same words as an operation, so the ranking reuses
:func:`kamiwaza_sdk.agent_tools.spec_index.score_terms` rather than keeping a
second scorer in step with the first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..spec_index import meaningful_terms, score_terms

__all__ = [
    "WORKFLOWS",
    "DeploymentOutcome",
    "Refusal",
    "WorkflowSpec",
    "register",
    "search_workflows",
]


@dataclass(frozen=True, slots=True)
class WorkflowSpec:
    """What one workflow tool promises.

    Attributes:
        name: Published identifier.
        summary: One sentence stating what the workflow returns.
        terminal_artifact: What the caller receives — the thing itself, never a
            handle to chase.
        polling_step: The single step that waits, or ``None`` when none does.
        approval_step: The single step needing approval, or ``None``.
        idempotent: Whether calling it twice is equivalent to calling it once.
        reads_only: Whether the workflow leaves platform state unchanged. The
            default is ``False`` because a workflow that never declared the
            fact must not look like one that declared it safe: a host reading
            ``False`` asks before running, which is the harmless mistake. The
            fact cannot be derived from the name — ``find_and_deploy_model``
            leads with a read verb and deploys a model — so only the author
            knows it.
        destructive: Whether the workflow ends or replaces something a caller
            could otherwise still use. Also declared rather than derived, for
            the same reason: the deployment a workflow stops is not named in
            the workflow's own verb.
        not_idempotent_because: Required when ``idempotent`` is false. FR-016
            allows a non-idempotent workflow only if it says so plainly.
        resume_hint: What to call to resume when a bounded wait expires.
    """

    name: str
    summary: str
    terminal_artifact: str
    polling_step: str | None
    approval_step: str | None
    idempotent: bool
    reads_only: bool = False
    destructive: bool = False
    not_idempotent_because: str | None = None
    resume_hint: str | None = None

    def __post_init__(self) -> None:
        """Reject a spec that would break the workflow contract.

        Raises:
            ValueError: If a non-idempotent workflow does not say why, or one
                that polls names no way to resume. Both are FR-016
                requirements, and both are unenforceable once a tool ships.
                Also if the behaviour hints contradict each other: a workflow
                cannot both leave state unchanged and end something, and one
                that leaves state unchanged has nothing for an approval step
                to gate.
        """
        if not self.idempotent and not self.not_idempotent_because:
            raise ValueError(
                f"{self.name} is not idempotent and must say why, per FR-016"
            )
        if self.polling_step and not self.resume_hint:
            raise ValueError(
                f"{self.name} polls and must name how to resume an expired wait"
            )
        if self.reads_only and self.destructive:
            raise ValueError(f"{self.name} cannot both read only and be destructive")
        if self.reads_only and self.approval_step:
            raise ValueError(
                f"{self.name} reads only and cannot need approval, because "
                f"approval gates a change"
            )


#: Every published workflow, keyed by name. The contract test asserts against
#: this rather than reading the functions, so a workflow that grows a second
#: wait or a second approval fails the build.
WORKFLOWS: dict[str, WorkflowSpec] = {}


def register(spec: WorkflowSpec) -> Callable[[Any], Any]:
    """Record a workflow's contract and return the decorator that attaches it.

    Args:
        spec: The workflow's promises.

    Returns:
        A decorator attaching the spec to the function.

    Raises:
        ValueError: If the name is already registered.
    """

    def decorate(function: Any) -> Any:
        if spec.name in WORKFLOWS:
            raise ValueError(f"{spec.name} is already registered")
        WORKFLOWS[spec.name] = spec
        function.workflow = spec
        return function

    return decorate


def search_workflows(query: str, *, limit: int = 20) -> tuple[WorkflowSpec, ...]:
    """Rank the registered workflows against a plain-language query.

    A workflow is the answer to a whole question — "why is this deployment not
    serving", "answer this over my documents" — so a caller who cannot find it
    by words finds it only by reading the whole tool listing. The ranking is
    the operation ranking applied to the two fields a spec already carries:
    the name is the identifier and the summary is the prose, scored by
    :func:`~kamiwaza_sdk.agent_tools.spec_index.score_terms` and ordered on
    score, the leading term's position, then length, so a caller asking for
    one word sees workflows and operations sorted by the same rule.

    The operation ranking's rarity tie-break is deliberately not applied
    here. It counts how many things carry each word, and seventeen workflows
    do not make a frequency worth counting: measured, it put
    ``export_workroom_bundle`` above ``grant_subject_access`` for "give Alice
    access to the finance workroom", because two workflow names carry
    "workroom" and five carry a word for access.

    Every term is required first; when nothing matches them all the
    requirement drops by one, down to a single term. Seventeen workflows is a
    small set and the relaxation is what makes a caller's own phrasing land:
    "answer a question over my documents" shares only two words with
    ``rag_query``'s summary. Nothing matching even one term returns nothing,
    which is the honest answer rather than the least-bad workflow.

    Args:
        query: Plain-language words. Case and order do not matter.
        limit: Most workflows to return.

    Returns:
        Up to ``limit`` specs, best first. Empty when the query has no
        searchable terms or when no workflow matches a single one.
    """
    terms = meaningful_terms(query)
    if not terms:
        return ()
    graded = [
        (score_terms(terms, (spec.name,), spec.summary), spec)
        for spec in WORKFLOWS.values()
    ]
    for required in range(len(terms), 0, -1):
        rows = [
            (-score, -lead, len(spec.name), spec.name, spec)
            for (score, hits, lead), spec in graded
            if hits >= required
        ]
        if rows:
            rows.sort()
            return tuple(spec for *_, spec in rows[:limit])
    return ()


@dataclass(frozen=True, slots=True)
class Refusal:
    """A workflow that declined before creating anything.

    Returned rather than raised so a caller can read the shortfall without
    parsing an exception. Refusing before anything exists is the point: a
    half-created deployment is worse than none.

    Attributes:
        reason: Why the workflow declined.
        shortfall: What was missing, named concretely.
    """

    reason: str
    shortfall: str


@dataclass(frozen=True, slots=True)
class DeploymentOutcome:
    """A deployment and how to reach it.

    Attributes:
        deployment_id: Identifier of the deployment.
        model: Identifier of the model that was deployed.
        endpoint: Reachable endpoint, when an instance reported one.
        instances: Instances backing the deployment.
    """

    deployment_id: str
    model: str
    endpoint: str | None
    instances: list[Any] = field(default_factory=list)
