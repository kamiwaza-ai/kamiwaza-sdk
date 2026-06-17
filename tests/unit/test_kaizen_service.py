from __future__ import annotations

from types import SimpleNamespace

import pytest

from kamiwaza_sdk.exceptions import APIError, AuthenticationError, NotFoundError
from kamiwaza_sdk.schemas.kaizen import LLMConfig
from kamiwaza_sdk.services.kaizen import (
    AgentService,
    AmbiguousExtensionError,
    ConversationError,
    ConversationService,
    _agent_error_from_events,
    _has_finish_action,
    _is_serving,
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
    responses = {("POST", "api/agents/"): {"id": "agent-1", "name": "seed-agent"}}
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
    assert (method, path) == ("POST", "api/agents/")
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
    responses = {("POST", "api/agents/"): {"id": "agent-2"}}
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
        ("POST", "api/conversations/"): {"id": "conv-1", "agent_id": "agent-1"}
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
    assert (method, path) == ("POST", "api/conversations/")
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
            external=None, public_api_url=None, api_url="https://kamiwaza.test/kaizen-api/"
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
    client._request = lambda *a, **k: {}
    return client


def test_resolve_base_url_matches_workroom_by_base_name_and_id():
    # The operator suffixes the CR name; match on base name + the exact workroom,
    # and ignore other extensions (milvus) and other workrooms' Kaizen.
    client = _client_listing(
        [
            _ext("kaizen-4f8b3ae1", "wr-A"),
            _ext("kaizen-99999999", "wr-B"),  # another workroom's Kaizen
            _ext("service-milvus-xyz", "wr-A", external="https://x/milvus"),
        ]
    )

    assert resolve_base_url(client, "kaizen", workroom_id="wr-A") == KAIZEN_URL
    # The listing MUST be scoped to the workroom (X-Workroom-Id); without it the
    # platform returns only global extensions and the match silently misses.
    assert client.seen_workroom_ids == ["wr-A"]


def test_resolve_base_url_workroom_no_match_raises():
    # Kaizen exists, but only in a different workroom — must not be picked.
    client = _client_listing([_ext("kaizen-99999999", "wr-B")])

    with pytest.raises(ValueError, match="No 'kaizen' extension found"):
        resolve_base_url(client, "kaizen", workroom_id="wr-A")


def test_resolve_base_url_workroom_ambiguous_raises():
    # Two Kaizen in the SAME workroom is anomalous — fail loudly, don't guess.
    # AmbiguousExtensionError (not ValueError) so the wait loop won't retry it.
    client = _client_listing([_ext("kaizen-aaaa", "wr-A"), _ext("kaizen-bbbb", "wr-A")])

    with pytest.raises(AmbiguousExtensionError, match="Multiple 'kaizen' extensions"):
        resolve_base_url(client, "kaizen", workroom_id="wr-A")


def test_resolve_base_url_workroom_ignores_unsuffixed_exact_name():
    # A per-workroom instance is always the operator's suffixed CR (kaizen-<hash>),
    # so a bare exact-name extension is not matched — a stray one can't shadow the
    # real instance or trip a false ambiguity.
    client = _client_listing(
        [
            _ext("kaizen", "wr-A", external="https://x/bare"),
            _ext("kaizen-4f8b3ae1", "wr-A"),
        ]
    )

    assert resolve_base_url(client, "kaizen", workroom_id="wr-A") == KAIZEN_URL


def test_wait_for_base_url_does_not_retry_ambiguity(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))
    client = _client_listing([_ext("kaizen-aaaa", "wr-A"), _ext("kaizen-bbbb", "wr-A")])

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
    rounds = [[], [_ext("kaizen-4f8b3ae1", "wr-A")]]

    def list_extensions(workroom_id=None):
        return rounds.pop(0)

    client = SimpleNamespace(
        extensions=SimpleNamespace(list_extensions=list_extensions),
        _request=lambda *a, **k: {},  # backend serves once listed
    )

    url = wait_for_base_url(
        client, "kaizen", workroom_id="wr-A", poll_interval_seconds=0
    )
    assert url == KAIZEN_URL
    assert rounds == []


