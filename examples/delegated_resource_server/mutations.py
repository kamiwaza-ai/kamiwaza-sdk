"""Deterministic exact-approved mutation and unknown-result fixture."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Literal, Protocol
from uuid import UUID

from kamiwaza_sdk.delegated_workloads import SealedDelegatedContext


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RESOURCE_TYPE = "conformance.document"


class MutationRejected(PermissionError):
    """A safe denial before the conformance mutation executes."""


class MutationAuthorityRejected(MutationRejected):
    def __init__(self) -> None:
        super().__init__("exact mutation authority is unavailable")


class MutationReplayRejected(MutationRejected):
    def __init__(self) -> None:
        super().__init__("an external mutation cannot be replayed")


class MutationOutcomeUnknown(RuntimeError):
    """The external mutation committed but its response was not observed."""

    def __init__(self) -> None:
        super().__init__("external mutation outcome is unknown")


class MutationStore(Protocol):
    def put(self, resource_id: str, title: str) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class MutationRequest:
    resource_id: str
    title: str
    request_digest: str


@dataclass(frozen=True, slots=True)
class SafeMutationRecord:
    effect_id: UUID
    run_id: UUID
    resource_id: str
    request_digest: str
    policy_version: str
    outcome: Literal["succeeded", "ambiguous"]


class ExactApprovedMutationFixture:
    """Execute a consumed mutation effect once and expose only safe records."""

    def __init__(self, store: MutationStore) -> None:
        self._store = store
        self._records: dict[UUID, SafeMutationRecord] = {}
        self._lose_response = False
        self._lock = Lock()

    @property
    def records(self) -> tuple[SafeMutationRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def lose_next_response(self) -> None:
        with self._lock:
            self._lose_response = True

    def mutate(
        self,
        request: MutationRequest,
        authority: SealedDelegatedContext,
    ) -> Mapping[str, object]:
        _require_exact_authority(request, authority)
        context = authority.context
        with self._lock:
            if context.effect_id in self._records:
                raise MutationReplayRejected
            result = self._store.put(request.resource_id, request.title)
            outcome: Literal["succeeded", "ambiguous"] = (
                "ambiguous" if self._lose_response else "succeeded"
            )
            self._lose_response = False
            self._records[context.effect_id] = SafeMutationRecord(
                effect_id=context.effect_id,
                run_id=context.run_id,
                resource_id=request.resource_id,
                request_digest=request.request_digest,
                policy_version=context.policy_version,
                outcome=outcome,
            )
        if outcome == "ambiguous":
            raise MutationOutcomeUnknown
        return result


def _require_exact_authority(
    request: MutationRequest,
    authority: SealedDelegatedContext,
) -> None:
    context = authority.context
    resource = context.resource
    valid_request = all(
        (
            bool(request.resource_id),
            isinstance(request.title, str),
            0 < len(request.title) <= 256,
            bool(_DIGEST.fullmatch(request.request_digest)),
        )
    )
    if not valid_request:
        raise ValueError("mutation request is invalid")
    exact_authority = all(
        (
            context.action == "mutate",
            resource.type == _RESOURCE_TYPE,
            resource.id == request.resource_id,
        )
    )
    if not exact_authority:
        raise MutationAuthorityRejected


__all__ = (
    "ExactApprovedMutationFixture",
    "MutationAuthorityRejected",
    "MutationOutcomeUnknown",
    "MutationReplayRejected",
    "MutationRequest",
    "SafeMutationRecord",
)
