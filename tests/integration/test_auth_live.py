from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from kamiwaza_sdk import KamiwazaClient
from kamiwaza_sdk.authentication import UserPasswordAuthenticator
from kamiwaza_sdk.exceptions import AuthenticationError, KamiwazaError, NotFoundError
from kamiwaza_sdk.schemas.auth import PATCreate

from . import _disposable_local_user as disposable

pytestmark = [pytest.mark.integration, pytest.mark.live, pytest.mark.withoutresponses]


def test_password_authentication_allows_whoami(
    client_factory,
    live_server_available: str,
    live_username: str,
    live_password_required: str,
):
    client: KamiwazaClient = client_factory(base_url=live_server_available)
    client.authenticator = UserPasswordAuthenticator(live_username, live_password_required, client.auth)

    whoami = client.get("/whoami")
    assert whoami is not None
    user = client.auth.get_current_user()
    assert user.username == live_username


def test_pat_lifecycle_supports_api_key_auth(
    client_factory,
    live_server_available: str,
    live_username: str,
    live_password_required: str,
):
    admin_client: KamiwazaClient = client_factory(base_url=live_server_available)
    admin_client.authenticator = UserPasswordAuthenticator(live_username, live_password_required, admin_client.auth)

    baseline_user = admin_client.auth.get_current_user()
    expected_sub = baseline_user.sub
    expected_username = baseline_user.username

    pat_name = f"sdk-m1-{uuid.uuid4().hex[:8]}-{int(time.time())}"
    pat_response = admin_client.auth.create_pat(
        PATCreate(name=pat_name, ttl_seconds=900, scope="openid", aud="kamiwaza-platform")
    )
    pat_token = pat_response.token
    pat_jti = pat_response.pat.jti

    try:
        pat_client: KamiwazaClient = client_factory(base_url=live_server_available, api_key=pat_token)
        profile = pat_client.auth.get_current_user()
        assert profile.sub == expected_sub
        assert profile.username in {expected_username, expected_sub}
    finally:
        admin_client.auth.revoke_pat(pat_jti)

    revoked_client: KamiwazaClient = client_factory(base_url=live_server_available, api_key=pat_token)
    with pytest.raises(AuthenticationError):
        revoked_client.auth.get_current_user()


@dataclass(frozen=True)
class _MintedPat:
    jti: str
    token: str = field(repr=False)


def _mint_pat(client: KamiwazaClient, name: str) -> _MintedPat:
    response = disposable.mint_pat(
        client, PATCreate(name=name, ttl_seconds=900, scope="openid", aud="kamiwaza-platform")
    )
    return _MintedPat(jti=response.pat.jti, token=response.token)


def _revoke_every(client: KamiwazaClient, minted: list[_MintedPat]) -> None:
    # Independent of the inventory under test: a PAT the listing omits is still
    # revoked. Every jti is attempted and the first failure is raised only after
    # all attempts. A jti the platform no longer knows is already in the state
    # this cleanup wants, so a 404 is not a failure; whether re-revoking answers
    # 404 or succeeds is not pinned anywhere.
    failures: list[KamiwazaError] = []
    for pat in minted:
        try:
            client.auth.revoke_pat(pat.jti)
        except NotFoundError:
            continue
        except KamiwazaError as exc:
            failures.append(exc)
    if failures:
        raise failures[0]


# The caller chooses a PAT's name, so a name that merely looks like a JWT is not
# token material the inventory exposed. The scan reads the operator's whole
# inventory, so ``aud`` and ``scope`` can hold whatever other runs supplied;
# they stay in it because a caller-supplied value there can only raise a false
# positive, never hide token material.
_PAT_FIELDS_EXCLUDED_FROM_JWT_SCAN = frozenset({"name"})


@pytest.fixture
def password_admin_client(
    client_factory,
    live_server_available: str,
    live_username: str,
    live_password_required: str,
) -> KamiwazaClient:
    """The operator, signed in by password grant, holding only the access token.

    The password stays in this fixture, so a failing test never renders it as
    one of its own arguments; see ``access_token_client`` for why the session is
    not logged out.
    """
    __tracebackhide__ = True  # its password argument must not be rendered
    return disposable.access_token_client(
        client_factory, live_server_available, live_username, live_password_required
    )


def _all_keys(value: Any) -> Iterator[str]:
    # Every mapping key at any depth, so a nested credential field is not missed.
    if isinstance(value, dict):
        yield from _mapping_keys(value)
    elif isinstance(value, list):
        for item in value:
            yield from _all_keys(item)


