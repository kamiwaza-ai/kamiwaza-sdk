"""ENG-5955 — persona clients in the extension-contract support harness
must each own an isolated token store. The SDK default is FileTokenStore
at ``~/.kamiwaza/token.json`` — a single, shared file across all personas
in the same process. Without isolation, persona B's authenticator silently
loads persona A's cached token, and the auth/spoof contract tests would
fail to detect privilege escalation.

This unit test exercises ``support.auth_ops.client_for_role`` with a
stub harness so it runs in CI without a live cluster, and asserts each
persona client carries its own ``InMemoryTokenStore`` instance.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from kamiwaza_sdk.token_store import InMemoryTokenStore
from tests.e2e.extension_contract.harness_test_helpers import bootstrap_state_fixture
from tests.e2e.extension_contract.support import auth_ops
from tests.e2e.extension_contract.support.settings import LiveExtensionSettings

pytestmark = pytest.mark.unit


@pytest.fixture
def stub_harness(tmp_path: Any) -> SimpleNamespace:
    state = bootstrap_state_fixture(tmp_path)
    settings = LiveExtensionSettings(
        base_url="https://kamiwaza.test/api",
        origin="https://kamiwaza.test",
        username="admin",
        password="secret",
        api_key=None,
        verify_ssl=False,
        deployment_timeout_seconds=60,
        probe_timeout_seconds=30,
        bootstrap_state=state,
        control_plane_role_key=None,
    )
    return SimpleNamespace(
        settings=settings,
        bootstrap_state=state,
        _persona_clients={},
        _bootstrap_clients={},
    )


def test_each_persona_gets_isolated_in_memory_token_store(
    stub_harness: SimpleNamespace,
) -> None:
    """Two personas must not share a token store; otherwise persona B
    would silently inherit persona A's cached bearer token."""
    # Force the password code path (resolve_api_key returns None on the
    # stub bootstrap state). resolve_password normally shells out to
    # kubectl/kz-login — stub it.
    with (
        patch.object(
            type(stub_harness.bootstrap_state),
            "resolve_password",
            return_value="stub-password",
        ),
        patch.object(auth_ops, "_validate_client") as validate,
    ):
        validate.return_value = None
        client_a = auth_ops.client_for_role(stub_harness, "admin")
        client_b = auth_ops.client_for_role(stub_harness, "allowed_non_admin")

    authenticator_a = client_a.authenticator
    authenticator_b = client_b.authenticator

    assert isinstance(authenticator_a.token_store, InMemoryTokenStore)
    assert isinstance(authenticator_b.token_store, InMemoryTokenStore)
    assert authenticator_a.token_store is not authenticator_b.token_store


def test_persona_token_does_not_leak_to_sibling(
    stub_harness: SimpleNamespace,
) -> None:
    """Writing a token to persona A's store must not appear in persona B's."""
    import time

    from kamiwaza_sdk.token_store import StoredToken

    with (
        patch.object(
            type(stub_harness.bootstrap_state),
            "resolve_password",
            return_value="stub-password",
        ),
        patch.object(auth_ops, "_validate_client") as validate,
    ):
        validate.return_value = None
        client_a = auth_ops.client_for_role(stub_harness, "admin")
        client_b = auth_ops.client_for_role(stub_harness, "allowed_non_admin")

    token_a = StoredToken(
        access_token="admin-token", refresh_token=None, expires_at=time.time() + 60
    )
    client_a.authenticator.token_store.save(token_a)

    assert client_b.authenticator.token_store.load() is None
