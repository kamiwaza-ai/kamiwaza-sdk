from __future__ import annotations

from types import SimpleNamespace

import pytest

from kamiwaza_sdk.exceptions import (
    APIError,
    AuthenticationError,
    BrokeredUserNotAllowlistedError,
    NotFoundError,
)
from kamiwaza_sdk.schemas.kaizen import LLMConfig
from kamiwaza_sdk.services.kaizen import (
    AgentService,
    AmbiguousExtensionError,
    ConversationError,
    ConversationService,
    _agent_error_from_events,
    _has_finish_action,
    _is_serving,
    _is_transient_resolve_error,
    _reply_from_events,
    resolve_base_url,
    wait_for_base_url,
)

pytestmark = pytest.mark.unit

KAIZEN_URL = "https://kamiwaza.test/kaizen"


class DummyClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def _request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.responses[(method, path)]


def test_agent_create_merges_llm_and_targets_kaizen_base_url():
    responses = {("POST", "api/agents"): {"id": "agent-1", "name": "seed-agent"}}
    client = DummyClient(responses)
    service = AgentService(client)

    agent = service.create(
        base_url=KAIZEN_URL,
        name="seed-agent",
        llm=LLMConfig(provider="kamiwaza", model="llama-3", endpoint_path="/dep/abc"),
        description="seeded",
        workroom_id="wr-123",
    )

    assert agent.id == "agent-1"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "api/agents")
    assert kwargs["base_url"] == KAIZEN_URL
    # llm is merged under agent_config; model binding preserved.
    assert kwargs["json"]["agent_config"]["llm"] == {
        "model": "llama-3",
        "provider": "kamiwaza",
        "endpoint_path": "/dep/abc",
    }
    assert kwargs["json"]["description"] == "seeded"
    # Workroom scope rides the header, never the body.
    assert kwargs["headers"] == {"X-Workroom-Id": "wr-123"}
    assert "workroom_id" not in kwargs["json"]


def test_agent_create_accepts_raw_llm_dict_and_no_workroom():
    responses = {("POST", "api/agents"): {"id": "agent-2"}}
    client = DummyClient(responses)
    service = AgentService(client)

    service.create(
        base_url=KAIZEN_URL,
        name="a",
        llm={"model": "gpt-4o", "base_url": "https://vendor.example/v1"},
    )

    _, _, kwargs = client.calls[0]
    assert kwargs["json"]["agent_config"]["llm"]["model"] == "gpt-4o"
    # No workroom_id -> no scoping header.
    assert kwargs["headers"] == {}


def test_conversation_create_builds_body_and_header():
    responses = {
        ("POST", "api/conversations"): {"id": "conv-1", "agent_id": "agent-1"}
    }
    client = DummyClient(responses)
    service = ConversationService(client)

    conv = service.create(
        base_url=KAIZEN_URL,
        agent_id="agent-1",
        title="seed convo",
        workroom_id="wr-123",
    )

    assert conv.id == "conv-1"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "api/conversations")
    assert kwargs["base_url"] == KAIZEN_URL
    assert kwargs["json"] == {
        "agent_id": "agent-1",
        "max_iterations": 500,
        "stuck_detection": True,
        "ephemeral": False,
        "title": "seed convo",
    }
    assert kwargs["headers"] == {"X-Workroom-Id": "wr-123"}


def test_resolve_base_url_reads_extension_endpoint():
    extension = SimpleNamespace(
        endpoints=SimpleNamespace(
            external="https://kamiwaza.test/kaizen/", public_api_url=None
        )
    )
    client = SimpleNamespace(
        extensions=SimpleNamespace(get_extension=lambda name: extension)
    )

    assert resolve_base_url(client) == "https://kamiwaza.test/kaizen"


def test_resolve_base_url_falls_back_to_api_url():
    # Deployment exposes only api_url (no external/public_api_url).
    extension = SimpleNamespace(
        endpoints=SimpleNamespace(
            external=None,
            public_api_url=None,
            api_url="https://kamiwaza.test/kaizen-api/",
        )
    )
    client = SimpleNamespace(
        extensions=SimpleNamespace(get_extension=lambda name: extension)
    )

    assert resolve_base_url(client) == "https://kamiwaza.test/kaizen-api"


def test_resolve_base_url_raises_when_no_endpoint():
    extension = SimpleNamespace(
        endpoints=SimpleNamespace(external=None, public_api_url=None)
    )
    client = SimpleNamespace(
        extensions=SimpleNamespace(get_extension=lambda name: extension)
    )

    with pytest.raises(ValueError, match="no published endpoint"):
        resolve_base_url(client, "kaizen")


def _ext(name, workroom_id, *, external=KAIZEN_URL):
    return SimpleNamespace(
        name=name,
        workroom_id=workroom_id,
        endpoints=SimpleNamespace(external=external, public_api_url=None),
    )


def _client_listing(extensions):
    # Record the workroom_id the resolver scopes the listing with. The platform
    # only lists a workroom's extensions to a caller that sends X-Workroom-Id, so
    # a resolver that drops the scope (the bug this guards) must fail loudly.
    client = SimpleNamespace(seen_workroom_ids=[])

    def list_extensions(workroom_id=None):
        client.seen_workroom_ids.append(workroom_id)
        return list(extensions)

    client.extensions = SimpleNamespace(list_extensions=list_extensions)
    # Backend probe (_is_serving) succeeds by default; tests that exercise the
    # not-serving path supply their own _request.
    client._request = lambda *_a, **_k: {}
    return client


