from __future__ import annotations

from typing import Any

import pytest
import requests

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import APIError
from kamiwaza_sdk.token_store import InMemoryTokenStore

from .process_utils import describe_auth_validation_error
from .state import LivePersona


def persona(harness: Any, role_key: str) -> LivePersona:
    if harness.bootstrap_state is None:
        pytest.skip(
            "Bootstrap state is required for persona-scoped live auth contracts"
        )
    try:
        return harness.bootstrap_state.persona(role_key)
    except KeyError as exc:
        available = ", ".join(sorted(harness.bootstrap_state.personas))
        pytest.fail(f"{exc}. Available personas: {available}")


def client_for_role(harness: Any, role_key: str) -> KamiwazaClient:
    existing = harness._persona_clients.get(role_key)
    if existing is not None:
        return existing
    current_persona = persona(harness, role_key)
    verify = harness.settings.verify_ssl
    if harness.bootstrap_state is not None:
        api_key = harness.bootstrap_state.resolve_api_key(current_persona)
        if api_key:
            client = KamiwazaClient(
                base_url=harness.settings.base_url,
                api_key=api_key,
                verify=verify,
            )
            _validate_client(client, role_key=role_key, mode="API-key")
            harness._persona_clients[role_key] = client
            return client
    password = (
        harness.bootstrap_state.resolve_password(current_persona)
        if harness.bootstrap_state
        else None
    )
    if not password:
        pytest.skip(f"Could not resolve credentials for persona {role_key!r}")
    bootstrap_client = KamiwazaClient(base_url=harness.settings.base_url, verify=verify)
    harness._bootstrap_clients[role_key] = bootstrap_client
    # ENG-5955: each persona MUST own its own token store. The SDK default
    # is FileTokenStore at ~/.kamiwaza/token.json — shared across personas
    # in the same process, so persona B's authenticator would silently load
    # persona A's cached token and the contract tests would fail to detect
    # privilege escalation.
    client = KamiwazaClient(
        base_url=harness.settings.base_url,
        verify=verify,
        authenticator=UserPasswordAuthenticator(
            username=current_persona.username,
            password=password,
            auth_service=bootstrap_client.auth,
            token_store=InMemoryTokenStore(),
        ),
    )
    _validate_client(client, role_key=role_key, mode="Password")
    harness._persona_clients[role_key] = client
    return client


def auth_headers(harness: Any) -> dict[str, str]:
    token = harness.client.get_bearer_token() or harness.settings.api_key
    if not token:
        pytest.fail("No bearer token available for authenticated app probes")
    return {"Authorization": f"Bearer {token}"}


def auth_headers_for_role(harness: Any, role_key: str) -> dict[str, str]:
    current_persona = persona(harness, role_key)
    client = client_for_role(harness, role_key)
    token = client.get_bearer_token() or (
        harness.bootstrap_state.resolve_api_key(current_persona)
        if harness.bootstrap_state
        else None
    )
    if not token:
        pytest.fail(f"No bearer token available for persona {role_key!r}")
    return {"Authorization": f"Bearer {token}"}


def probe_headers(harness: Any, contract: Any) -> dict[str, str] | None:
    return None if not contract.requires_auth else auth_headers(harness)


def build_live_client(settings: Any) -> KamiwazaClient:
    verify = settings.verify_ssl
    if settings.api_key:
        client = KamiwazaClient(
            base_url=settings.base_url,
            api_key=settings.api_key,
            verify=verify,
        )
        _validate_client(client, role_key="live harness client", mode="API-key")
        return client
    bootstrap = KamiwazaClient(base_url=settings.base_url, verify=verify)
    client = KamiwazaClient(
        base_url=settings.base_url,
        verify=verify,
        authenticator=UserPasswordAuthenticator(
            username=settings.username or "admin",
            password=settings.password or "",
            auth_service=bootstrap.auth,
            # ENG-5955: isolate harness client's token from any persona
            # clients created later. See client_for_role() for rationale.
            token_store=InMemoryTokenStore(),
        ),
    )
    client._bootstrap_client = bootstrap
    _validate_client(client, role_key="live harness client", mode="Password")
    return client


def _validate_client(client: KamiwazaClient, *, role_key: str, mode: str) -> None:
    try:
        client.get("/auth/validate", timeout=10)
    except APIError as exc:
        pytest.fail(
            f"{mode} auth validation failed for {role_key!r}: "
            f"{describe_auth_validation_error(exc)}"
        )
    except (requests.RequestException, OSError) as exc:
        pytest.fail(f"{mode} auth validation could not reach {role_key!r}: {exc}")
    except Exception as exc:  # pragma: no cover - defensive fallback
        pytest.fail(f"{mode} auth validation failed for {role_key!r}: {exc!r}")
