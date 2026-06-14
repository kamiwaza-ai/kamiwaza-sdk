# kamiwaza_sdk/services/kaizen.py

"""Client services for the Kaizen agent extension.

Kaizen runs as a per-workroom extension behind its own ingress, so every call
takes an explicit ``base_url`` (the Kaizen instance API root). Resolve it once
from the platform extensions API with :func:`resolve_base_url`, then pass it to
``agents.create`` / ``conversations.create``.

Workroom scope is carried as the ``X-Workroom-Id`` header. The authoritative
scope is the caller's identity; with a global PAT (no workroom claim) the
platform honors this header. A workroom-scoped token is the durable fix
(tracked for the nightly-seeding work).
"""

from typing import Any, Dict, List, Optional, Union

from ..schemas.kaizen import Agent, Conversation, LLMConfig
from .base_service import BaseService

# Kaizen route prefixes, relative to the extension's ingress root.
_AGENTS_PATH = "api/agents/"
_CONVERSATIONS_PATH = "api/conversations/"


def _workroom_headers(workroom_id: Optional[Union[str, object]]) -> Dict[str, str]:
    """Build the X-Workroom-Id header that scopes a Kaizen call to a workroom."""
    if workroom_id is None:
        return {}
    return {"X-Workroom-Id": str(workroom_id)}


def resolve_base_url(
    client, extension_name: str = "kaizen", *, public: bool = False
) -> str:
    """Best-effort lookup of a Kaizen instance's ingress root from the platform.

    Reads the extension's resolved endpoints. Raises ValueError when no endpoint
    is published yet (extension still provisioning) — callers that already know
    the URL should pass ``base_url`` directly instead.
    """
    extension = client.extensions.get_extension(extension_name)
    endpoints = getattr(extension, "endpoints", None)
    candidates = []
    if endpoints is not None:
        # ``external`` is the ingress-reachable URL; some deployments instead
        # surface ``public_api_url`` / ``api_url`` (extra fields). Prefer the
        # public-facing field when ``public`` is set, else the ingress URL.
        order = (
            ("public_api_url", "api_url", "external")
            if public
            else ("external", "api_url", "public_api_url")
        )
        for attr in order:
            value = getattr(endpoints, attr, None)
            if value:
                candidates.append(value)
    if not candidates:
        raise ValueError(
            f"Extension '{extension_name}' has no published endpoint yet; "
            "wait for it to become ready or pass base_url explicitly."
        )
    return str(candidates[0]).rstrip("/")


class AgentService(BaseService):
    """Create and list Kaizen agents within a workroom."""

    def create(
        self,
        *,
        base_url: str,
        name: str,
        llm: Union[LLMConfig, Dict[str, Any]],
        description: Optional[str] = None,
        agent_config: Optional[Dict[str, Any]] = None,
        custom_instructions: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        mcp_headers: Optional[Dict[str, Dict[str, str]]] = None,
        workroom_id: Optional[Union[str, object]] = None,
    ) -> Agent:
        """Create an agent bound to a model via ``agent_config.llm``.

        Args:
            base_url: The Kaizen instance API root (see :func:`resolve_base_url`).
            name: Agent display name.
            llm: Model binding (``LLMConfig`` or a raw dict). Merged into
                ``agent_config["llm"]``.
            description: Optional agent description.
            agent_config: Optional base agent config; ``llm`` is merged in.
            custom_instructions: Optional behavior instructions.
            llm_api_key: Optional custom-endpoint API key (encrypted server-side).
            mcp_headers: Optional per-MCP-server headers.
            workroom_id: Workroom to scope the agent to (X-Workroom-Id header).

        Returns:
            Agent: The created agent.
        """
        llm_dict = (
            llm.model_dump(exclude_none=True)
            if isinstance(llm, LLMConfig)
            else dict(llm)
        )
        merged_config: Dict[str, Any] = dict(agent_config or {})
        merged_config["llm"] = llm_dict

        body: Dict[str, Any] = {"name": name, "agent_config": merged_config}
        if description is not None:
            body["description"] = description
        if custom_instructions is not None:
            body["custom_instructions"] = custom_instructions
        if llm_api_key is not None:
            body["llm_api_key"] = llm_api_key
        if mcp_headers is not None:
            body["mcp_headers"] = mcp_headers

        response = self.client._request(
            "POST",
            _AGENTS_PATH,
            base_url=base_url,
            json=body,
            headers=_workroom_headers(workroom_id),
        )
        return Agent.model_validate(response)

    def list(
        self,
        *,
        base_url: str,
        workroom_id: Optional[Union[str, object]] = None,
    ) -> List[Agent]:
        """List agents visible in the workroom scope."""
        response = self.client._request(
            "GET",
            _AGENTS_PATH,
            base_url=base_url,
            headers=_workroom_headers(workroom_id),
        )
        items = (
            response.get("agents", response) if isinstance(response, dict) else response
        )
        return [Agent.model_validate(item) for item in items]

    def bind_skill(
        self,
        agent_id: str,
        skill_id: Union[str, object],
        *,
        base_url: str,
        workroom_id: Optional[Union[str, object]] = None,
    ) -> Agent:
        """Attach a published library skill to an agent.

        Kaizen rejects ``skill_bindings`` at agent-create time; skills must be
        attached through this managed endpoint after the agent exists. The skill
        must be published (draft skills are rejected with 404).

        Args:
            agent_id: The agent to attach the skill to.
            skill_id: The published skill's id.
            base_url: The Kaizen instance API root.
            workroom_id: Workroom scope (X-Workroom-Id header).

        Returns:
            Agent: The updated agent (including the new binding).
        """
        response = self.client._request(
            "POST",
            f"api/agents/{agent_id}/skill-bindings",
            base_url=base_url,
            json={"skill_id": str(skill_id)},
            headers=_workroom_headers(workroom_id),
        )
        return Agent.model_validate(response)