def _mapping_keys(mapping: dict[Any, Any]) -> Iterator[str]:
    for key, item in mapping.items():
        yield str(key)
        yield from _all_keys(item)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def test_pat_inventory_lists_without_token_material_and_revokes_one(
    client_factory,
    live_server_available: str,
    password_admin_client: KamiwazaClient,
):
    admin_client = password_admin_client
    owner = admin_client.auth.get_current_user().sub

    run_name = f"sdk-inventory-{uuid.uuid4().hex[:8]}-{int(time.time())}"
    minted: list[_MintedPat] = []
    try:
        for index in range(3):
            minted.append(_mint_pat(admin_client, f"{run_name}-{index + 1}"))
        # The middle PAT is revoked, so revoking by position (oldest or newest)
        # instead of by jti cannot pass.
        kept_first, revoked_pat, kept_last = minted

        inventory = {pat.jti: pat for pat in admin_client.auth.list_pats().pats}
        assert all(pat.jti in inventory for pat in minted), "a minted PAT is missing from the inventory"
        assert all(inventory[pat.jti].revoked is False for pat in minted)
        assert all(inventory[pat.jti].owner_id == owner for pat in minted)
        # The raw body, not the typed model: the model would silently drop a token field.
        raw_inventory = admin_client.get("/auth/pats")
        raw_text = json.dumps(raw_inventory)
        token_material_listed = any(pat.token in raw_text for pat in minted)
        platform_text = json.dumps(
            [
                {key: value for key, value in entry.items() if key not in _PAT_FIELDS_EXCLUDED_FROM_JWT_SCAN}
                for entry in raw_inventory["pats"]
            ]
            + [{key: value for key, value in raw_inventory.items() if key != "pats"}]
        )
        jwt_shaped_value_listed = re.search(r"eyJ[\w-]+\.[\w-]+\.", platform_text) is not None
        credential_like_fields = sorted(
            {key for key in _all_keys(raw_inventory) if "token" in key.lower() or "secret" in key.lower()}
        )
        assert token_material_listed is False, "the PAT inventory exposed a minted token"
        assert jwt_shaped_value_listed is False, "the PAT inventory contains a JWT-shaped value"
        assert credential_like_fields == [], "the PAT inventory carries credential-like fields"

        pat_config = admin_client.get("/auth/pat-config")
        ttl = pat_config["ttl"]
        bounds = (ttl["min_seconds"], ttl["default_seconds"], ttl["max_seconds"])
        assert all(_is_count(bound) for bound in bounds), "PAT TTL bounds are not integers"
        assert 0 < ttl["min_seconds"] <= ttl["default_seconds"] <= ttl["max_seconds"]
        assert _is_str_list(pat_config["available_scopes"]), "available_scopes is not a list of strings"
        assert _is_str_list(pat_config["scope_hierarchy"]), "scope_hierarchy is not a list of strings"
        assert isinstance(pat_config["default_scope"], str), "default_scope is not a string"
        assert pat_config["available_scopes"], "PAT configuration offers no scopes"
        assert set(pat_config["available_scopes"]) <= set(pat_config["scope_hierarchy"])
        assert pat_config["default_scope"] in pat_config["available_scopes"]

        revoked_client: KamiwazaClient = client_factory(base_url=live_server_available, api_key=revoked_pat.token)
        assert revoked_client.auth.get_current_user().sub == owner

        admin_client.auth.revoke_pat(revoked_pat.jti)

        after_revoke = {pat.jti: pat for pat in admin_client.auth.list_pats().pats}
        assert all(pat.jti in after_revoke for pat in minted), (
            "a minted PAT left the inventory"
        )
        assert [after_revoke[pat.jti].revoked for pat in minted] == [False, True, False]
        with pytest.raises(AuthenticationError):
            client_factory(base_url=live_server_available, api_key=revoked_pat.token).auth.get_current_user()
        for kept_pat in (kept_first, kept_last):
            kept_client: KamiwazaClient = client_factory(base_url=live_server_available, api_key=kept_pat.token)
            assert kept_client.auth.get_current_user().sub == owner
    finally:
        _revoke_every(admin_client, minted)

    final_inventory = {pat.jti: pat for pat in admin_client.auth.list_pats().pats}
    assert all(pat.jti in final_inventory for pat in minted), (
        "a minted PAT left the inventory"
    )
    assert all(final_inventory[pat.jti].revoked for pat in minted), "a minted PAT is still active after cleanup"
