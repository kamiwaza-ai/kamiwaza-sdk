"""The connector server SPI (``kamiwaza_sdk.connectors``).

A connector pod subclasses :class:`ConnectorProvider`, declares a
:class:`ConfigSchema`, and serves it with :func:`create_connector_app`. These
cover the contract connector repos depend on (``kamiwaza-sdk[connector]``): the
served endpoints (manifest / execute / verify / lifespan), descriptor
``to_manifest``/``from_manifest`` round-trips, config validation, and the
proxy/token schema bounds.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from kamiwaza_sdk.connectors import (
    ConfigField,
    ConfigSchema,
    ConfigType,
    ConnectorIdentityProxyRequest,
    ConnectorMintRequest,
    ConnectorMintResponse,
    ConnectorProvider,
    ConnectorProxyRequest,
    ConnectorProxyResponse,
    ConnectorSpec,
    ConstraintDescriptor,
    DeploymentDescriptor,
    InvalidConfigException,
    OAuthDescriptor,
    OpResult,
    PerUserOAuth,
    ServiceToken,
    SurfaceDescriptor,
    WhoamiRequest,
    auth_model_from_kind,
    create_connector_app,
    validate_icon,
)
from pydantic import ValidationError


class _DummyProvider(ConnectorProvider):
    @property
    def connector_type(self) -> str:
        return "dummy"

    def config_schema(self) -> ConfigSchema:
        return ConfigSchema(
            fields=(
                ConfigField(name="client_id", type=ConfigType.STRING),
                ConfigField(name="client_secret", type=ConfigType.STRING, secret=True),
            )
        )


class _StubDispatcher:
    """Async dispatcher stub: returns a canned execute/verify result or raises."""

    def __init__(self, *, execute_result=None, execute_exc=None, verify_result=None):
        self._execute_result = execute_result
        self._execute_exc = execute_exc
        self._verify_result = verify_result
        self.closed = False

    async def execute(self, op, *, subject_token, params):
        if self._execute_exc is not None:
            raise self._execute_exc
        return self._execute_result

    async def verify(self, *, subject_token):
        return self._verify_result

    async def aclose(self):
        self.closed = True


class _BoomError(Exception):
    pass


def _app(dispatcher, *, build=None):
    return create_connector_app(
        title="kamiwaza-connector-dummy",
        provider=_DummyProvider(),
        build_dispatcher=build or (lambda: dispatcher),
        error_type=_BoomError,
        classify_error=lambda exc: (429, "rate_limited", 503),
        dispatcher=dispatcher,
    )


# --- manifest + config schema ------------------------------------------------


def test_to_manifest_carries_identity_and_config_schema() -> None:
    manifest = _DummyProvider().to_manifest()
    assert manifest["connector_type"] == "dummy"
    cfg = manifest["config_schema"]
    assert cfg["type"] == "object"
    assert set(cfg["required"]) == {"client_id", "client_secret"}
    assert cfg["properties"]["client_secret"]["writeOnly"] is True


def test_validate_config_rejects_missing_required() -> None:
    provider = _DummyProvider()
    provider.validate_config({"client_id": "abc", "client_secret": "shh"})  # ok
    with pytest.raises(InvalidConfigException):
        provider.validate_config({"client_id": "abc"})


def test_config_schema_rejects_bool_for_int() -> None:
    schema = ConfigSchema(fields=(ConfigField(name="n", type=ConfigType.INTEGER),))
    schema.validate({"n": 5})  # ok
    with pytest.raises(InvalidConfigException):
        schema.validate({"n": True})  # bool is not an int here


def test_config_schema_json_roundtrip_is_idempotent() -> None:
    schema = ConfigSchema(
        fields=(
            ConfigField(name="client_id", type=ConfigType.STRING),
            ConfigField(name="client_secret", type=ConfigType.STRING, secret=True),
            ConfigField(name="port", type=ConfigType.INTEGER, required=False),
        )
    )
    once = schema.to_json_schema()
    twice = ConfigSchema.from_json_schema(once).to_json_schema()
    assert once == twice
    assert ConfigSchema.from_json_schema(once).secret_fields() == ("client_secret",)


# --- served endpoints --------------------------------------------------------


def test_manifest_endpoint() -> None:
    with TestClient(_app(_StubDispatcher())) as client:
        resp = client.get("/manifest")
    assert resp.status_code == 200
    assert resp.json()["connector_type"] == "dummy"


def test_healthz() -> None:
    with TestClient(_app(_StubDispatcher())) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_execute_plain_body() -> None:
    with TestClient(_app(_StubDispatcher(execute_result={"items": [1, 2]}))) as client:
        resp = client.post("/v1/execute", json={"op": "list", "params": {}})
    assert resp.status_code == 200
    assert resp.json() == {"body": {"items": [1, 2]}}


def test_execute_opresult_carries_state_and_session() -> None:
    result = OpResult(body={"items": []}, state="cursor-2", session="sess-9")
    with TestClient(_app(_StubDispatcher(execute_result=result))) as client:
        resp = client.post("/v1/execute", json={"op": "list"})
    assert resp.json() == {
        "body": {"items": []},
        "state": "cursor-2",
        "session": "sess-9",
    }


def test_execute_classifies_connector_error() -> None:
    disp = _StubDispatcher(execute_exc=_BoomError("upstream throttled"))
    with TestClient(_app(disp)) as client:
        resp = client.post("/v1/execute", json={"op": "list"})
    assert resp.status_code == 429
    assert resp.json()["error"] == {
        "kind": "rate_limited",
        "message": "upstream throttled",
        "upstream_status": 503,
    }


def test_verify() -> None:
    with TestClient(_app(_StubDispatcher(verify_result={"ok": True}))) as client:
        resp = client.post("/v1/verify", json={})
    assert resp.json() == {"ok": True}


# --- /v1/whoami: optional identity probe per dispatcher ---------------------


class _WhoamiDispatcher(_StubDispatcher):
    """Dispatcher that implements whoami; record the token it received."""

    def __init__(self, identity: dict) -> None:
        super().__init__(verify_result={"ok": True})
        self._identity = identity
        self.whoami_tokens: list[str] = []

    async def whoami(self, *, access_token: str) -> dict:
        self.whoami_tokens.append(access_token)
        return self._identity


def test_whoami_routes_to_dispatcher_when_implemented() -> None:
    """A dispatcher with whoami gets the identity call routed through."""
    dispatcher = _WhoamiDispatcher({"email": "user@example.com", "name": "User"})
    with TestClient(_app(dispatcher)) as client:
        resp = client.post("/v1/whoami", json={"access_token": "hs-at"})
    assert resp.status_code == 200
    assert resp.json() == {"email": "user@example.com", "name": "User"}
    assert dispatcher.whoami_tokens == ["hs-at"]


def test_whoami_returns_404_when_dispatcher_does_not_implement_it() -> None:
    """An OIDC-only connector (or one shipped before whoami) responds 404.

    Core's connect fallback treats this the same as an empty payload — the
    UI keeps its documented "Unknown account" label until the connector
    ships the probe.
    """
    with TestClient(_app(_StubDispatcher())) as client:
        # Base _StubDispatcher has no whoami method.
        resp = client.post("/v1/whoami", json={"access_token": "tok"})
    assert resp.status_code == 404
    assert resp.json()["error"]["kind"] == "whoami_not_implemented"


def test_whoami_rejects_empty_access_token() -> None:
    """Pydantic validation: empty access_token never reaches the dispatcher."""
    dispatcher = _WhoamiDispatcher({})
    with TestClient(_app(dispatcher)) as client:
        resp = client.post("/v1/whoami", json={"access_token": ""})
    assert resp.status_code == 422
    assert dispatcher.whoami_tokens == []


def test_whoami_classifies_connector_error() -> None:
    """Connector errors raised inside whoami flow through the classifier."""

    class _ExplodingWhoami(_StubDispatcher):
        async def whoami(self, *, access_token: str) -> dict:
            raise _BoomError("upstream blew up")

    with TestClient(_app(_ExplodingWhoami())) as client:
        resp = client.post("/v1/whoami", json={"access_token": "tok"})
    assert resp.status_code == 429  # _app's classify_error maps _BoomError -> 429
    assert resp.json()["error"]["kind"] == "rate_limited"


def test_whoami_returns_404_when_handler_not_callable() -> None:
    """A non-callable ``whoami`` attribute folds into the 404 contract, not 500."""

    class _NonCallableWhoami(_StubDispatcher):
        whoami = "not a method"  # truthy but not callable

    with TestClient(_app(_NonCallableWhoami())) as client:
        resp = client.post("/v1/whoami", json={"access_token": "tok"})
    assert resp.status_code == 404
    assert resp.json()["error"]["kind"] == "whoami_not_implemented"


def test_whoami_request_token_not_in_repr() -> None:
    """The raw provider access token must not surface in repr()/logs/tracebacks."""
    req = WhoamiRequest(access_token="super-secret-token")
    assert "super-secret-token" not in repr(req)


def test_lifespan_builds_and_closes_owned_dispatcher() -> None:
    built: list[_StubDispatcher] = []

    def build() -> _StubDispatcher:
        disp = _StubDispatcher(verify_result={"ok": True})
        built.append(disp)
        return disp

    # dispatcher=None -> the app owns the one build() returns and must aclose it.
    app = create_connector_app(
        title="kamiwaza-connector-dummy",
        provider=_DummyProvider(),
        build_dispatcher=build,
        error_type=_BoomError,
        classify_error=lambda exc: (500, "internal", None),
        dispatcher=None,
    )
    with TestClient(app) as client:
        assert client.post("/v1/verify", json={}).json() == {"ok": True}
    assert built and built[0].closed is True


# --- descriptor round-trips --------------------------------------------------


def test_connector_spec_manifest_roundtrip() -> None:
    spec = ConnectorSpec(
        connector_type="acme",
        provider_id="acme",
        provider_label="Acme",
        auth_model=ServiceToken(),
        egress_allowlist=("api.acme.test",),
        oauth=OAuthDescriptor(
            authorization_endpoint="https://acme.test/auth",
            token_endpoint="https://acme.test/token",
            scopes=("read", "write"),
            extra_auth_params=(("access_type", "offline"),),
        ),
        deployment=DeploymentDescriptor(
            image_repository="kamiwaza-internal/connectors/acme",
            image_tag="develop",
            port=8080,
        ),
        icon=None,
        surfaces=(
            SurfaceDescriptor(
                name="tickets",
                display_label="Tickets",
                operations=(("list", "tickets.list"),),
                resource_kinds=("ticket",),
                search_supported=True,
                constraints=(
                    ConstraintDescriptor(
                        code="no_search",
                        message="search unsupported",
                        actions=("search",),
                    ),
                ),
            ),
        ),
        constraints=(ConstraintDescriptor(code="c1", message="m1"),),
    )
    assert ConnectorSpec.from_manifest(spec.to_manifest()) == spec


def test_auth_model_from_kind() -> None:
    assert isinstance(auth_model_from_kind("per_user_oauth"), PerUserOAuth)
    assert isinstance(auth_model_from_kind("service_token"), ServiceToken)
    with pytest.raises(ValueError):
        auth_model_from_kind("nope")


# --- validation guards -------------------------------------------------------


def test_validate_icon() -> None:
    validate_icon(None)  # allowed
    validate_icon("data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=")  # allowed
    with pytest.raises(ValueError):
        validate_icon("https://example.test/logo.png")  # not a data: URI
    with pytest.raises(ValueError):
        validate_icon("data:image/png;base64," + "A" * 256_001)  # over cap


def test_mint_request_lease_bounds() -> None:
    ConnectorMintRequest(lease_duration=60)  # min
    ConnectorMintRequest(lease_duration=900)  # max
    for bad in (59, 901):
        with pytest.raises(ValidationError):
            ConnectorMintRequest(lease_duration=bad)
    # the proxy request inherits the same bound
    with pytest.raises(ValidationError):
        ConnectorProxyRequest(url="https://api.acme.test/x", lease_duration=10)


def test_identity_proxy_request_requires_token_and_hides_it() -> None:
    # The pre-connection whoami request carries the provider token itself (no
    # connection to mint from yet); it must be supplied and must not surface in repr.
    with pytest.raises(ValidationError):
        ConnectorIdentityProxyRequest(url="https://api.acme.test/userinfo")
    req = ConnectorIdentityProxyRequest(
        url="https://api.acme.test/userinfo", access_token="prov-secret"
    )
    assert req.response_format == "json"
    assert "prov-secret" not in repr(req)


def test_from_json_schema_required_field_must_be_supplied() -> None:
    # An externally-authored manifest: a property both `required` AND carrying a
    # `default`. `required` wins — from_json_schema must reconstruct it as
    # must-supply so validate({}) rejects the missing field.
    schema = {
        "type": "object",
        "properties": {"client_secret": {"type": "string", "default": "x"}},
        "required": ["client_secret"],
    }
    cfg = ConfigSchema.from_json_schema(schema)
    with pytest.raises(InvalidConfigException):
        cfg.validate({})
    cfg.validate({"client_secret": "abc"})  # ok when supplied


def test_oauth_descriptor_resolved_fills_placeholders() -> None:
    d = OAuthDescriptor(
        authorization_endpoint="https://login.test/{tenant_id}/authorize",
        token_endpoint="https://login.test/{tenant_id}/token",
        revocation_endpoint=None,
    )
    r = d.resolved({"tenant_id": "contoso"})
    assert r.authorization_endpoint == "https://login.test/contoso/authorize"
    assert r.token_endpoint == "https://login.test/contoso/token"
    assert r.revocation_endpoint is None  # None stays None
    # Fixed endpoints (no placeholders) are a no-op.
    fixed = OAuthDescriptor(
        authorization_endpoint="https://accounts.test/auth",
        token_endpoint="https://accounts.test/token",
    )
    assert fixed.resolved({"tenant_id": "x"}) == fixed


def test_missing_scopes_default_is_empty() -> None:
    # The base provider grants access by configuration, not per-surface scopes.
    assert _DummyProvider().missing_scopes("files", {"read"}) == []


def test_lifespan_rebuilds_after_owned_close() -> None:
    built: list[_StubDispatcher] = []

    def build() -> _StubDispatcher:
        disp = _StubDispatcher(verify_result={"ok": True})
        built.append(disp)
        return disp

    app = create_connector_app(
        title="kamiwaza-connector-dummy",
        provider=_DummyProvider(),
        build_dispatcher=build,
        error_type=_BoomError,
        classify_error=lambda exc: (500, "internal", None),
        dispatcher=None,
    )
    # Two lifespan passes: each must build + close its OWN dispatcher, never serve
    # through the previous (closed) one.
    for _ in range(2):
        with TestClient(app) as client:
            assert client.post("/v1/verify", json={}).json() == {"ok": True}
    assert len(built) == 2
    assert all(d.closed for d in built)


def test_lifespan_clears_state_even_if_aclose_raises() -> None:
    # If the owned dispatcher's aclose() raises during shutdown, app.state must
    # still be nulled (the ref is cleared BEFORE close), so a later pass rebuilds.
    class _RaisingClose(_StubDispatcher):
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    disp = _RaisingClose(verify_result={"ok": True})
    app = create_connector_app(
        title="kamiwaza-connector-dummy",
        provider=_DummyProvider(),
        build_dispatcher=lambda: disp,
        error_type=_BoomError,
        classify_error=lambda exc: (500, "internal", None),
        dispatcher=None,
    )
    try:
        with TestClient(app) as client:
            client.post("/v1/verify", json={})
    except RuntimeError:
        pass  # the raising aclose propagates out of shutdown
    assert app.state.dispatcher is None


# --- forward-compat / device-code edge cases ---------------------------------


def test_oauth_device_code_manifest_loads_without_auth_endpoint() -> None:
    # A device_code (RFC 8628) connector ships only token + device endpoints.
    # from_manifest must not KeyError on the missing authorization_endpoint.
    data = {
        "token_endpoint": "https://login.test/token",
        "device_authorization_endpoint": "https://login.test/devicecode",
        "flow": "device_code",
    }
    d = OAuthDescriptor.from_manifest(data)
    assert d.authorization_endpoint is None
    assert d.flow == "device_code"
    assert d.device_authorization_endpoint == "https://login.test/devicecode"
    # resolved() must tolerate the None auth endpoint too.
    assert d.resolved({"tenant_id": "x"}).authorization_endpoint is None
    # and it round-trips inside a full ConnectorSpec.
    spec = ConnectorSpec(
        connector_type="m365",
        provider_id="m365",
        provider_label="Microsoft 365",
        oauth=d,
    )
    assert ConnectorSpec.from_manifest(spec.to_manifest()) == spec


def test_from_json_schema_tolerates_nullable_type_array() -> None:
    # JSON Schema nullable form expresses type as a list (["integer","null"]).
    # from_json_schema must not crash (TypeError: unhashable list) — it picks the
    # non-null type, and a non-string/unknown type falls back to string.
    schema = {
        "type": "object",
        "properties": {
            "port": {"type": ["integer", "null"]},
            "weird": {"type": 123},
        },
    }
    by_name = {f.name: f for f in ConfigSchema.from_json_schema(schema).fields}
    assert by_name["port"].type is ConfigType.INTEGER
    assert by_name["weird"].type is ConfigType.STRING


def test_mint_response_access_token_not_in_repr() -> None:
    resp = ConnectorMintResponse(
        access_token="super-secret-token",
        lease_id="lease-1",
        granted_scopes=[],
        expires_in=1,
        broker_lease_expires_in=1,
    )
    assert "super-secret-token" not in repr(resp)


def test_response_models_retain_extra_fields() -> None:
    # extra="allow": a newer core's added fields survive validate -> dump on an
    # older connector.
    mint = ConnectorMintResponse.model_validate(
        {
            "access_token": "t",
            "lease_id": "l",
            "granted_scopes": [],
            "expires_in": 1,
            "broker_lease_expires_in": 1,
            "new_core_field": "keep-me",
        }
    )
    assert mint.model_dump()["new_core_field"] == "keep-me"
    proxy = ConnectorProxyResponse.model_validate(
        {"status_code": 200, "body": None, "new_core_field": "keep-me"}
    )
    assert proxy.model_dump()["new_core_field"] == "keep-me"


# --- framework logging -------------------------------------------------------


def _capture_connector_logs():
    """Attach a capturing handler to the framework logger (returns messages, detach)."""
    import logging

    logger = logging.getLogger("kamiwaza_sdk.connectors")
    messages: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: messages.append(record.getMessage())  # type: ignore[method-assign]
    logger.addHandler(handler)
    return messages, lambda: logger.removeHandler(handler)


def test_execute_logs_outcome_and_redacts_token_and_values() -> None:
    messages, detach = _capture_connector_logs()
    try:
        with TestClient(_app(_StubDispatcher(execute_result={"ok": 1}))) as client:
            resp = client.post(
                "/v1/execute",
                json={
                    "op": "files.get_content",
                    "subject_token": "SECRET-TOKEN",
                    "params": {
                        "node_id": "NODEVAL",
                        "drive_id": "DRIVEVAL",
                        "mime_type": "application/pdf",
                    },
                },
            )
    finally:
        detach()
    assert resp.status_code == 200
    blob = "\n".join(messages)
    assert "op=files.get_content" in blob and "outcome=ok" in blob
    # param KEYS are logged for debuggability...
    assert "node_id" in blob and "drive_id" in blob and "mime_type" in blob
    # ...but never the acting-subject token or any param VALUE.
    assert "SECRET-TOKEN" not in blob
    assert "application/pdf" not in blob
    assert "NODEVAL" not in blob and "DRIVEVAL" not in blob


def test_execute_error_is_logged_at_warning() -> None:
    messages, detach = _capture_connector_logs()
    try:
        with TestClient(
            _app(_StubDispatcher(execute_exc=_BoomError("kaboom")))
        ) as client:
            resp = client.post("/v1/execute", json={"op": "list", "params": {}})
    finally:
        detach()
    assert resp.status_code == 429
    blob = "\n".join(messages)
    assert "outcome=error" in blob and "kind=rate_limited" in blob
    assert "error_type=_BoomError" in blob
    # The raw exception text is DEBUG-only (can embed upstream fragments), so it
    # must not appear in the default-level (INFO) capture.
    assert "kaboom" not in blob
