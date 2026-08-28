"""Test-fixture AttributeGate filtering records on tier.

This gate is used by the SDK-owned gate-package lifecycle test.  It is
deliberately tiny and deterministic: a record is included when its ``tier``
does not exceed the requester's ``tier`` attribute.
"""

from __future__ import annotations

from typing import Any

from kamiwaza.services.authz.gates.protocol import (
    AttributeGate,
    AttributeSpec,
    GateAuditEntry,
    GateResult,
)

_TIERS = {"bronze": 1, "silver": 2, "gold": 3, "platinum": 4}


class AcmeAttributeGate(AttributeGate):
    """Filter records by tier; requester tier must meet the record tier."""

    @property
    def name(self) -> str:
        return "acme_attribute_gate"

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
                    "description": "Floor tier for records returned by the gate.",
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
        user_tier_name = str(user_attrs.get("tier", "")).lower()
        user_tier = _TIERS.get(user_tier_name, 0)
        floor_name = str(gate_config.get("min_tier", "bronze")).lower()
        floor = _TIERS.get(floor_name, 1)
        effective_tier = max(user_tier, floor)

        kept: list[dict[str, Any]] = []
        audit: list[GateAuditEntry] = []
        for index, record in enumerate(records):
            record_tier_name = str(record.get("tier", "bronze")).lower()
            record_tier = _TIERS.get(record_tier_name, 1)
            included = record_tier <= effective_tier
            audit.append(
                GateAuditEntry(
                    record_index=index,
                    decision="INCLUDED" if included else "REDACTED",
                    reason="tier_match" if included else "tier_insufficient",
                    gate=self.name,
                    attributes_checked={
                        "tier": user_tier_name,
                        "min_tier": floor_name,
                        "record_tier": record_tier_name,
                    },
                )
            )
            if included:
                kept.append(record)

        return GateResult(
            records=kept,
            audit=audit,
            included_count=len(kept),
            redacted_count=len(records) - len(kept),
            total_count=len(records),
        )