def test_resolve_base_url_matches_workroom_by_base_name_and_id():
    # The operator suffixes the CR name; match on base name + the exact workroom,
    # and ignore other extensions (milvus) and other workrooms' Kaizen.
    client = _client_listing(
        [
            _ext("kaizen-4f8b3ae100000000", "wr-A"),
            _ext("kaizen-9999999900000000", "wr-B"),  # another workroom's Kaizen
            _ext("service-milvus-xyz", "wr-A", external="https://x/milvus"),
        ]
    )

    assert resolve_base_url(client, "kaizen", workroom_id="wr-A") == KAIZEN_URL
    # The listing MUST be scoped to the workroom (X-Workroom-Id); without it the
    # platform returns only global extensions and the match silently misses.
    assert client.seen_workroom_ids == ["wr-A"]


def test_resolve_base_url_workroom_no_match_raises():
    # Kaizen exists, but only in a different workroom — must not be picked.
    client = _client_listing([_ext("kaizen-9999999900000000", "wr-B")])

    with pytest.raises(ValueError, match="No 'kaizen' extension found"):
        resolve_base_url(client, "kaizen", workroom_id="wr-A")


def test_resolve_base_url_workroom_ambiguous_raises():
    # Two Kaizen in the SAME workroom is anomalous — fail loudly, don't guess.
    # AmbiguousExtensionError (not ValueError) so the wait loop won't retry it.
    client = _client_listing(
        [
            _ext("kaizen-aaaaaaaaaaaaaaaa", "wr-A"),
            _ext("kaizen-bbbbbbbbbbbbbbbb", "wr-A"),
        ]
    )

    with pytest.raises(AmbiguousExtensionError, match="Multiple 'kaizen' extensions"):
        resolve_base_url(client, "kaizen", workroom_id="wr-A")


def test_resolve_base_url_workroom_ignores_unsuffixed_exact_name():
    # A per-workroom instance is always the operator's suffixed CR (kaizen-<hash>),
    # so a bare exact-name extension is not matched — a stray one can't shadow the
    # real instance or trip a false ambiguity.
    client = _client_listing(
        [
            _ext("kaizen", "wr-A", external="https://x/bare"),
            _ext("kaizen-4f8b3ae100000000", "wr-A"),
        ]
    )

    assert resolve_base_url(client, "kaizen", workroom_id="wr-A") == KAIZEN_URL


def test_resolve_base_url_does_not_adopt_kaizen_next_instance_by_prefix():
    client = _client_listing(
        [_ext("kaizen-next-4f8b3ae100000000", "wr-A")]
    )

    with pytest.raises(ValueError, match="No 'kaizen' extension found"):
        resolve_base_url(client, "kaizen", workroom_id="wr-A")


def test_resolve_base_url_accepts_non_uuid_workroom_suffix_shape():
    client = _client_listing([_ext("kaizen-wra", "wr-A")])

    assert resolve_base_url(client, "kaizen", workroom_id="wr-A") == KAIZEN_URL


def test_wait_for_base_url_does_not_retry_ambiguity(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))
    client = _client_listing(
        [
            _ext("kaizen-aaaaaaaaaaaaaaaa", "wr-A"),
            _ext("kaizen-bbbbbbbbbbbbbbbb", "wr-A"),
        ]
    )

    # Ambiguity is deterministic — it must propagate immediately, never poll.
    with pytest.raises(AmbiguousExtensionError):
        wait_for_base_url(client, "kaizen", workroom_id="wr-A", poll_interval_seconds=0)
    assert slept == []


def test_wait_for_base_url_caps_sleep_to_remaining(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    # monotonic calls: deadline (0.0 -> deadline 1.0), attempt-1 remaining (0.0),
    # attempt-2 remaining (1.0 -> deadline reached).
    times = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    slept: list = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))
    client = _client_listing([])  # nothing yet -> retryable ValueError

    with pytest.raises(TimeoutError):
        wait_for_base_url(
            client,
            "kaizen",
            workroom_id="wr-A",
            timeout_seconds=1,
            poll_interval_seconds=60,
        )
    # Sleep capped to the 1s remaining, not the 60s poll interval.
    assert slept == [1.0]


def test_wait_for_base_url_workroom_retries_until_listed(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)

    # First poll: workroom has no Kaizen yet. Second poll: it's listed and ready.
    rounds = [[], [_ext("kaizen-4f8b3ae100000000", "wr-A")]]

    def list_extensions(workroom_id=None):
        return rounds.pop(0)

    client = SimpleNamespace(
        extensions=SimpleNamespace(list_extensions=list_extensions),
        _request=lambda *_a, **_k: {},  # backend serves once listed
    )

    url = wait_for_base_url(
        client, "kaizen", workroom_id="wr-A", poll_interval_seconds=0
    )
    assert url == KAIZEN_URL
    assert rounds == []


def test_wait_for_base_url_retries_transient_api_error_from_resolve(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)

    # A freshly-installed box can 403 (or 5xx) on the platform /extensions listing
    # while the workroom's rebac grant / gateway route settle, then succeed. That
    # transient must be retried, not propagated — otherwise resolve-kaizen-url
    # crashes the nightly seed's agent step (observed as a 403 mid-poll).
    rounds = [
        APIError("transient authz", status_code=403),
        [_ext("kaizen-4f8b3ae1", "wr-A")],
    ]

    def list_extensions(workroom_id=None):
        item = rounds.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = SimpleNamespace(
        extensions=SimpleNamespace(list_extensions=list_extensions),
        _request=lambda *_a, **_k: {},  # backend serves once listed
    )

    url = wait_for_base_url(
        client, "kaizen", workroom_id="wr-A", poll_interval_seconds=0
    )
    assert url == KAIZEN_URL
    assert rounds == []


