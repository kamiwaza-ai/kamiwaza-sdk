"""C2 (PR feedback) — end-to-end URL construction for migrated services.

WS-M3.2 ship-blocker C1 (double ``/api/`` prefix) escaped CI because the
unit tests mocked at the ``_request`` boundary, intercepting raw endpoint
strings before the URL was assembled. These tests mock at the
``requests.Session.request`` boundary instead, so the full URL is
asserted against the documented base_url convention
(``KamiwazaClient(base_url="https://host/api")``).

If any migrated service ever hardcodes ``/api/`` in its path again, the
URL becomes ``https://host/api/api/...`` and the assertion below fails.
That's the regression guard the original PR was missing.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from kamiwaza_sdk.client import KamiwazaClient

pytestmark = pytest.mark.unit


class _CapturingSession:
    """Stand-in for requests.Session that captures full request URL.

    Replaces ``client.session.request`` so URL construction is exercised
    end-to-end. Returns the canned response so service code can
    ``.model_validate(...)`` whatever comes back.
    """

    def __init__(self, response_payload: Any) -> None:
        self.captured_urls: list[str] = []
        self.captured_methods: list[str] = []
        self._payload = response_payload

    def __call__(self, method: str, url: str, **_kwargs: Any) -> Any:
        self.captured_methods.append(method)
        self.captured_urls.append(url)
        return _Stub(self._payload)


class _Stub:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = ""
        self.headers = {"content-type": "application/json"}

    def json(self) -> Any:
        return self._payload


def _client_with_capture(
    monkeypatch: pytest.MonkeyPatch,
    response: Any,
    base_url: str = "https://example.test/api",
) -> tuple[KamiwazaClient, _CapturingSession]:
    """Build a client where session.request is captured; base_url defaults
    to the documented convention (ending in ``/api``)."""
    client = KamiwazaClient(base_url=base_url, api_key="fake-pat", verify=False)
    capture = _CapturingSession(response)
    monkeypatch.setattr(client.session, "request", capture)
    return client, capture


def _last_url(capture: _CapturingSession) -> str:
    assert capture.captured_urls, "No request was captured"
    return capture.captured_urls[-1]


# ---------------------------------------------------------------------------
# federations.py — pair() create + /pair drive
# ---------------------------------------------------------------------------


def test_federations_pair_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``client.federations.pair("ORION", "initiator", ...)`` must hit
    ``https://host/api/cluster/federations`` — NOT
    ``https://host/api/api/cluster/federations``."""
    client, capture = _client_with_capture(
        monkeypatch,
        {
            "id": "fed-id",
            "status": "PAIRED",
            "remote_cluster_name": "ORION",
            "remote_ips": [{"ip": "10.0.0.1", "primary": True}],
        },
    )
    client.federations.pair(
        name="ORION", role="initiator", remote_url="https://orion.example.com"
    )

    # First call creates the federation; second drives /pair.
    assert capture.captured_urls[0] == (
        "https://example.test/api/cluster/federations"
    ), f"Got: {capture.captured_urls[0]}"
    assert (
        capture.captured_urls[1]
        == "https://example.test/api/cluster/federations/fed-id/pair"
    ), f"Got: {capture.captured_urls[1]}"


def test_federations_users_add_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``client.federations["ORION"].users.add(...)`` resolves federation
    by name (GET) then POSTs the user — both at clean /api/cluster/... paths."""
    client, capture = _client_with_capture(
        monkeypatch,
        # First request: resolve name → id. Second: create user.
        # The capture session returns the same payload on every call, so
        # we use a payload shape that satisfies both call sites.
        {
            "items": [{"id": "fed-id", "remote_cluster_name": "ORION"}],
            "federation_id": "fed-id",
            "external_id": "user@uuid",
            "auto_provisioned": False,
        },
    )
    client.federations["ORION"].users.add(external_id="user@uuid")
    assert any(
        u == "https://example.test/api/cluster/federations"
        for u in capture.captured_urls
    )
    assert any(
        u == "https://example.test/api/cluster/federations/fed-id/users"
        for u in capture.captured_urls
    )


def test_federations_proxy_probe_preserves_inner_api_for_mesh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mesh proxy paths preserve the INNER ``/api/cluster/...`` segment —
    that's the path the remote cluster sees after mesh routing. Only the
    OUTER ``/api`` (gateway prefix) is implicit in base_url."""
    client, capture = _client_with_capture(
        monkeypatch,
        {"system_type": "x86_64", "os": "linux"},
    )
    client.federations["ORION"].probe()
    assert _last_url(capture) == (
        "https://example.test/api/mesh/ORION/api/cluster/cluster_capabilities"
    ), f"Got: {_last_url(capture)}"


