"""T7.5 / ENG-5039 — FederationsAPI on the canonical kamiwaza_sdk surface.

WS-M3.2 service migration. Brings the federation surface from
``kamiwaza/federations.py`` (M1+ skeleton) into
``kamiwaza_sdk/services/federations.py`` per design v0.3.7 §4.2.11.

Includes the **ENG-5016 fix** at migration time per design §6.2 WS-M3.2
T7.5:

- ``pair()`` accepts ``preshared_key`` kwarg. Auto-mints UUID4 when None
  (Mode A default).
- ``pair()`` accepts ``callback_hostname`` kwarg; passes to the server's
  CreateClusterFederation body when supplied.
- ``pair()`` drops the bogus ``remote_url`` server-body field — the server's
  ``CreateClusterFederation`` Pydantic schema doesn't accept it. The SDK
  still accepts ``remote_url`` as a kwarg (backward compat with setup.py
  call shape); it's used to derive ``remote_ips`` when callers don't
  supply them explicitly.

Three-mode PSK contract per design OQ-17:

- Mode A (default): SDK mints UUID4. Suitable for single-operator setups
  where the same operator has admin on both clusters (the demo case).
- Mode B: caller supplies via env / config / interactive prompt. Same
  single-operator threat model as A, but caller controls the value.
- Mode C: caller receives PSK out-of-band (signed email, secrets manager,
  paper). Suitable for cross-org pairings where the PSK is the actual
  Hop-2 auth gate at the unauthenticated /pair_federation endpoint.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

import pytest

pytestmark = pytest.mark.unit


class _MockClient:
    """Mock KamiwazaClient that records posts + dispenses pre-canned responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self._responses: dict[tuple[str, str], Any] = {}

    def expect(self, method: str, path: str, response: Any) -> None:
        self._responses[(method.upper(), path)] = response

    def _dispatch(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        response = self._responses.get((method.upper(), path))
        if response is None:
            raise KeyError(f"No mock response set for {method} {path}")
        return response

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._dispatch("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._dispatch("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self._dispatch("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self._dispatch("DELETE", path, **kwargs)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Bridge for service code that calls ``self.client._request``
        directly. Supports both the legacy ``kamiwaza.Kamiwaza._request``
        and the canonical ``kamiwaza_sdk.KamiwazaClient._request`` shape
        without service-side changes during the WS-M3.2 transition."""
        return self._dispatch(method, path, **kwargs)


def _stage_pair_responses(
    client: _MockClient,
    *,
    fed_id: str = "fed-orion-abc",
    final_status: str = "PAIRED",
    callback_hostname: Optional[str] = "edge.lyra.example.com",
) -> None:
    """Stage the two-call /federations → /pair sequence with stable shapes."""
    client.expect(
        "POST",
        "/cluster/federations",
        {
            "id": fed_id,
            "status": "PAIRING",
            "remote_cluster_name": "ORION",
            "remote_ips": [{"ip": "10.0.0.99", "primary": True}],
        },
    )
    client.expect(
        "POST",
        f"/cluster/federations/{fed_id}/pair",
        {
            "id": fed_id,
            "status": final_status,
            "remote_cluster_name": "ORION",
            "remote_ips": [{"ip": "10.0.0.99", "primary": True}],
            "callback_hostname": callback_hostname,
        },
    )


def _create_call(client: _MockClient) -> Tuple[str, Dict[str, Any]]:
    """Extract the POST /api/cluster/federations body for assertions."""
    for method, path, kwargs in client.calls:
        if (method, path) == ("POST", "/cluster/federations"):
            body = kwargs.get("json")
            assert isinstance(body, dict), "create call body must be a dict"
            return path, body
    raise AssertionError("No POST /api/cluster/federations call recorded")


# ---------------------------------------------------------------------------
# Import surface — service module exists on the canonical kamiwaza_sdk path
# ---------------------------------------------------------------------------


def test_federations_service_importable_from_canonical_surface() -> None:
    from kamiwaza_sdk.services.federations import (
        FederationProxy,
        FederationsAPI,
        FederationUsersAPI,
    )

    assert isinstance(FederationsAPI, type)
    assert isinstance(FederationProxy, type)
    assert isinstance(FederationUsersAPI, type)


# ---------------------------------------------------------------------------
# ENG-5016 PSK fix — auto-mint UUID4 when not supplied
# ---------------------------------------------------------------------------


def test_pair_auto_mints_uuid4_preshared_key_when_none() -> None:
    """Mode A default: when caller doesn't supply preshared_key, the SDK
    mints a UUID4 and sends it in the create body. The server's
    CreateClusterFederation schema requires preshared_key (HTTP 422
    otherwise) — this is the gap ENG-5016 surfaced in M4 UAT."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client)

    api = FederationsAPI(client)
    api.pair(
        name="ORION",
        role="initiator",
        remote_url="https://orion.example.com",
    )

    _, body = _create_call(client)
    assert "preshared_key" in body, "preshared_key MUST be sent on the create body"
    # Auto-minted value must be a valid UUID4 (Mode A contract).
    minted = body["preshared_key"]
    parsed = UUID(minted)
    assert parsed.version == 4, "Auto-minted PSK must be a UUID4 (Mode A)"


def test_pair_uses_caller_supplied_preshared_key_verbatim() -> None:
    """Modes B/C: caller supplies the PSK out-of-band. SDK passes it
    verbatim to the server (no transformation, no validation beyond
    type-checking)."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client)

    api = FederationsAPI(client)
    api.pair(
        name="ORION",
        role="initiator",
        remote_url="https://orion.example.com",
        preshared_key="custom-psk-from-vault-12345",
    )

    _, body = _create_call(client)
    assert body["preshared_key"] == "custom-psk-from-vault-12345"


def test_pair_minted_psk_is_unique_per_call() -> None:
    """Two consecutive pair() calls without caller PSK must mint distinct
    values — otherwise two adjacent demos would share a PSK accidentally."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    psks: list[str] = []
    for _ in range(2):
        client = _MockClient()
        _stage_pair_responses(client)
        api = FederationsAPI(client)
        api.pair(name="ORION", role="initiator", remote_url="https://orion.example.com")
        _, body = _create_call(client)
        psks.append(body["preshared_key"])
    assert psks[0] != psks[1], "Auto-minted PSKs must be unique per call"


# ---------------------------------------------------------------------------
# Server-body schema match — remote_url DROPPED, callback_hostname HONORED
# ---------------------------------------------------------------------------


def test_pair_body_omits_bogus_remote_url_field() -> None:
    """The server's CreateClusterFederation schema does NOT accept
    remote_url. Sending it causes 422 on Pydantic v2 strict-extra realms
    (and is silently ignored on others). M3 pair() incorrectly sent it;
    M3.2 drops it."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client)

    api = FederationsAPI(client)
    api.pair(
        name="ORION",
        role="initiator",
        remote_url="https://orion.example.com",
    )

    _, body = _create_call(client)
    assert "remote_url" not in body, "remote_url must NOT be on the wire body"


def test_pair_derives_remote_ips_from_remote_url_when_not_supplied() -> None:
    """Backward-compat with setup.py: callers can still pass remote_url
    and the SDK derives remote_ips from its host portion. The server
    needs remote_ips on initiator role (per CreateClusterFederation
    @root_validator), so the SDK fills in the gap."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client)

    api = FederationsAPI(client)
    api.pair(
        name="ORION",
        role="initiator",
        remote_url="https://orion.example.com",
    )

    _, body = _create_call(client)
    assert "remote_ips" in body
    assert isinstance(body["remote_ips"], list)
    assert len(body["remote_ips"]) >= 1
    primary = next((ip for ip in body["remote_ips"] if ip.get("primary")), None)
    assert primary is not None
    assert primary["ip"] == "orion.example.com"


def test_pair_uses_explicit_remote_ips_when_supplied() -> None:
    """Caller-supplied remote_ips override the URL-derived ones."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client)

    explicit = [{"ip": "192.168.1.42", "primary": True}]
    api = FederationsAPI(client)
    api.pair(
        name="ORION",
        role="initiator",
        remote_url="https://orion.example.com",
        remote_ips=explicit,
    )

    _, body = _create_call(client)
    assert body["remote_ips"] == explicit


def test_pair_forwards_callback_hostname_when_supplied() -> None:
    """ENG-5016: callback_hostname kwarg lands on the create body so the
    receiver knows how to reach back during /pair handshake."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client)

    api = FederationsAPI(client)
    api.pair(
        name="ORION",
        role="initiator",
        remote_url="https://orion.example.com",
        callback_hostname="lyra.lan.example.com",
    )

    _, body = _create_call(client)
    assert body.get("callback_hostname") == "lyra.lan.example.com"


def test_pair_omits_callback_hostname_when_none() -> None:
    """When callback_hostname is not supplied, the SDK leaves the field off
    the body so the server's auto-detection runs (FR-37 callback-host
    auto-exchange)."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client)

    api = FederationsAPI(client)
    api.pair(
        name="ORION",
        role="initiator",
        remote_url="https://orion.example.com",
    )

    _, body = _create_call(client)
    # Either absent OR explicit None — both let the server's auto-detect run.
    assert body.get("callback_hostname") in (None,)


def test_pair_body_contains_role_and_name() -> None:
    """Schema essentials — server requires remote_cluster_name + role."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client)

    api = FederationsAPI(client)
    api.pair(
        name="ORION",
        role="initiator",
        remote_url="https://orion.example.com",
    )

    _, body = _create_call(client)
    assert body["remote_cluster_name"] == "ORION"
    assert body["role"] == "initiator"


# ---------------------------------------------------------------------------
# Two-call pair flow (create → /pair) preserved from M1+ shape
# ---------------------------------------------------------------------------


def test_pair_does_two_step_create_then_pair_call() -> None:
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    _stage_pair_responses(client, fed_id="fed-orion-xyz")

    api = FederationsAPI(client)
    result = api.pair(
        name="ORION",
        role="initiator",
        remote_url="https://orion.example.com",
    )

    # Two POSTs in order: create then drive the handshake.
    assert len(client.calls) == 2
    assert client.calls[0][1] == "/cluster/federations"
    assert client.calls[1][1] == "/cluster/federations/fed-orion-xyz/pair"
    # Final returned status reflects the second call's response.
    assert result.status == "PAIRED"


# ---------------------------------------------------------------------------
# FederationProxy + users sub-API
# ---------------------------------------------------------------------------


def test_indexed_access_returns_proxy() -> None:
    from kamiwaza_sdk.services.federations import FederationProxy, FederationsAPI

    client = _MockClient()
    api = FederationsAPI(client)
    proxy = api["ORION"]
    assert isinstance(proxy, FederationProxy)
    assert proxy.name == "ORION"


def test_users_add_posts_initial_tuples() -> None:
    from kamiwaza_sdk.services.federations import FederationsAPI

    client = _MockClient()
    # Name → id resolution.
    client.expect(
        "GET",
        "/cluster/federations",
        {
            "items": [
                {
                    "id": "fed-orion-id",
                    "status": "PAIRED",
                    "remote_cluster_name": "ORION",
                }
            ]
        },
    )
    client.expect(
        "POST",
        "/cluster/federations/fed-orion-id/users",
        {
            "federation_id": "fed-orion-id",
            "external_id": "cdr-baker@lyra-uuid",
            "auto_provisioned": False,
        },
    )

    initial_tuples = [
        {
            "subject": "user:cdr-baker@lyra-uuid",
            "relation": "viewer",
            "object": "cluster:ORION",
        }
    ]
    api = FederationsAPI(client)
    user = api["ORION"].users.add(
        external_id="cdr-baker@lyra-uuid",
        initial_tuples=initial_tuples,
    )

    assert user.federation_id == "fed-orion-id"
    assert user.external_id == "cdr-baker@lyra-uuid"
    # Verify the request body included initial_tuples.
    add_call = next(
        kw for m, p, kw in client.calls if p.endswith("/users") and m == "POST"
    )
    assert add_call["json"]["initial_tuples"] == initial_tuples


# ---------------------------------------------------------------------------
# Three-mode PSK contract documented in docstring (operator-facing intent)
# ---------------------------------------------------------------------------


def test_pair_docstring_documents_three_modes() -> None:
    """The pair() docstring must surface the three PSK modes so operators
    reading help(kz.federations.pair) understand which mode they're using."""
    from kamiwaza_sdk.services.federations import FederationsAPI

    doc = FederationsAPI.pair.__doc__
    assert doc is not None
    # Mode names appear as discrete tokens in the docstring.
    assert re.search(r"Mode A\b", doc), "Mode A (auto-mint) must be named"
    assert re.search(r"Mode B\b", doc), "Mode B (caller-supplied) must be named"
    assert re.search(r"Mode C\b", doc), "Mode C (cross-org out-of-band) must be named"


# ---------------------------------------------------------------------------
# Legacy kamiwaza.* namespace was removed in WS-M3.2 (design v0.3.7 §4.2.11
# revised OQ-17). The bridge-identity test is deleted.
# ---------------------------------------------------------------------------


def test_kamiwaza_federations_namespace_is_removed() -> None:
    """The interim ``kamiwaza.federations`` shim (v0.2.0 carcass) is gone."""
    import pytest

    with pytest.raises(ModuleNotFoundError):
        __import__("kamiwaza.federations")


def test_federation_aware_services_reexported_from_kamiwaza_sdk_services() -> None:
    """PR-feedback M7 (architecture consistency): ``FederationsAPI`` and
    its 6 federation-aware peers must be importable from
    ``kamiwaza_sdk.services`` like every other service.

    Before the fix, ``from kamiwaza_sdk.services import FederationsAPI``
    raised AttributeError — only the fully-qualified submodule path or
    the lazy ``client.federations`` property worked, breaking the
    invariant with peers like ``ClusterService`` and ``RetrievalService``.
    """
    from kamiwaza_sdk import services
    from kamiwaza_sdk.services import (
        FederationsAPI,
    )

    # Identity check: same class object as the submodule export.
    from kamiwaza_sdk.services.federations import FederationsAPI as Canonical

    assert FederationsAPI is Canonical

    # Every new service appears in ``services.__all__``.
    expected = {
        "ClusterAPI",
        "DatasetsAPI",
        "FederationProxy",
        "FederationUsersAPI",
        "FederationsAPI",
        "GatesAPI",
        "JobsAPI",
        "RetrievalAPI",
        "SubjectGrantsAPI",
        "SubjectsAPI",
    }
    missing = expected - set(services.__all__)
    assert not missing, f"missing from kamiwaza_sdk.services.__all__: {missing}"