def test_wait_for_base_url_retries_transient_authorization_error(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)

    # The rebac 403 on a fresh box surfaces as an AuthorizationError SUBCLASS
    # (via error_for_response's typed dispatch), which is a sibling of APIError
    # — an `except APIError` alone would let it crash the poll (the observed
    # nightly failure). It must be caught and classified transient by its 403.
    rounds = [
        BrokeredUserNotAllowlistedError("grant not propagated", status_code=403),
        [_ext("kaizen-4f8b3ae1", "wr-A")],
    ]

    def list_extensions(workroom_id=None):
        item = rounds.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = SimpleNamespace(
        extensions=SimpleNamespace(list_extensions=list_extensions),
        _request=lambda *_a, **_k: {},
    )

    url = wait_for_base_url(
        client, "kaizen", workroom_id="wr-A", poll_interval_seconds=0
    )
    assert url == KAIZEN_URL
    assert rounds == []


def test_wait_for_base_url_retries_transient_5xx_from_resolve(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)

    # A gateway 503 while the upstream warms must ride the same loop path as
    # the 403 case (not just the pure classifier).
    rounds = [
        APIError("no healthy upstream", status_code=503),
        [_ext("kaizen-4f8b3ae1", "wr-A")],
    ]

    def list_extensions(workroom_id=None):
        item = rounds.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = SimpleNamespace(
        extensions=SimpleNamespace(list_extensions=list_extensions),
        _request=lambda *_a, **_k: {},
    )

    url = wait_for_base_url(
        client, "kaizen", workroom_id="wr-A", poll_interval_seconds=0
    )
    assert url == KAIZEN_URL
    assert rounds == []


def test_is_transient_resolve_error_accepts_authorization_error():
    # The classifier must work on AuthorizationError subclasses, not just
    # APIError — both carry status_code from the response boundary.
    err = BrokeredUserNotAllowlistedError("not allowlisted yet", status_code=403)
    assert _is_transient_resolve_error(err, workroom_scoped=True) is True


def test_wait_for_base_url_does_not_retry_unscoped_403(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))

    # Without a workroom scope there is no rebac grant to wait for — a 403 on
    # the get_extension path is a genuine permission denial. It must surface
    # immediately instead of burning the whole timeout into an opaque
    # TimeoutError that buries the authorization failure.
    def get_extension(_name):
        raise APIError("forbidden", status_code=403)

    client = SimpleNamespace(
        extensions=SimpleNamespace(get_extension=get_extension),
        _request=lambda *_a, **_k: {},
    )

    with pytest.raises(APIError):
        wait_for_base_url(client, "kaizen", poll_interval_seconds=0)
    assert slept == []


def test_wait_for_base_url_does_not_retry_non_transient_api_error(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))

    # A genuine bad request (400) is not a startup blip — surface it immediately
    # rather than burning the whole timeout polling a request that can't succeed.
    # (400 is used rather than 401 because the real client intercepts 401 in its
    # token-refresh path and raises AuthenticationError, never a bare APIError.)
    def list_extensions(workroom_id=None):
        raise APIError("bad request", status_code=400)

    client = SimpleNamespace(
        extensions=SimpleNamespace(list_extensions=list_extensions),
        _request=lambda *_a, **_k: {},
    )

    with pytest.raises(APIError):
        wait_for_base_url(client, "kaizen", workroom_id="wr-A", poll_interval_seconds=0)
    assert slept == []


@pytest.mark.parametrize(
    "status,workroom_scoped,expected",
    [
        (None, True, True),  # transport error before any response — settling
        (None, False, True),  # ...regardless of scope
        (500, True, True),  # any 5xx — gateway/upstream not ready
        (500, False, True),  # ...regardless of scope
        (503, True, True),  # no healthy upstream while backend comes up
        (599, True, True),  # upper 5xx bound
        (403, True, True),  # workroom rebac grant not applied yet on a fresh box
        (403, False, False),  # unscoped 403 = real permission denial — surface it
        (429, True, True),  # rate limited
        (429, False, True),  # ...regardless of scope
        (400, True, False),  # bad request — can't clear on its own
        (401, True, False),  # auth failure — surface immediately
        (404, True, False),  # not found — handled separately, not transient here
        (200, True, False),  # a non-error status is never transient
    ],
)
def test_is_transient_resolve_error_classifies_statuses(
    status, workroom_scoped, expected
):
    # Table-driven check of the pure classifier so every retryable/terminal
    # status is pinned independently of the wait_for_base_url poll loop.
    assert (
        _is_transient_resolve_error(
            APIError("boom", status_code=status), workroom_scoped=workroom_scoped
        )
        is expected
    )


def test_wait_for_base_url_returns_when_ready():
    extension = SimpleNamespace(
        endpoints=SimpleNamespace(external=KAIZEN_URL, public_api_url=None)
    )
    client = SimpleNamespace(
        extensions=SimpleNamespace(get_extension=lambda name: extension),
        _request=lambda *_a, **_k: {},  # backend serves
    )

    assert wait_for_base_url(client, "kaizen-4f8b3ae1") == KAIZEN_URL


