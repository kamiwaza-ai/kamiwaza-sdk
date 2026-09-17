"""Fail-closed clearance gate for the SDK federation known-answer fixture."""

from __future__ import annotations

from typing import Any

from .protocol_compat import (
    AttributeGate,
    AttributeSpec,
    GateAuditEntry,
    GateResult,
)

_RANK = {"U": 0, "S": 1, "TS": 2}


class MiniClearanceGate(AttributeGate):
    """Include records whose classification is within caller clearance."""

    @property
    def name(self) -> str:
        return "mini_clearance_gate"

    def required_attributes(self) -> list[AttributeSpec]:
        return [
            AttributeSpec(
                key="clearance",
                header="x-user-clearance",
                required=True,
                description="Caller clearance level (U|S|TS); unknown floors to U.",
            )
        ]

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "classification_field": {
                    "type": "string",
                    "default": "classification",
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
        clearance = str(user_attrs.get("clearance", "")).strip().upper()
        caller_rank = _RANK.get(clearance, 0)
        field = str(gate_config.get("classification_field", "classification"))
        kept: list[dict[str, Any]] = []
        audit: list[GateAuditEntry] = []

        for index, record in enumerate(records):
            classification = str(record.get(field, "")).strip().upper()
            row_rank = _RANK.get(classification)
            included = row_rank is not None and row_rank <= caller_rank
            if included:
                kept.append(record)
            audit.append(
                GateAuditEntry(
                    record_index=index,
                    decision="INCLUDED" if included else "REDACTED",
                    reason=(
                        "clearance_sufficient"
                        if included
                        else (
                            "classification_unrecognized"
                            if row_rank is None
                            else "clearance_insufficient"
                        )
                    ),
                    gate=self.name,
                    attributes_checked={
                        "clearance": clearance,
                        "record_classification": classification,
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