def test_wait_for_base_url_returns_when_ready():
    extension = SimpleNamespace(
        endpoints=SimpleNamespace(external=KAIZEN_URL, public_api_url=None)
    )
    client = SimpleNamespace(
        extensions=SimpleNamespace(get_extension=lambda name: extension),
        _request=lambda *a, **k: {},  # backend serves
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
        _request=lambda *a, **k: {},  # backend serves once published
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
    assert (method, path) == ("GET", "api/agents/")
    assert kwargs["base_url"] == KAIZEN_URL
    assert kwargs["headers"] == {"X-Workroom-Id": "wr-A"}


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_is_serving_false_on_5xx(status):
    # Any 5xx means the gateway/backend isn't ready (no healthy upstream during
    # pod startup is 503, but envoy can also emit 502/504 mid-startup).
    def server_error(*a, **k):
        raise APIError("server error", status_code=status)

    client = SimpleNamespace(_request=server_error)
    assert _is_serving(client, KAIZEN_URL, workroom_id=None) is False


def test_is_serving_false_on_connection_error():
    # A transport failure surfaces as APIError with no status_code — the route
    # exists but nothing is answering yet, so keep polling.
    def refused(*a, **k):
        raise APIError("An error occurred while making the request: refused")

    client = SimpleNamespace(_request=refused)
    assert _is_serving(client, KAIZEN_URL, workroom_id=None) is False


def test_is_serving_true_on_4xx():
    # A 4xx means the backend answered — it's up, just rejecting this probe.
    def not_found(*a, **k):
        raise APIError("not found", status_code=404)

    client = SimpleNamespace(_request=not_found)
    assert _is_serving(client, KAIZEN_URL, workroom_id=None) is True


def test_is_serving_true_on_structured_error():
    # Non-APIError KamiwazaError subclasses (auth/validation) prove a response.
    def unauthorized(*a, **k):
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

    def probe(*a, **k):
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
    def always_503(*a, **k):
        raise APIError("no healthy upstream", status_code=503)

    client = _serving_client(always_503)
    with pytest.raises(TimeoutError, match="not serving yet"):
        wait_for_base_url(client, "kaizen", timeout_seconds=0)


def test_agent_list_unwraps_agents_envelope():
    responses = {("GET", "api/agents/"): {"agents": [{"id": "agent-1", "name": "a"}]}}
    client = DummyClient(responses)
    service = AgentService(client)

    agents = service.list(base_url=KAIZEN_URL, workroom_id="wr-1")

    assert [a.id for a in agents] == ["agent-1"]
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("GET", "api/agents/")
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

    result = service.send_message("conv-1", "hello", base_url=KAIZEN_URL, workroom_id="wr-1")

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

    out = service.get_events("conv-1", base_url=KAIZEN_URL, workroom_id="wr-1", offset=5, limit=50)

    assert out == {"events": [], "total": 0}
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("GET", "api/conversations/conv-1/events")
    assert kwargs["params"] == {"offset": 5, "limit": 50}
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

    def __init__(self, polls, baseline_total=0):
        self.polls = list(polls)
        self.baseline_total = baseline_total
        self.calls: list[tuple[str, str, dict]] = []

    def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("/events"):
            if kwargs.get("params", {}).get("limit") == 1:
                return {"total": self.baseline_total, "events": []}
            events = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
            return {"events": events}
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
        _agent_error_from_events([{"kind": "ConversationErrorEvent", "message": "nope"}])
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


def test_chat_non_positive_poll_interval_does_not_raise(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    slept: list = []
    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda s: slept.append(s))
    # First poll empty, second has the reply; a negative interval must clamp to
    # 0 at the sleep rather than raising ValueError out of the wait loop.
    client = ChatClient(polls=[[], [_finish_event("done")]])
    service = ConversationService(client)

    assert service.chat("conv-1", "hi", base_url=KAIZEN_URL, poll_interval_seconds=-5) == "done"
    assert slept == [0.0]


def test_chat_ignores_interim_assistant_text_before_finish(monkeypatch):
    import kamiwaza_sdk.services.kaizen as kaizen_mod

    monkeypatch.setattr(kaizen_mod.time, "sleep", lambda _s: None)
    # First poll: only interim assistant narration, no finish yet — must NOT be
    # returned. Second poll: the terminal finish carries the real answer.
    client = ChatClient(polls=[[_message_event("Let me think…")], [_finish_event("real answer")]])
    service = ConversationService(client)

    assert service.chat("conv-1", "hi", base_url=KAIZEN_URL, poll_interval_seconds=0) == "real answer"


def test_chat_negative_timeout_raises():
    service = ConversationService(ChatClient(polls=[[_finish_event("x")]]))
    with pytest.raises(ValueError, match="zero or a positive"):
        service.chat("conv-1", "hi", base_url=KAIZEN_URL, timeout_seconds=-1)


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
