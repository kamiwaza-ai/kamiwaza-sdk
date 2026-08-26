"""Authenticated state and ownership helpers for federation fixtures."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any, cast

from pydantic import JsonValue

from kamiwaza_sdk.validation.models import (
    FixtureMutation,
    FixtureState,
    RuntimeContext,
    ScenarioPlan,
)
from kamiwaza_sdk.validation.provider import FixtureStateWriter, ProviderContractError
from kamiwaza_sdk.validation.registry import model_digest
from kamiwaza_sdk.validation.inference_state import runtime_ownership_key

FEDERATION_STATE_MAC_KEY = "ownership_mac"


def owner_nonce(key: bytes, target_id: str) -> str:
    """Derive a run-scoped realm nonce without persisting secret material."""

    if not key:
        raise ProviderContractError("fixture ownership key is empty")
    return hmac.new(
        key, f"realm:{target_id}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


def owner_digest(run_id: str, provider_revision: str) -> str:
    return (
        f"sha256:{hashlib.sha256(f'{run_id}:{provider_revision}'.encode()).hexdigest()}"
    )


def sign_state(state: FixtureState, key: bytes) -> FixtureState:
    """MAC the complete state representation, excluding the MAC itself."""

    if not key:
        raise ProviderContractError("fixture ownership key is empty")
    opaque = dict(state.opaque)
    opaque.pop(FEDERATION_STATE_MAC_KEY, None)
    unsigned = state.model_copy(update={"opaque": opaque})
    payload = json.dumps(
        unsigned.model_dump(mode="json", by_alias=True),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(key, payload, hashlib.sha256).hexdigest()
    opaque[FEDERATION_STATE_MAC_KEY] = f"sha256:{signature}"
    return state.model_copy(update={"opaque": opaque})


def validate_state(runtime: RuntimeContext, state: FixtureState, revision: str) -> None:
    if state.provider_revision != revision:
        raise ProviderContractError("fixture state provider revision mismatch")
    if state.run_id != runtime.run_id:
        raise ProviderContractError("fixture state run does not match runtime")
    if state.runtime_digest != model_digest(runtime):
        raise ProviderContractError("fixture state runtime digest mismatch")
    if not hmac.compare_digest(
        state.owner_token_digest, owner_digest(runtime.run_id, revision)
    ):
        raise ProviderContractError("fixture state ownership digest mismatch")
    key = runtime_ownership_key(runtime)
    actual = state.opaque.get(FEDERATION_STATE_MAC_KEY)
    if not isinstance(actual, str):
        raise ProviderContractError("fixture state ownership MAC is missing")
    expected = sign_state(state, key).opaque[FEDERATION_STATE_MAC_KEY]
    if not isinstance(expected, str) or not hmac.compare_digest(actual, expected):
        raise ProviderContractError("fixture state ownership MAC mismatch")


class FederationStateStore:
    """Write authenticated, monotonic fixture snapshots around mutations."""

    def __init__(self, writer: FixtureStateWriter, key: bytes, revision: str) -> None:
        self.writer = writer
        self.key = key
        self.revision = revision

    def initial(self, plan: ScenarioPlan, runtime: RuntimeContext) -> FixtureState:
        edges = {
            item.target_id: {
                "cluster_id": item.cluster_id,
                "cluster_ids": list(item.cluster_ids or (item.cluster_id,)),
                "scenario_id": item.scenario_id,
                "resources": {},
            }
            for item in plan.selected
        }
        state = FixtureState(
            schema="kamiwaza.fixture-state/v1",
            provider_revision=self.revision,
            plan_digest=model_digest(plan),
            runtime_digest=model_digest(runtime),
            run_id=runtime.run_id,
            owner_token_digest=owner_digest(runtime.run_id, self.revision),
            journal=(),
            opaque={"edges": cast(JsonValue, edges)},
        )
        return self._write(state)

    def record(
        self,
        state: FixtureState,
        *,
        target_id: str,
        resource_type: str,
        resource_id: str,
        opaque: Mapping[str, Any] | None = None,
    ) -> FixtureState:
        mutation = FixtureMutation(
            sequence=len(state.journal) + 1,
            target_id=target_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action="created",
        )
        next_state = state.model_copy(update={"journal": (*state.journal, mutation)})
        if opaque:
            opaque_values = _mapping(next_state.opaque)
            edges = _mapping(opaque_values.get("edges"))
            edge = _mapping(edges.get(target_id))
            resources = _mapping(edge.get("resources"))
            resources.update(opaque)
            edge["resources"] = resources
            edges[target_id] = edge
            next_state = next_state.model_copy(
                update={
                    "opaque": cast(
                        JsonValue, {**opaque_values, "edges": cast(JsonValue, edges)}
                    )
                }
            )
        return self._write(next_state)

    def update_edge(
        self, state: FixtureState, target_id: str, values: Mapping[str, Any]
    ) -> FixtureState:
        opaque_values = _mapping(state.opaque)
        edges = _mapping(opaque_values.get("edges"))
        edge = _mapping(edges.get(target_id))
        edge.update(values)
        edges[target_id] = edge
        return self._write(
            state.model_copy(
                update={
                    "opaque": cast(
                        JsonValue, {**opaque_values, "edges": cast(JsonValue, edges)}
                    )
                }
            )
        )

    def _write(self, state: FixtureState) -> FixtureState:
        authenticated = sign_state(state, self.key)
        self.writer.write(authenticated)
        return authenticated


def _mapping(value: Any) -> dict[str, Any]:
    """Narrow a JSON object from the opaque, versioned state envelope."""

    return dict(value) if isinstance(value, Mapping) else {}