# ---------------------------------------------------------------------------
# jobs_federation.py
# ---------------------------------------------------------------------------


def test_jobs_run_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(
        monkeypatch, {"job_id": "j1", "status": "SUCCEEDED"}
    )
    client.jobs.run(entrypoint="echo hi")
    assert _last_url(capture) == "https://example.test/api/cluster/jobs/run"


def test_jobs_cancel_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(monkeypatch, {"job_id": "j1"})
    client.jobs.cancel("j1")
    assert _last_url(capture) == "https://example.test/api/cluster/jobs/j1/cancel"


# ---------------------------------------------------------------------------
# cluster_federation.py
# ---------------------------------------------------------------------------


def test_cluster_capabilities_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(
        monkeypatch, {"system_type": "x86_64", "os": "linux"}
    )
    client.cluster.capabilities()
    assert _last_url(capture) == (
        "https://example.test/api/cluster/cluster_capabilities"
    )


def test_cluster_set_execution_gate_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(
        monkeypatch,
        {"type": "X.Y", "config": {}, "gate_name": "n"},
    )
    client.cluster.set_execution_gate(type="X.Y", config={})
    assert _last_url(capture) == "https://example.test/api/cluster/execution-gate"


def test_cluster_declare_attribute_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(
        monkeypatch,
        {
            "name": "clearance",
            "type": "string",
            "state": "declared",
            "declared_at": "2026-05-13T00:00:00Z",
        },
    )
    client.cluster.declare_attribute(name="clearance", type="string")
    assert _last_url(capture) == (
        "https://example.test/api/cluster/attribute-schema/clearance"
    )


# ---------------------------------------------------------------------------
# subjects.py
# ---------------------------------------------------------------------------


def test_subjects_upsert_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(
        monkeypatch,
        {"id": "uuid", "username": "alice", "attributes": {}},
    )
    client.subjects.upsert(username="alice", attributes={})
    assert _last_url(capture) == "https://example.test/api/authz/subjects/alice"


def test_subjects_grants_create_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(
        monkeypatch,
        {"object_namespace": "cluster", "object_id": "x", "relation": "viewer"},
    )
    client.subjects.grants("alice").create(
        object_namespace="cluster", object_id="x", relation="viewer"
    )
    assert _last_url(capture) == (
        "https://example.test/api/authz/subjects/alice/grants"
    )


# ---------------------------------------------------------------------------
# datasets.py
# ---------------------------------------------------------------------------


def test_datasets_get_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(
        monkeypatch, {"urn": "urn:li:dataset:x", "name": "n", "platform": "p"}
    )
    client.datasets.get("urn:li:dataset:x")
    assert _last_url(capture) == "https://example.test/api/catalog/datasets/by-urn"


def test_datasets_set_gate_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(
        monkeypatch,
        {
            "dataset_urn": "urn",
            "type": "X.Y",
            "config": {},
            "gate_name": "n",
        },
    )
    client.datasets.set_gate("urn", type="X.Y", config={})
    assert _last_url(capture) == "https://example.test/api/catalog/datasets/urn/gate"


# ---------------------------------------------------------------------------
# gates.py
# ---------------------------------------------------------------------------


def test_gates_discover_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(
        monkeypatch,
        {
            "name": "MyGate",
            "kind": "execution",
            "classpath": "x.y",
        },
    )
    client.gates.discover("x.y")
    assert _last_url(capture) == "https://example.test/api/authz/gates/discover"


# ---------------------------------------------------------------------------
# retrieval_federation.py
# ---------------------------------------------------------------------------


