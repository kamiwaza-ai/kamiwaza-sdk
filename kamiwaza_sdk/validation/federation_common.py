"""Small shared helpers for the owned shared-IdP validation provider."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any, cast

from kamiwaza_sdk.validation.federation_fixture import (
    DEFAULT_TENANT_ID,
    PERSONAS,
    TENANT_NEGATIVE_PERSONAS,
    UNONBOARDED_PERSONA,
)
from kamiwaza_sdk.validation.federation_runtime import read_file_reference
from kamiwaza_sdk.validation.federation_spec import (
    SHARED_REALM_PERSONA_PASSWORD_REF,
)
from kamiwaza_sdk.validation.models import (
    FixtureState,
    ResolvedScenario,
    RuntimeContext,
)
from kamiwaza_sdk.validation.provider import ProviderContractError


def all_personas() -> tuple[tuple[str, dict[str, str]], ...]:
    values = [
        (username, {"clearance": clearance, "tenant_id": DEFAULT_TENANT_ID})
        for clearance, username in PERSONAS.items()
    ]
    values.append(
        (UNONBOARDED_PERSONA, {"clearance": "U", "tenant_id": DEFAULT_TENANT_ID})
    )
    values.extend(TENANT_NEGATIVE_PERSONAS.values())
    return tuple(values)


def initial_tuples(
    dataset_urn: str | None = None,
    *,
    job_executor: bool,
    model_id: str | None = None,
) -> list[dict[str, str]]:
    tuples: list[dict[str, str]] = []
    if dataset_urn:
        tuples.append(
            {
                "subject": "user:{{user_id}}",
                "relation": "viewer",
                "object": f"dataset:{dataset_urn}",
            }
        )
    if job_executor:
        tuples.append(
            {
                "subject": "user:{{user_id}}",
                "relation": "executor",
                "object": "cluster_jobs:__all__",
            }
        )
    if model_id:
        tuples.append(
            {
                "subject": "user:{{user_id}}",
                "relation": "viewer",
                "object": f"model:{model_id}",
            }
        )
    return tuples


def selected_endpoints(selected: ResolvedScenario) -> tuple[str, str]:
    values = selected.cluster_ids or (selected.cluster_id,)
    if len(values) != 2 or values[0] == values[1]:
        raise ProviderContractError(
            "shared-IdP selection must bind two distinct clusters"
        )
    return values[0], values[1]


def receiver_id(selected: ResolvedScenario) -> str:
    return selected_endpoints(selected)[1]


def edge_receiver_id(edge: Mapping[str, Any]) -> str:
    values = edge_cluster_ids(edge)
    if len(values) != 2:
        raise ProviderContractError("fixture state edge does not bind two clusters")
    return values[1]


def edge_state(state: FixtureState, target_id: str) -> Mapping[str, Any]:
    edges = state.opaque.get("edges")
    if not isinstance(edges, Mapping) or not isinstance(edges.get(target_id), Mapping):
        raise ProviderContractError("fixture state is missing selected edge")
    return cast(Mapping[str, Any], edges[target_id])


def resource_map(edge: Mapping[str, Any]) -> Mapping[str, Any]:
    resources = edge.get("resources")
    if not isinstance(resources, Mapping):
        raise ProviderContractError("fixture state edge resources are invalid")
    return {**dict(edge), **dict(resources)}


def edge_cluster_ids(edge: Mapping[str, Any]) -> tuple[str, ...]:
    values = edge.get("cluster_ids")
    if isinstance(values, list):
        return tuple(value for value in values if isinstance(value, str))
    fallback = edge.get("cluster_id")
    return (fallback,) if isinstance(fallback, str) else ()


def required_text(value: Any, key: str) -> str:
    candidate = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
    if not isinstance(candidate, str) or not candidate:
        raise ProviderContractError(f"shared-IdP value {key!r} is missing")
    return candidate


def optional_text(value: Any, key: str) -> str | None:
    candidate = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
    if isinstance(candidate, str) and candidate:
        return candidate
    if candidate is None or isinstance(candidate, (Mapping, list, tuple, set)):
        return None
    rendered = str(candidate)
    return rendered or None


def federation_name(run_id: str, target_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{target_id}".encode()).hexdigest()[:16]
    return f"kz-validation-{digest}"


def jwt_subject(token: str) -> str:
    parts = token.split(".")
    if len(parts) != 3:
        raise ProviderContractError("shared-IdP token is not a JWT")
    encoded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ProviderContractError("shared-IdP token payload is invalid") from None
    subject = payload.get("sub") if isinstance(payload, Mapping) else None
    if not isinstance(subject, str) or not subject:
        raise ProviderContractError("shared-IdP token has no subject")
    return subject


def token_client(base_url: str, token: str) -> Any:
    from kamiwaza_sdk import KamiwazaClient

    return KamiwazaClient(base_url=base_url, api_key=token)


def close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def close_resource(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def read_persona_password(runtime: RuntimeContext) -> str:
    reference = runtime.secret_refs.get(SHARED_REALM_PERSONA_PASSWORD_REF)
    if not reference:
        raise ProviderContractError("shared-IdP persona password reference is missing")
    return read_file_reference(reference, label="shared-IdP persona password")


def read_execution_gate(client: Any) -> Any | None:
    try:
        binding = client.cluster.get_execution_gate()
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            return None
        raise
    return binding.model_dump(mode="json") if hasattr(binding, "model_dump") else binding


def json_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return str(value)
