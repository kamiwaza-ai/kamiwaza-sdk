"""Authenticated fixture-state persistence for inference validation."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import SplitResult, unquote, urlsplit

from pydantic import JsonValue

from kamiwaza_sdk.validation.inference_evidence import mapping, target_state
from kamiwaza_sdk.validation.inference_spec import (
    INFERENCE_PROVIDER_REVISION,
    TargetParameters,
)
from kamiwaza_sdk.validation.models import (
    FixtureMutation,
    FixtureState,
    RuntimeContext,
    ScenarioPlan,
)
from kamiwaza_sdk.validation.provider import (
    FixtureStateWriter,
    ProviderContractError,
)
from kamiwaza_sdk.validation.registry import model_digest

_STATE_MAC_KEY = "ownership_mac"
_KEY_DERIVATION_DOMAIN = b"kamiwaza.sdk.inference.ownership-key/v1\0"
OwnershipKeyResolver = Callable[[RuntimeContext], bytes]


class InferenceStateAuthenticator:
    """Bind inference state authorization to runtime-secret key material."""

    def __init__(self, key_resolver: OwnershipKeyResolver | None = None) -> None:
        self._key_resolver = key_resolver or runtime_ownership_key

    def key(self, runtime: RuntimeContext) -> bytes:
        return self._key_resolver(runtime)

    def validate(self, runtime: RuntimeContext, state: FixtureState) -> None:
        _validate_owner_digest(runtime, state)
        validate_state_mac(state, self.key(runtime))


@dataclass(frozen=True)
class InferenceStateStore:
    """Persist complete authenticated state snapshots around owned mutations."""

    writer: FixtureStateWriter
    ownership_key: bytes

    def initial(
        self,
        plan: ScenarioPlan,
        runtime: RuntimeContext,
        parameters: Mapping[str, TargetParameters],
    ) -> FixtureState:
        targets = {
            item.target_id: {
                "cluster_id": item.cluster_id,
                "parameters": _parameter_payload(parameters[item.target_id]),
                "phases": {},
                "runtime": _parameter_payload(parameters[item.target_id]),
            }
            for item in plan.selected
        }
        owner = hashlib.sha256(
            f"{runtime.run_id}:{INFERENCE_PROVIDER_REVISION}".encode()
        ).hexdigest()
        state = FixtureState(
            schema="kamiwaza.fixture-state/v1",
            provider_revision=INFERENCE_PROVIDER_REVISION,
            plan_digest=model_digest(plan),
            runtime_digest=model_digest(runtime),
            run_id=runtime.run_id,
            owner_token_digest=f"sha256:{owner}",
            journal=(),
            opaque={"targets": cast(JsonValue, targets)},
        )
        return self._write(state)

    def record_created(
        self, state: FixtureState, target_id: str, deployment_id: str
    ) -> FixtureState:
        state = self.set_target_value(
            state, target_id, "deployment_id", deployment_id
        )
        mutation = FixtureMutation(
            sequence=len(state.journal) + 1,
            target_id=target_id,
            resource_type="model-deployment",
            resource_id=deployment_id,
            action="created",
        )
        return self._write(
            state.model_copy(update={"journal": (*state.journal, mutation)})
        )

    def record_phase(
        self,
        state: FixtureState,
        target_id: str,
        case_id: str,
        outcome: Mapping[str, Any],
    ) -> FixtureState:
        target = dict(target_state(state, target_id))
        phases = dict(mapping(target.get("phases"), "target phases"))
        phases[case_id] = dict(outcome)
        target["phases"] = phases
        return self._write(_replace_target_state(state, target_id, target))

    def merge_runtime(
        self, state: FixtureState, target_id: str, values: Mapping[str, Any]
    ) -> FixtureState:
        target = dict(target_state(state, target_id))
        runtime = dict(mapping(target.get("runtime"), "target runtime"))
        runtime.update(values)
        target["runtime"] = runtime
        return _replace_target_state(state, target_id, target)

    def set_target_value(
        self, state: FixtureState, target_id: str, key: str, value: Any
    ) -> FixtureState:
        target = dict(target_state(state, target_id))
        target[key] = value
        return _replace_target_state(state, target_id, target)

    def _write(self, state: FixtureState) -> FixtureState:
        authenticated = sign_state(state, self.ownership_key)
        self.writer.write(authenticated)
        return authenticated


def runtime_ownership_key(runtime: RuntimeContext) -> bytes:
    """Derive a provider key from materialized runtime credentials."""

    digest = hashlib.sha256(_KEY_DERIVATION_DOMAIN)
    for cluster in sorted(runtime.clusters, key=lambda item: item.id):
        credential = _read_materialized_credential(cluster.api_key_ref)
        _update_length_prefixed(digest, cluster.id.encode())
        _update_length_prefixed(digest, credential)
    return digest.digest()


def _read_materialized_credential(reference: str) -> bytes:
    path = _materialized_credential_path(reference)
    try:
        credential = path.read_bytes().strip()
    except OSError:
        raise ProviderContractError("runtime API key file is unavailable") from None
    if not credential:
        raise ProviderContractError("runtime API key file is empty")
    return credential


def _materialized_credential_path(reference: str) -> Path:
    parsed = urlsplit(reference)
    _validate_materialized_location(parsed)
    path = Path(unquote(parsed.path))
    if not path.is_absolute():
        raise ProviderContractError("runtime API key file path must be absolute")
    return path


def _validate_materialized_location(parsed: SplitResult) -> None:
    if parsed.scheme != "file":
        raise ProviderContractError(_MATERIALIZATION_ERROR)
    if parsed.netloc:
        raise ProviderContractError(_MATERIALIZATION_ERROR)
    if not parsed.path:
        raise ProviderContractError(_MATERIALIZATION_ERROR)


_MATERIALIZATION_ERROR = "runtime API key must be materialized as a local file"


def _update_length_prefixed(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, byteorder="big"))
    digest.update(value)


def sign_state(state: FixtureState, key: bytes) -> FixtureState:
    """Authenticate all persisted state used to authorize product mutation."""

    if not key:
        raise ProviderContractError("fixture ownership key is empty")
    opaque = dict(state.opaque)
    opaque.pop(_STATE_MAC_KEY, None)
    unsigned = state.model_copy(update={"opaque": opaque})
    signature = hmac.new(key, _canonical_state(unsigned), hashlib.sha256).hexdigest()
    opaque[_STATE_MAC_KEY] = f"sha256:{signature}"
    return state.model_copy(update={"opaque": opaque})


def validate_state_mac(state: FixtureState, key: bytes) -> None:
    """Reject altered state before resolving any product resource identity."""

    actual = state.opaque.get(_STATE_MAC_KEY)
    if not isinstance(actual, str):
        raise ProviderContractError("fixture state ownership MAC is missing")
    expected = sign_state(state, key).opaque[_STATE_MAC_KEY]
    if not isinstance(expected, str):
        raise ProviderContractError("fixture state ownership MAC is invalid")
    if not hmac.compare_digest(actual, expected):
        raise ProviderContractError("fixture state ownership MAC mismatch")


def _validate_owner_digest(runtime: RuntimeContext, state: FixtureState) -> None:
    owner = hashlib.sha256(
        f"{runtime.run_id}:{INFERENCE_PROVIDER_REVISION}".encode()
    ).hexdigest()
    if not hmac.compare_digest(state.owner_token_digest, f"sha256:{owner}"):
        raise ProviderContractError("fixture state ownership digest mismatch")


def _canonical_state(state: FixtureState) -> bytes:
    payload = state.model_dump(mode="json", by_alias=True)
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _parameter_payload(parameters: TargetParameters) -> dict[str, Any]:
    return {
        "repository": parameters.repository,
        "engine": parameters.engine,
        "model_format": parameters.model_format,
        "quantization": parameters.quantization,
        "runtime_profile": parameters.runtime_profile,
        "expected_image": parameters.expected_image,
        "accelerators": [dict(item) for item in parameters.accelerators],
    }


def _replace_target_state(
    state: FixtureState, target_id: str, target: Mapping[str, Any]
) -> FixtureState:
    opaque = dict(state.opaque)
    targets = dict(mapping(opaque.get("targets"), "fixture targets"))
    targets[target_id] = dict(target)
    opaque["targets"] = targets
    return state.model_copy(update={"opaque": opaque})