def test_wait_for_base_url_retries_past_transient_states(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)

    ready = SimpleNamespace(
        endpoints=SimpleNamespace(external=KAIZEN_URL, public_api_url=None)
    )
    unpublished = SimpleNamespace(
        endpoints=SimpleNamespace(external=None, public_api_url=None)
    )
    # CR not visible yet (404), then present but no endpoint, then ready.
    outcomes = [NotFoundError("nf"), unpublished, ready]

    def get_extension(_name):
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = SimpleNamespace(
        extensions=SimpleNamespace(get_extension=get_extension),
        _request=lambda *_a, **_k: {},  # backend serves once published
    )

    assert wait_for_base_url(client, "kaizen", poll_interval_seconds=0) == KAIZEN_URL
    assert outcomes == []


def test_wait_for_base_url_times_out():
    unpublished = SimpleNamespace(
        endpoints=SimpleNamespace(external=None, public_api_url=None)
    )
    client = SimpleNamespace(
        extensions=SimpleNamespace(get_extension=lambda name: unpublished)
    )

    # timeout 0: the deadline is already past after the first failed attempt.
    with pytest.raises(TimeoutError, match="not serving"):
        wait_for_base_url(client, "kaizen", timeout_seconds=0)


# --- backend-serving probe -------------------------------------------------


def _serving_client(get_request):
    """A client whose _request delegates to get_request (probe behavior)."""
    extension = SimpleNamespace(
        endpoints=SimpleNamespace(external=KAIZEN_URL, public_api_url=None)
    )
    return SimpleNamespace(
        extensions=SimpleNamespace(get_extension=lambda name: extension),
        _request=get_request,
    )


def test_is_serving_true_on_success():
    calls: list = []

    def ok(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"agents": []}

    client = SimpleNamespace(_request=ok)
    assert _is_serving(client, KAIZEN_URL, workroom_id="wr-A") is True
    # Probes the agents endpoint against the resolved base_url, workroom-scoped.
    method, path, kwargs = calls[0]
    assert (method, path) == ("GET", "api/agents")
    assert kwargs["base_url"] == KAIZEN_URL
    assert kwargs["headers"] == {"X-Workroom-Id": "wr-A"}


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_is_serving_false_on_5xx(status):
    # Any 5xx means the gateway/backend isn't ready (no healthy upstream during
    # pod startup is 503, but envoy can also emit 502/504 mid-startup).
    def server_error(*_a, **_k):
        raise APIError("server error", status_code=status)

    client = SimpleNamespace(_request=server_error)
    assert _is_serving(client, KAIZEN_URL, workroom_id=None) is False


def test_is_serving_false_on_connection_error():
    # A transport failure surfaces as APIError with no status_code — the route
    # exists but nothing is answering yet, so keep polling.
    def refused(*_a, **_k):
        raise APIError("An error occurred while making the request: refused")

    client = SimpleNamespace(_request=refused)
    assert _is_serving(client, KAIZEN_URL, workroom_id=None) is False


def test_is_serving_true_on_4xx():
    # A 4xx means the backend answered — it's up, just rejecting this probe.
    def not_found(*_a, **_k):
        raise APIError("not found", status_code=404)

    client = SimpleNamespace(_request=not_found)
    assert _is_serving(client, KAIZEN_URL, workroom_id=None) is True


def test_is_serving_true_on_structured_error():
    # Non-APIError KamiwazaError subclasses (auth/validation) prove a response.
    def unauthorized(*_a, **_k):
        raise AuthenticationError("nope")

    client = SimpleNamespace(_request=unauthorized)
    assert _is_serving(client, KAIZEN_URL, workroom_id=None) is True


def test_wait_for_base_url_polls_past_503_until_serving(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)

    # Ingress is published immediately, but the backend 503s twice (pod still
    # starting) before it serves. wait must not return the URL until it serves.
    outcomes = [
        APIError("no healthy upstream", status_code=503),
        APIError("no healthy upstream", status_code=503),
        {"agents": []},
    ]

    def probe(*_a, **_k):
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = _serving_client(probe)
    assert wait_for_base_url(client, "kaizen", poll_interval_seconds=0) == KAIZEN_URL
    assert outcomes == []


def test_wait_for_base_url_public_probes_ingress_not_offhost_url():
    # public=True returns the (possibly off-host) public URL, but the readiness
    # probe must ride the same-host ingress URL — the credentialed client refuses
    # off-host base_urls, so probing the public URL would never resolve.
    ingress = "https://kamiwaza.test/runtime/apps/kaizen-4f8b3ae1"
    public = "https://public.example/kaizen"
    extension = SimpleNamespace(
        endpoints=SimpleNamespace(external=ingress, public_api_url=public)
    )
    probed: list = []

    def record(method, path, **kwargs):
        probed.append(kwargs.get("base_url"))
        return {"agents": []}

    client = SimpleNamespace(
        extensions=SimpleNamespace(get_extension=lambda name: extension),
        _request=record,
    )

    assert wait_for_base_url(client, "kaizen-4f8b3ae1", public=True) == public
    # Probe hit the ingress endpoint, never the off-host public URL.
    assert probed == [ingress]


def test_wait_for_base_url_times_out_when_published_but_never_serving(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)

    # Ingress resolves but the backend 503s forever — the timeout message must
    # reflect "not serving", not "not resolvable".
    def always_503(*_a, **_k):
        raise APIError("no healthy upstream", status_code=503)

    client = _serving_client(always_503)
    with pytest.raises(TimeoutError, match="not serving yet"):
        wait_for_base_url(client, "kaizen", timeout_seconds=0)


