"""The contract every workflow tool obeys, and the registry that records it.

Split from the workflows themselves so the rules live in one place and each
domain module carries only its own behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "WORKFLOWS",
    "DeploymentOutcome",
    "Refusal",
    "WorkflowSpec",
    "register",
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
    not_idempotent_because: str | None = None
    resume_hint: str | None = None

    def __post_init__(self) -> None:
        """Reject a spec that would break the workflow contract.

        Raises:
            ValueError: If a non-idempotent workflow does not say why, or one
                that polls names no way to resume. Both are FR-016
                requirements, and both are unenforceable once a tool ships.
        """
        if not self.idempotent and not self.not_idempotent_because:
            raise ValueError(
                f"{self.name} is not idempotent and must say why, per FR-016"
            )
        if self.polling_step and not self.resume_hint:
            raise ValueError(
                f"{self.name} polls and must name how to resume an expired wait"
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
