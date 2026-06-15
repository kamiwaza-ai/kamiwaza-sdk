from __future__ import annotations

from types import SimpleNamespace

import pytest

from kamiwaza_sdk.schemas.kaizen import LLMConfig
from kamiwaza_sdk.services.kaizen import (
    AgentService,
    ConversationService,
    resolve_base_url,
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
