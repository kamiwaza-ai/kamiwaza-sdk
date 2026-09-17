"""Fail-closed access_tier gate for the SDK federation known-answer fixture."""

from __future__ import annotations

from typing import Any

from .protocol_compat import (
    AttributeGate,
    AttributeSpec,
    GateAuditEntry,
    GateResult,
)

_RANK = {"PUBLIC": 0, "PRIVATE": 1, "CONFIDENTIAL": 2}


class MiniAccessTierGate(AttributeGate):
    """Include records whose tier is within caller access_tier."""

    @property
    def name(self) -> str:
        return "mini_access_tier_gate"

    def required_attributes(self) -> list[AttributeSpec]:
        return [
            AttributeSpec(
                key="access_tier",
                header="x-user-access-tier",
                required=True,
                description="Caller access_tier level (PUBLIC|PRIVATE|CONFIDENTIAL); unknown floors to PUBLIC.",
            )
        ]

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tier_field": {
                    "type": "string",
                    "default": "tier",
                }
            },
            "additionalProperties": False,
        }

    def filter_records(
        self,
        records: list[dict[str, Any]],
        user_attrs: dict[str, Any],
        gate_config: dict[str, Any],
    ) -> GateResult:
        access_tier = str(user_attrs.get("access_tier", "")).strip().upper()
        caller_rank = _RANK.get(access_tier, 0)
        field = str(gate_config.get("tier_field", "tier"))
        kept: list[dict[str, Any]] = []
        audit: list[GateAuditEntry] = []

        for index, record in enumerate(records):
            tier = str(record.get(field, "")).strip().upper()
            row_rank = _RANK.get(tier)
            included = row_rank is not None and row_rank <= caller_rank
            if included:
                kept.append(record)
            audit.append(
                GateAuditEntry(
                    record_index=index,
                    decision="INCLUDED" if included else "REDACTED",
                    reason=(
                        "access_tier_sufficient"
                        if included
                        else (
                            "tier_unrecognized"
                            if row_rank is None
                            else "access_tier_insufficient"
                        )
                    ),
                    gate=self.name,
                    attributes_checked={
                        "access_tier": access_tier,
                        "record_tier": tier,
                    },
                )
            )

        return GateResult(
            records=kept,
            audit=audit,
            included_count=len(kept),
            redacted_count=len(records) - len(kept),
            total_count=len(records),
        )
