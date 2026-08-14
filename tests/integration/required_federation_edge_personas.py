"""Primary-realm personas and receiver onboarding for the required edge."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from urllib.parse import quote

from tests.integration import _mini_clearance as mc

PERSONAS = {"U": "fed-clr-u", "S": "fed-clr-s", "TS": "fed-clr-ts"}
UNONBOARDED_PERSONA = "fed-clr-unonboarded"


def required_initial_tuples(
    dataset_urn: str,
    *,
    job_executor: bool,
) -> list[dict[str, str]]:
    """Fixture-scoped retrieval authority plus one explicit job submitter."""
    tuples = [
        {
            "subject": "user:{{user_id}}",
            "relation": "viewer",
            "object": f"dataset:{dataset_urn}",
        }
    ]
    if job_executor:
        tuples.append(
            {
                "subject": "user:{{user_id}}",
                "relation": "executor",
                "object": "cluster_jobs:__all__",
            }
        )
    return tuples


def cleanup_brokered_persona(
    receiver: Any, federation_id: str, external_id: str
) -> None:
    """Revoke the exact temporary allowlist row and cancel active jobs."""
    encoded_external_id = quote(external_id, safe="")
    receiver._request(
        "POST",
        f"/cluster/federations/{federation_id}/users/{encoded_external_id}/revoke",
        params={"cancel_in_flight_jobs": "true"},
    )


def _cleanup_primary_persona(initiator: Any, username: str) -> None:
    from kamiwaza_sdk.exceptions import APIError

    try:
        initiator.subjects.delete(username, cascade_grants=True)
    except APIError as exc:
        if exc.status_code != 404:
            raise


def _require_primary_personas_absent(initiator: Any, usernames: list[str]) -> None:
    """Refuse to overwrite an operator-owned primary-realm identity."""
    from kamiwaza_sdk.exceptions import APIError

    for username in usernames:
        try:
            initiator.subjects.get(username)
        except APIError as exc:
            if exc.status_code == 404:
                continue
            raise
        raise AssertionError(
            f"required federation persona already exists on initiator: {username}"
        )


def _login_primary_persona(
    initiator: Any,
    username: str,
    password: str,
    *,
    verify: bool,
) -> dict[str, Any]:
    """Authenticate through the initiator API and prove the returned identity."""
    persona = mc.authed_client(
        initiator.base_url,
        username,
        password,
        verify=verify,
    )
    current = persona.auth.get_current_user()
    token = persona.get_bearer_token()
    assert current.username == username, current
    assert token, f"normal initiator login returned no bearer token for {username}"
    sub = mc.jwt_sub(token)
    assert sub and sub == str(current.sub), current
    return {"client": persona, "token": token, "sub": sub, "username": username}


def _onboard_persona(
    receiver: Any,
    identity: tuple[str, str, str],
    clearance: str,
    persona: dict[str, Any],
) -> str:
    federation_id, source_cluster_id, dataset_urn = identity
    external_id = f"{persona['sub']}@{source_cluster_id}"
    persona["external_id"] = external_id
    receiver._request(
        "POST",
        f"/cluster/federations/{federation_id}/users",
        json={
            "external_id": external_id,
            "initial_tuples": required_initial_tuples(
                dataset_urn,
                job_executor=clearance == "U",
            ),
        },
    )
    return external_id


def provision_primary_personas(
    cleanup: ExitStack,
    clients: Any,
    identity: tuple[str, str, str],
    prerequisites: Any,
) -> dict[str, dict[str, Any]]:
    """Create normal initiator users and onboard only U/S/TS on receiver."""
    auth = prerequisites.persona_auth
    initiator, receiver = clients.initiator, clients.receiver
    specs = [*PERSONAS.items(), ("unonboarded", UNONBOARDED_PERSONA)]
    _require_primary_personas_absent(initiator, [name for _, name in specs])
    mc.declare_clearance_attribute(initiator)
    personas: dict[str, dict[str, Any]] = {}
    for clearance, username in specs:
        value = "U" if clearance == "unonboarded" else clearance
        cleanup.callback(_cleanup_primary_persona, initiator, username)
        initiator.subjects.upsert(
            username,
            attributes={"clearance": value},
            password=auth["password"],
        )
        persona = _login_primary_persona(
            initiator,
            username,
            auth["password"],
            verify=auth["verify"],
        )
        personas[clearance] = persona
        if clearance != "unonboarded":
            external_id = _onboard_persona(receiver, identity, clearance, persona)
            cleanup.callback(
                cleanup_brokered_persona,
                receiver,
                identity[0],
                external_id,
            )
    return personas