def test_agent_list_unwraps_agents_envelope():
    responses = {("GET", "api/agents"): {"agents": [{"id": "agent-1", "name": "a"}]}}
    client = DummyClient(responses)
    service = AgentService(client)

    agents = service.list(base_url=KAIZEN_URL, workroom_id="wr-1")

    assert [a.id for a in agents] == ["agent-1"]
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("GET", "api/agents")
    assert kwargs["headers"] == {"X-Workroom-Id": "wr-1"}


def test_agent_bind_skill_posts_to_managed_endpoint():
    responses = {("POST", "api/agents/agent-1/skill-bindings"): {"id": "agent-1"}}
    client = DummyClient(responses)
    service = AgentService(client)

    service.bind_skill("agent-1", "skill-9", base_url=KAIZEN_URL, workroom_id="wr-1")

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "api/agents/agent-1/skill-bindings")
    assert kwargs["base_url"] == KAIZEN_URL
    assert kwargs["json"] == {"skill_id": "skill-9"}
    assert kwargs["headers"] == {"X-Workroom-Id": "wr-1"}


def test_conversation_send_message_enqueues_without_json_response():
    responses = {("POST", "api/conversations/conv-1/messages"): None}
    client = DummyClient(responses)
    service = ConversationService(client)

    result = service.send_message(
        "conv-1", "hello", base_url=KAIZEN_URL, workroom_id="wr-1"
    )

    assert result is None
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "api/conversations/conv-1/messages")
    assert kwargs["json"] == {"message": "hello"}
    assert kwargs["expect_json"] is False
    assert kwargs["headers"] == {"X-Workroom-Id": "wr-1"}


def test_conversation_run_triggers_processing():
    responses = {("POST", "api/conversations/conv-1/run"): None}
    client = DummyClient(responses)
    service = ConversationService(client)

    service.run("conv-1", base_url=KAIZEN_URL)

    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "api/conversations/conv-1/run")
    assert kwargs["expect_json"] is False
    assert kwargs["headers"] == {}


def test_conversation_get_events_passes_pagination():
    responses = {("GET", "api/conversations/conv-1/events"): {"events": [], "total": 0}}
    client = DummyClient(responses)
    service = ConversationService(client)

    out = service.get_events(
        "conv-1", base_url=KAIZEN_URL, workroom_id="wr-1", offset=5, limit=50
    )

    assert out == {"events": [], "total": 0}
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("GET", "api/conversations/conv-1/events")
    assert kwargs["params"] == {"offset": 5, "limit": 50}
    assert kwargs["headers"] == {"X-Workroom-Id": "wr-1"}


def test_conversation_get_returns_status():
    responses = {
        ("GET", "api/conversations/conv-1"): {
            "id": "conv-1",
            "agent_id": "agent-1",
            "execution_status": "running",
            "container_status": "active",
        }
    }
    client = DummyClient(responses)
    service = ConversationService(client)

    conversation = service.get("conv-1", base_url=KAIZEN_URL, workroom_id="wr-1")

    assert conversation.id == "conv-1"
    assert conversation.execution_status == "running"
    assert conversation.container_status == "active"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("GET", "api/conversations/conv-1")
    assert kwargs["headers"] == {"X-Workroom-Id": "wr-1"}


# --- chat() + event helpers ------------------------------------------------


def _message_event(text, role="assistant"):
    return {
        "kind": "MessageEvent",
        "llm_message": {"role": role, "content": [{"type": "text", "text": text}]},
    }


def _error_event(message):
    return {"kind": "AgentErrorEvent", "error": message}


def _finish_event(text):
    # The agent's canonical "done" signal: the `finish` tool, with the reply in
    # action.message (the shape a live Kaizen run emits).
    return {
        "kind": "ActionEvent",
        "tool_name": "finish",
        "action": {"kind": "FinishAction", "message": text},
    }


class ChatClient:
    """Scripts send/run/poll for ConversationService.chat tests.

    The limit=1 baseline read returns ``baseline_total``; subsequent polling
    reads pop from ``polls`` (the last entry repeats once exhausted, so a
    never-answers run keeps returning the same empty list).
    """

    def __init__(
        self,
        polls,
        baseline_total=0,
        execution_statuses=None,
        container_statuses=None,
    ):
        self.polls = list(polls)
        self.baseline_total = baseline_total
        self.execution_statuses = list(execution_statuses or ["running"])
        self.container_statuses = list(container_statuses or ["active"])
        self.calls: list[tuple[str, str, dict]] = []

    def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("/events"):
            if kwargs.get("params", {}).get("limit") == 1:
                return {"total": self.baseline_total, "events": []}
            events = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
            return {"events": events}
        if method == "GET" and path.startswith("api/conversations/"):
            execution_status = (
                self.execution_statuses.pop(0)
                if len(self.execution_statuses) > 1
                else self.execution_statuses[0]
            )
            container_status = (
                self.container_statuses.pop(0)
                if len(self.container_statuses) > 1
                else self.container_statuses[0]
            )
            return {
                "id": "conv-1",
                "agent_id": "agent-1",
                "execution_status": execution_status,
                "container_status": container_status,
            }
        return None  # messages / run -> 204


def test_reply_from_events_reads_finish_action_message():
    # The common happy path: the reply arrives via the `finish` tool with no
    # assistant MessageEvent, so a MessageEvent-only reader would miss it.
    events = [
        {"kind": "SystemPromptEvent"},
        _message_event("hi", role="user"),
        _finish_event("Hello! I'm Kaizen."),
    ]
    assert _reply_from_events(events) == "Hello! I'm Kaizen."


