"""Test-fixture ExecutionGate for the SDK gate-package lifecycle rig."""

from __future__ import annotations

from typing import Any

from .protocol_compat import (
    AttributeSpec,
    AuthorizationDecision,
    AuthzOutcome,
    ExecutionGate,
    GateAuditEntry,
)

_TIERS = {"bronze": 1, "silver": 2, "gold": 3, "platinum": 4}


class AcmeExecutionGate(ExecutionGate):
    """Allow execution when the requester meets the configured tier floor."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._min_tier = (config or {}).get("min_tier", "bronze")

    @property
    def name(self) -> str:
        return "acme_execution_gate"

    def required_attributes(self) -> list[AttributeSpec]:
        return [
            AttributeSpec(
                key="tier",
                header="x-acme-tier",
                required=True,
                description="Acme membership tier (bronze|silver|gold|platinum)",
            )
        ]

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "min_tier": {
                    "type": "string",
                    "enum": list(_TIERS),
                    "default": "bronze",
                }
            },
            "additionalProperties": False,
        }

    def authorize(
        self,
        user_attrs: dict[str, Any],
        job_spec: dict[str, Any],
    ) -> AuthorizationDecision:
        del job_spec
        floor = _TIERS.get(str(self._min_tier).lower(), 1)
        user_tier = _TIERS.get(str(user_attrs.get("tier", "")).lower(), 0)
        if user_tier >= floor:
            return AuthorizationDecision(
                outcome=AuthzOutcome.ALLOW,
                reason="tier_meets_floor",
                gate=self.name,
            )
        return AuthorizationDecision(
            outcome=AuthzOutcome.DENY,
            reason="tier_below_floor",
            gate=self.name,
            audit_entry=GateAuditEntry(
                record_index=-1,
                decision="DENY",
                reason="tier_below_floor",
                gate=self.name,
                attributes_checked={"tier": user_attrs.get("tier")},
            ),
        )
