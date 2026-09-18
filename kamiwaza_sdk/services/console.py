"""Console surface: the act-approval path a human decides on.

Customer surface: ``kz.console.act_approvals.{create, get, resolve, consume}``.

An act that needs organisational approval cannot be approved inside an MCP
client: the MCP specification has no approval primitive, and the clients
measured do not advertise elicitation. So the approval is opened on the
platform, decided by a second person on the console, and spent by the caller
through an opaque reference. These four calls are the whole of that path from
the client side.

None of the four is published as an agent tool. The reasons are recorded in
``kamiwaza_sdk.agent_tools.ids.UNPUBLISHED``, because an agent that can call
``resolve`` approves its own act.

Server-side correlate: ``kamiwaza/services/console/api.py``, whose routes are
mounted under the application's ``/api`` root path.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union
from uuid import UUID

from ..schemas.act_approvals import (
    ActApprovalCreate,
    ActApprovalResolve,
    ActApprovalView,
    ActDecision,
    ActKind,
    OperationAccepted,
)
from .base_service import BaseService


class ActApprovalsAPI(BaseService):
    """Open, read, decide and spend one act approval."""

    def create(
        self,
        act_kind: ActKind,
        act_target: str,
        act_digest: str,
        title: str,
        act_payload: Dict[str, Any],
        idempotency_key: str,
        *,
        reason: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> OperationAccepted:
        """Open an approval for one act and return the operation recording it.

        Args:
            act_kind: Dispatch path of the act: ``"operation"`` or
                ``"workflow"``.
            act_target: Published operation or workflow name the act calls.
            act_digest: SHA-256 over the canonical act, as 64 hex characters.
                The resolver presents this digest back, which is what binds a
                decision to the act that was read.
            title: The act described in the words the approver reads.
            act_payload: The act's arguments, so the approver sees what will
                happen.
            idempotency_key: Caller-chosen key, salted per tenant by the
                service, so a retry reopens the same approval.
            reason: Why the caller wants the act.
            ttl_seconds: Lifetime of the approval. Omit for the platform
                default of 7 days; more than 30 days is refused.

        Returns:
            OperationAccepted: The console operation that records the opened
            approval, with the approval among its ``resource_refs``.

        Raises:
            KamiwazaError: 422 when the platform rejects a field, and 429 when
                the caller is over the console's request limit.
        """
        request = ActApprovalCreate(
            act_kind=act_kind,
            act_target=act_target,
            act_digest=act_digest,
            title=title,
            act_payload=act_payload,
            reason=reason,
            idempotency_key=idempotency_key,
            ttl_seconds=ttl_seconds,
        )
        response = self.client._request(
            "POST",
            "/act-approvals",
            json=request.model_dump(exclude_none=True),
        )
        return OperationAccepted.model_validate(response)

    def get(self, approval_id: Union[UUID, str]) -> ActApprovalView:
        """Read one approval, with its decision and who made it.

        Readable by the requester and by anyone in the approver set, which is
        why this route exists rather than the owner-scoped operation feed.

        Args:
            approval_id: Identifier of the approval.

        Returns:
            ActApprovalView: The approval's current state, including
            ``resolver_id`` and ``resolved_at`` once a human has decided.

        Raises:
            KamiwazaError: 403 when the caller is neither the requester nor an
                approver, and 404 when no such approval exists.
        """
        response = self.client._request("GET", f"/act-approvals/{approval_id}")
        return ActApprovalView.model_validate(response)

    def resolve(
        self,
        approval_id: Union[UUID, str],
        decision: ActDecision,
        presented_digest: str,
    ) -> OperationAccepted:
        """Record a human decision on one approval.

        Args:
            approval_id: Identifier of the approval.
            decision: ``"approved"`` or ``"denied"``.
            presented_digest: The act digest that was shown to the resolver, as
                64 hex characters.

        Returns:
            OperationAccepted: The console operation recording the decision.

        Raises:
            KamiwazaError: 403 when the resolver is the requester, because the
                platform refuses self-approval, or when the caller holds no
                act-approval role; 409 ``act_approval.digest_stale`` when
                ``presented_digest`` is not the stored digest, and 409
                ``act_approval.expired`` when the approval has lapsed.
        """
        request = ActApprovalResolve(
            decision=decision, presented_digest=presented_digest
        )
        response = self.client._request(
            "POST",
            f"/act-approvals/{approval_id}/resolve",
            json=request.model_dump(),
        )
        return OperationAccepted.model_validate(response)

    def consume(self, approval_id: Union[UUID, str]) -> ActApprovalView:
        """Spend an approved approval, so its act runs exactly once.

        Args:
            approval_id: Identifier of the approval.

        Returns:
            ActApprovalView: The approval with ``status`` now ``consumed`` and
            ``consumed_at`` set, which the caller reads to dispatch the act it
            describes.

        Raises:
            KamiwazaError: 409 when the approval is not approved, was already
                consumed, or has expired.
        """
        response = self.client._request("POST", f"/act-approvals/{approval_id}/consume")
        return ActApprovalView.model_validate(response)


class ConsoleAPI(BaseService):
    """The console surface, with the act-approval sub-API under it."""

    @property
    def act_approvals(self) -> ActApprovalsAPI:
        """Act approvals: open, read, decide, spend. Lazy-loaded sub-service.

        Lives under ``kz.console`` because an act approval is a console
        concern: the decision is taken on the console by a person who is not
        the requester.
        """
        if not hasattr(self, "_act_approvals"):
            self._act_approvals = ActApprovalsAPI(self.client)
        return self._act_approvals