def test_reply_from_events_returns_latest_assistant_text():
    events = [
        _message_event("hi", role="user"),
        {"kind": "ActionEvent", "tool_name": "bash"},
        _message_event("partial"),
        _message_event("final answer"),
    ]
    assert _reply_from_events(events) == "final answer"


def test_reply_from_events_ignores_assistant_tool_call_text():
    events = [
        {
            "kind": "MessageEvent",
            "tool_calls": [{"id": "call-1"}],
            "llm_message": {
                "role": "assistant",
                "tool_calls": None,
                "content": [{"type": "text", "text": "Let me search..."}],
            },
        }
    ]
    assert _reply_from_events(events) is None


def test_reply_from_events_joins_text_blocks_and_ignores_images():
    events = [
        {
            "kind": "MessageEvent",
            "llm_message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "image_url", "image_url": {"url": "x"}},
                    {"type": "text", "text": "b"},
                ],
            },
        }
    ]
    assert _reply_from_events(events) == "ab"


def test_reply_from_events_none_for_empty_user_only_or_blank():
    assert _reply_from_events([]) is None
    assert _reply_from_events([_message_event("hi", role="user")]) is None
    assert _reply_from_events([_message_event("   ")]) is None
    # A non-finish action (e.g. a tool call) is not a reply on its own.
    assert _reply_from_events([{"kind": "ActionEvent", "tool_name": "bash"}]) is None


def test_agent_error_from_events_returns_message_or_none():
    assert _agent_error_from_events([_error_event("boom")]) == "boom"
    assert _agent_error_from_events([{"kind": "AgentErrorEvent"}]) == "agent error"
    assert _agent_error_from_events([_message_event("ok")]) is None
    # Defensively accept the alternate error-event kind + its `message` field.
    assert (
        _agent_error_from_events(
            [{"kind": "ConversationErrorEvent", "message": "nope"}]
        )
        == "nope"
    )


def test_chat_sends_message_runs_and_returns_reply(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    # Reply arrives via the finish tool, as it does on a live cluster.
    client = ChatClient(polls=[[_finish_event("Hi, I'm Kaizen.")]])
    service = ConversationService(client)

    reply = service.chat("conv-1", "hello", base_url=KAIZEN_URL, workroom_id="wr-1")

    assert reply == "Hi, I'm Kaizen."
    paths = [(m, p) for m, p, _ in client.calls]
    send_i = paths.index(("POST", "api/conversations/conv-1/messages"))
    run_i = paths.index(("POST", "api/conversations/conv-1/run"))
    # Order: send the message, then run, then read events for the reply.
    assert send_i < run_i < len(paths) - 1
    # Workroom scope rides the header on every leg.
    assert client.calls[send_i][2]["headers"] == {"X-Workroom-Id": "wr-1"}


def test_chat_polls_until_reply_appears(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))
    # First poll: nothing yet. Second poll: the agent finished with the reply.
    client = ChatClient(polls=[[], [_finish_event("done")]])
    service = ConversationService(client)

    reply = service.chat("conv-1", "hi", base_url=KAIZEN_URL, poll_interval_seconds=0)

    assert reply == "done"
    assert slept == [0]  # slept once between the two polls


def test_has_finish_action_detects_terminal_finish():
    assert _has_finish_action([_finish_event("done")]) is True
    assert _has_finish_action([_finish_event("")]) is True  # empty but terminal
    assert _has_finish_action([{"kind": "ActionEvent", "tool_name": "bash"}]) is False
    assert _has_finish_action([_message_event("hi")]) is False


def test_chat_returns_none_when_agent_finishes_empty(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))
    # Agent finishes with an empty message: terminal, so return immediately
    # rather than burning the timeout. cmd_chat faults the empty reply.
    client = ChatClient(polls=[[_finish_event("")]])
    service = ConversationService(client)

    assert service.chat("conv-1", "hi", base_url=KAIZEN_URL) is None
    assert slept == []  # returned on the first poll, never waited


def test_chat_zero_poll_interval_does_not_raise(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))
    # First poll empty, second has the reply; zero is valid and sleeps without
    # raising out of the wait loop.
    client = ChatClient(polls=[[], [_finish_event("done")]])
    service = ConversationService(client)

    assert (
        service.chat("conv-1", "hi", base_url=KAIZEN_URL, poll_interval_seconds=0)
        == "done"
    )
    assert slept == [0.0]


@pytest.mark.parametrize("poll_interval", [-1.0, float("nan"), float("inf")])
def test_chat_rejects_invalid_poll_interval_without_side_effects(poll_interval):
    client = ChatClient(polls=[[]])
    service = ConversationService(client)

    with pytest.raises(ValueError, match="poll_interval_seconds"):
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=0,
            poll_interval_seconds=poll_interval,
        )

    assert client.calls == []