class ConversationService(BaseService):
    """Create Kaizen conversations (auto-starts the agent sandbox)."""

    def create(
        self,
        *,
        base_url: str,
        agent_id: str,
        title: Optional[str] = None,
        max_iterations: int = 500,
        stuck_detection: bool = True,
        ephemeral: bool = False,
        workroom_id: Optional[Union[str, object]] = None,
    ) -> Conversation:
        """Start a conversation with an agent; provisions the sandbox container.

        Args:
            base_url: The Kaizen instance API root.
            agent_id: The agent to converse with.
            title: Optional conversation title.
            max_iterations: Agent step ceiling (Kaizen default 500).
            stuck_detection: Enable Kaizen's stuck-loop detection.
            ephemeral: Ephemeral (non-persisted) conversation; rejected by
                Kaizen in shared workrooms.
            workroom_id: Workroom scope (X-Workroom-Id header).

        Returns:
            Conversation: The created conversation.
        """
        body: Dict[str, Any] = {
            "agent_id": agent_id,
            "max_iterations": max_iterations,
            "stuck_detection": stuck_detection,
            "ephemeral": ephemeral,
        }
        if title is not None:
            body["title"] = title

        response = self.client._request(
            "POST",
            _CONVERSATIONS_PATH,
            base_url=base_url,
            json=body,
            headers=_workroom_headers(workroom_id),
        )
        return Conversation.model_validate(response)

    def send_message(
        self,
        conversation_id: str,
        message: str,
        *,
        base_url: str,
        workroom_id: Optional[Union[str, object]] = None,
    ) -> None:
        """Enqueue a user message on a conversation.

        The message is queued; call :meth:`run` to have the agent process it.
        Returns None (the endpoint responds 204 No Content).
        """
        self.client._request(
            "POST",
            f"api/conversations/{conversation_id}/messages",
            base_url=base_url,
            json={"message": message},
            headers=_workroom_headers(workroom_id),
            expect_json=False,
        )

    def run(
        self,
        conversation_id: str,
        *,
        base_url: str,
        workroom_id: Optional[Union[str, object]] = None,
    ) -> None:
        """Trigger the agent to process queued messages (runs in the background).

        Observe progress/results via :meth:`get_events`. Returns None (204).
        """
        self.client._request(
            "POST",
            f"api/conversations/{conversation_id}/run",
            base_url=base_url,
            headers=_workroom_headers(workroom_id),
            expect_json=False,
        )

    def get_events(
        self,
        conversation_id: str,
        *,
        base_url: str,
        workroom_id: Optional[Union[str, object]] = None,
        offset: int = 0,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Fetch conversation events (agent messages, tool calls, errors).

        Returns the raw events envelope ``{events, total, offset, limit}``.
        Event ``kind`` values include ``MessageEvent`` (the ``llm_message`` with
        role/content), ``ConversationErrorEvent``, tool events, etc.
        """
        return self.client._request(
            "GET",
            f"api/conversations/{conversation_id}/events",
            base_url=base_url,
            params={"offset": offset, "limit": limit},
            headers=_workroom_headers(workroom_id),
        )