def test_retrieval_cancel_url_no_double_api_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, capture = _client_with_capture(monkeypatch, {})
    client.retrieval.cancel("q1")
    assert _last_url(capture) == ("https://example.test/api/retrieval/jobs/q1/cancel")


# ---------------------------------------------------------------------------
# H2 regression: error_for_response dispatch through _raise_for_error_response
# ---------------------------------------------------------------------------


def _error_session(status_code: int, reason: Optional[str]) -> Any:
    """Return a ``session.request`` replacement that yields a structured
    error response with ``detail.reason`` on every call. Closure rather
    than callable class — sidesteps unused-arg lint flags from the
    request(method, url, **kwargs) signature we have to accept but
    don't use."""
    stub = _ErrorStub(status_code, reason)

    def _call(*_args: Any, **_kwargs: Any) -> Any:
        return stub

    return _call


class _ErrorStub:
    def __init__(self, status_code: int, reason: Optional[str]) -> None:
        self.status_code = status_code
        self.text = '{"detail":...}'
        self.headers = {"content-type": "application/json"}
        self._reason = reason

    def json(self) -> Any:
        if self._reason is None:
            return {"detail": "Forbidden"}
        return {"detail": {"reason": self._reason}}


def test_brokered_user_not_allowlisted_routes_through_typed_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2 (PR feedback): a 403 with
    ``detail.reason == "brokered_user_not_allowlisted"`` must surface as
    ``BrokeredUserNotAllowlistedError`` (typed), not the generic ``APIError``."""
    from kamiwaza_sdk.exceptions import BrokeredUserNotAllowlistedError

    client = KamiwazaClient(
        base_url="https://example.test/api", api_key="fake", verify=False
    )
    monkeypatch.setattr(
        client.session,
        "request",
        _error_session(403, "brokered_user_not_allowlisted"),
    )
    with pytest.raises(BrokeredUserNotAllowlistedError):
        client.subjects.upsert(username="alice", attributes={})


def test_native_realm_required_routes_through_typed_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2 regression: 403 + endpoint_requires_native_realm → typed subclass."""
    from kamiwaza_sdk.exceptions import NativeRealmRequiredError

    client = KamiwazaClient(
        base_url="https://example.test/api", api_key="fake", verify=False
    )
    monkeypatch.setattr(
        client.session,
        "request",
        _error_session(403, "endpoint_requires_native_realm"),
    )
    with pytest.raises(NativeRealmRequiredError):
        client.cluster.set_execution_gate(type="X.Y", config={})


def test_generic_403_still_raises_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2 negative case: a 403 with no recognized reason falls back to APIError —
    no synthesized typed subclass for shapes the dispatch table doesn't own."""
    from kamiwaza_sdk.exceptions import APIError, BrokeredUserNotAllowlistedError

    client = KamiwazaClient(
        base_url="https://example.test/api", api_key="fake", verify=False
    )
    monkeypatch.setattr(client.session, "request", _error_session(403, None))
    with pytest.raises(APIError) as exc_info:
        client.subjects.upsert(username="alice", attributes={})
    assert not isinstance(exc_info.value, BrokeredUserNotAllowlistedError)


# ---------------------------------------------------------------------------
# H1 regression: pair(name, role, remote_url) positional still works
# ---------------------------------------------------------------------------


def test_pair_accepts_remote_url_positional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H1 (PR feedback): legacy M1-M3 callers do
    ``pair("ORION", "initiator", "https://orion.example.com", remote_admin_token=...)``
    with ``remote_url`` as the 3rd positional. The migration must not
    regress that surface."""
    client, capture = _client_with_capture(
        monkeypatch,
        {
            "id": "fed-id",
            "status": "PAIRED",
            "remote_cluster_name": "ORION",
            "remote_ips": [{"ip": "orion.example.com", "primary": True}],
        },
    )
    # All positional — exactly the pre-M3.2 legacy call shape.
    client.federations.pair(
        "ORION", "initiator", "https://orion.example.com", remote_admin_token="pat"
    )
    assert capture.captured_urls[0] == "https://example.test/api/cluster/federations"