def test_chat_ignores_interim_assistant_text_before_finish(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    # First poll: only interim assistant narration, no finish yet — must NOT be
    # returned. Second poll: the terminal finish carries the real answer.
    client = ChatClient(
        polls=[[_message_event("Let me think…")], [_finish_event("real answer")]]
    )
    service = ConversationService(client)

    assert (
        service.chat("conv-1", "hi", base_url=KAIZEN_URL, poll_interval_seconds=0)
        == "real answer"
    )


def test_chat_returns_plain_assistant_reply_when_execution_finished(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    # Current broken behavior times out after seeing the reply because no
    # FinishAction is present. The fixed path returns before checking remaining
    # time once Kaizen marks the execution finished.
    times = iter([0.0, 2.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    plain_reply = [
        {
            "kind": "MessageEvent",
            "llm_message": {
                "role": "assistant",
                "tool_calls": None,
                "content": [{"type": "text", "text": "Hello! I'm Kaizen."}],
            },
        }
    ]
    client = ChatClient(polls=[plain_reply], execution_statuses=["running", "finished"])
    service = ConversationService(client)

    assert (
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        == "Hello! I'm Kaizen."
    )


def test_chat_returns_none_when_finished_without_final_reply(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    client = ChatClient(
        polls=[[]], execution_statuses=["running", "finished", "finished"]
    )
    service = ConversationService(client)

    assert (
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        is None
    )


def test_chat_rechecks_status_when_plain_reply_seen_near_deadline(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 2.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    plain_reply = [
        {
            "kind": "MessageEvent",
            "llm_message": {
                "role": "assistant",
                "tool_calls": None,
                "content": [{"type": "text", "text": "late plain reply"}],
            },
        }
    ]
    client = ChatClient(
        polls=[plain_reply],
        execution_statuses=["running", "finished"],
    )
    service = ConversationService(client)

    assert (
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        == "late plain reply"
    )


def test_chat_ignores_plain_assistant_reply_while_execution_running(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 0.0, 0.0, 2.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    plain_reply = [
        {
            "kind": "MessageEvent",
            "llm_message": {
                "role": "assistant",
                "tool_calls": None,
                "content": [{"type": "text", "text": "Let me think..."}],
            },
        }
    ]
    client = ChatClient(
        polls=[plain_reply, plain_reply, plain_reply],
        execution_statuses=["running", "running", "running"],
    )
    service = ConversationService(client)

    with pytest.raises(TimeoutError, match="No agent reply"):
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )


def test_chat_raises_conversation_error_on_execution_error_status(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 2.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    client = ChatClient(polls=[[]], execution_statuses=["running", "error"])
    service = ConversationService(client)

    with pytest.raises(ConversationError, match="execution_status=error"):
        service.chat("conv-1", "hi", base_url=KAIZEN_URL, timeout_seconds=1)


def test_chat_ignores_stale_finished_status_from_previous_turn(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 0.0, 0.0, 2.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    final_reply = [
        {
            "kind": "MessageEvent",
            "llm_message": {
                "role": "assistant",
                "tool_calls": None,
                "content": [{"type": "text", "text": "fresh answer"}],
            },
        }
    ]
    client = ChatClient(
        polls=[[], final_reply, final_reply],
        execution_statuses=["finished", "finished", "running", "finished"],
    )
    service = ConversationService(client)

    assert (
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        == "fresh answer"
    )


def test_chat_accepts_fast_plain_reply_when_status_stays_finished(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 0.0, 0.0, 2.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    final_reply = [
        {
            "kind": "MessageEvent",
            "llm_message": {
                "role": "assistant",
                "tool_calls": None,
                "content": [{"type": "text", "text": "fresh fast answer"}],
            },
        }
    ]
    client = ChatClient(
        polls=[final_reply, final_reply],
        execution_statuses=["finished", "finished", "finished", "finished"],
    )
    service = ConversationService(client)

    assert (
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        == "fresh fast answer"
    )


def test_chat_same_terminal_changing_reply_respects_timeout(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 0.0, 2.0])
    slept: list[float] = []
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))

    class ChangingReplyClient(ChatClient):
        def __init__(self):
            super().__init__(polls=[[]], execution_statuses=["finished"])
            self.reply_polls = 0

        def _request(self, method, path, **kwargs):
            if path.endswith("/events") and kwargs.get("params", {}).get("limit") != 1:
                self.reply_polls += 1
                if self.reply_polls > 3:
                    raise AssertionError("chat() did not enforce its timeout")
                return {"events": [_message_event(f"draft {self.reply_polls}")]}
            return super()._request(method, path, **kwargs)

    client = ChangingReplyClient()
    service = ConversationService(client)

    with pytest.raises(TimeoutError, match="No agent reply"):
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )

    assert client.reply_polls == 2
    assert slept == [0.0]


def test_chat_missing_status_does_not_make_stale_terminal_status_fresh(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    stale_interim = [
        {
            "kind": "MessageEvent",
            "llm_message": {
                "role": "assistant",
                "tool_calls": None,
                "content": [{"type": "text", "text": "stale interim"}],
            },
        }
    ]
    final_reply = [
        {
            "kind": "MessageEvent",
            "llm_message": {
                "role": "assistant",
                "tool_calls": None,
                "content": [{"type": "text", "text": "fresh answer"}],
            },
        }
    ]
    client = ChatClient(
        polls=[[], stale_interim, [], final_reply],
        execution_statuses=["finished", None, "finished", "running", "finished"],
    )
    service = ConversationService(client)

    assert (
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        == "fresh answer"
    )


def test_chat_ignores_stale_error_status_from_previous_turn(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 0.0, 0.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    final_reply = [
        {
            "kind": "MessageEvent",
            "llm_message": {
                "role": "assistant",
                "tool_calls": None,
                "content": [{"type": "text", "text": "recovered answer"}],
            },
        }
    ]
    client = ChatClient(
        polls=[[], final_reply, final_reply],
        execution_statuses=["error", "error", "running", "finished"],
    )
    service = ConversationService(client)

    assert (
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        == "recovered answer"
    )


def test_chat_ignores_lingering_stale_error_status_before_recovery(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 0.0, 0.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    final_reply = [_message_event("recovered answer")]
    client = ChatClient(
        polls=[[], [], final_reply],
        execution_statuses=["error", "error", "error", "running", "finished"],
    )
    service = ConversationService(client)

    assert (
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        == "recovered answer"
    )


def test_chat_ignores_interim_reply_while_stale_error_status_lingers(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 0.0, 0.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    interim_reply = [_message_event("still working")]
    final_reply = [_message_event("recovered answer")]
    client = ChatClient(
        polls=[interim_reply, final_reply],
        execution_statuses=["error", "error", "error", "running", "finished"],
    )
    service = ConversationService(client)

    assert (
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        == "recovered answer"
    )


def test_chat_times_out_on_repeated_same_error_status_without_error_event(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    client = ChatClient(
        polls=[[], []],
        execution_statuses=["error", "error", "error"],
    )
    service = ConversationService(client)

    with pytest.raises(TimeoutError, match="No agent reply"):
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )


def test_chat_times_out_when_same_terminal_reply_keeps_changing(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    first_reply = [_message_event("first")]
    second_reply = [_message_event("second")]
    client = ChatClient(
        polls=[first_reply, second_reply],
        execution_statuses=["finished", "finished", "finished", "finished"],
    )
    service = ConversationService(client)

    with pytest.raises(TimeoutError, match="No agent reply"):
        service.chat(
            "conv-1",
            "hi",
            base_url=KAIZEN_URL,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )


def test_wait_until_ready_polls_until_container_active(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list[float] = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))
    client = ChatClient(
        polls=[[]],
        container_statuses=["provisioning", "initializing", "active"],
    )
    service = ConversationService(client)

    conversation = service.wait_until_ready(
        "conv-1", base_url=KAIZEN_URL, timeout_seconds=10, poll_interval_seconds=2
    )

    assert conversation.container_status == "active"
    assert slept == [2, 2]


def test_wait_until_ready_proceeds_when_container_status_missing(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list[float] = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))
    client = ChatClient(polls=[[]], container_statuses=[None])
    service = ConversationService(client)

    conversation = service.wait_until_ready(
        "conv-1", base_url=KAIZEN_URL, timeout_seconds=0, poll_interval_seconds=0
    )

    assert conversation.container_status is None
    assert slept == []


def test_wait_until_ready_accepts_ready_status_synonym(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list[float] = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))
    client = ChatClient(polls=[[]], container_statuses=["ready"])
    service = ConversationService(client)

    conversation = service.wait_until_ready(
        "conv-1", base_url=KAIZEN_URL, timeout_seconds=0, poll_interval_seconds=0
    )

    assert conversation.container_status == "ready"
    assert slept == []


def test_wait_until_ready_raises_on_terminal_container_status(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    client = ChatClient(polls=[[]], container_statuses=["error"])
    service = ConversationService(client)

    with pytest.raises(ConversationError, match="container_status=error"):
        service.wait_until_ready("conv-1", base_url=KAIZEN_URL)


@pytest.mark.parametrize("timeout", [-1.0, float("nan"), float("inf")])
def test_wait_until_ready_invalid_timeout_raises_without_side_effects(timeout):
    client = ChatClient(polls=[[]], container_statuses=["active"])
    service = ConversationService(client)

    with pytest.raises(ValueError, match="timeout_seconds"):
        service.wait_until_ready(
            "conv-1",
            base_url=KAIZEN_URL,
            timeout_seconds=timeout,
        )

    assert client.calls == []


@pytest.mark.parametrize("timeout", [-1.0, float("nan"), float("inf")])
def test_chat_invalid_timeout_raises_without_side_effects(timeout):
    client = ChatClient(polls=[[_finish_event("x")]])
    service = ConversationService(client)

    with pytest.raises(ValueError, match="timeout_seconds"):
        service.chat("conv-1", "hi", base_url=KAIZEN_URL, timeout_seconds=timeout)

    assert client.calls == []


def test_chat_raises_conversation_error_on_agent_error(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    client = ChatClient(polls=[[_error_event("model unreachable")]])
    service = ConversationService(client)

    with pytest.raises(ConversationError, match="model unreachable"):
        service.chat("conv-1", "hi", base_url=KAIZEN_URL)


def test_chat_fire_and_forget_returns_none_without_reading_events():
    client = ChatClient(polls=[[_message_event("ignored")]])
    service = ConversationService(client)

    assert service.chat("conv-1", "hi", base_url=KAIZEN_URL, timeout_seconds=0) is None

    paths = [(m, p) for m, p, _ in client.calls]
    assert ("POST", "api/conversations/conv-1/messages") in paths
    assert ("POST", "api/conversations/conv-1/run") in paths
    # No events are read at all when not waiting.
    assert not any(p.endswith("/events") for _, p, _ in client.calls)


def test_chat_times_out_when_no_reply(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    # deadline (0.0 -> 1.0), first remaining (0.0), second remaining (5.0 -> past).
    times = iter([0.0, 0.0, 5.0])
    monkeypatch.setattr(kaizen_mod.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    client = ChatClient(polls=[[]])  # never any reply
    service = ConversationService(client)

    with pytest.raises(TimeoutError, match="No agent reply"):
        service.chat("conv-1", "hi", base_url=KAIZEN_URL, timeout_seconds=1)
