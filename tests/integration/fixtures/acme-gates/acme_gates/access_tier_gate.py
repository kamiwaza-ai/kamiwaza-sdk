"""Fail-closed access-tier gate for SDK federation validation."""

from __future__ import annotations

from typing import Any

from .protocol_compat import (
    AttributeGate,
    AttributeSpec,
    GateAuditEntry,
    GateResult,
)

_RANK = {"basic": 0, "standard": 1, "advanced": 2}


class AccessTierGate(AttributeGate):
    """Include records available to caller's access tier."""

    @property
    def name(self) -> str:
        return "access_tier_gate"

    def required_attributes(self) -> list[AttributeSpec]:
        return [
            AttributeSpec(
                key="access_tier",
                header="x-user-access-tier",
                required=True,
                description="Caller access tier.",
            )
        ]

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "required_tier_field": {
                    "type": "string",
                    "default": "required_tier",
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
        access_tier = str(user_attrs.get("access_tier", "")).strip().lower()
        caller_rank = _RANK.get(access_tier, 0)
        field = str(gate_config.get("required_tier_field", "required_tier"))
        kept: list[dict[str, Any]] = []
        audit: list[GateAuditEntry] = []

        for index, record in enumerate(records):
            required_tier = str(record.get(field, "")).strip().lower()
            row_rank = _RANK.get(required_tier)
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
                            "required_tier_unknown"
                            if row_rank is None
                            else "access_tier_insufficient"
                        )
                    ),
                    gate=self.name,
                    attributes_checked={
                        "access_tier": access_tier,
                        "record_required_tier": required_tier,
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
