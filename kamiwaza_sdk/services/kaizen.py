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

import time
from typing import Any, Dict, List, Optional, Union

from ..exceptions import KamiwazaError, NotFoundError
from ..schemas.kaizen import Agent, Conversation, LLMConfig
from .base_service import BaseService

# Kaizen route prefixes, relative to the extension's ingress root.
_AGENTS_PATH = "api/agents/"
_CONVERSATIONS_PATH = "api/conversations/"


class AmbiguousExtensionError(KamiwazaError):
    """More than one extension matched a workroom + base-name lookup.

    A deterministic failure (a workroom should hold one instance of a given
    extension), so callers must NOT retry it — unlike the transient "not visible
    yet" / "no endpoint yet" states that resolve on a later poll.
    """


def _workroom_headers(workroom_id: Optional[Union[str, object]]) -> Dict[str, str]:
    """Build the X-Workroom-Id header that scopes a Kaizen call to a workroom."""
    if workroom_id is None:
        return {}
    return {"X-Workroom-Id": str(workroom_id)}


def _endpoint_from_extension(extension, *, public: bool) -> Optional[str]:
    """Pull the ingress (or public) URL off an extension's endpoints, or None."""
    endpoints = getattr(extension, "endpoints", None)
    if endpoints is None:
        return None
    # ``external`` is the ingress-reachable URL; some deployments instead surface
    # ``public_api_url`` / ``api_url`` (extra fields). Prefer the public-facing
    # field when ``public`` is set, else the ingress URL.
    order = (
        ("public_api_url", "api_url", "external")
        if public
        else ("external", "api_url", "public_api_url")
    )
    for attr in order:
        value = getattr(endpoints, attr, None)
        if value:
            return str(value).rstrip("/")
    return None


def _find_workroom_extension(client, extension_name: str, workroom_id):
    """Find a workroom's extension by base name (the operator suffixes CR names).

    The operator names a per-workroom CR ``<extension_name>-<hash>`` and stamps it
    with its ``workroom_id``, so we match on both: the exact ``workroom_id`` (never
    another workroom's instance) and the ``<extension_name>-`` prefix (the kaizen,
    not milvus/omniparse). ``workroom_id`` is the strong discriminator; the
    ambiguity guard below is the backstop if more than one ever matches — so the
    prefix check doesn't need to over-anchor on the hash shape. Requires the client
    to be scoped into the workroom — the platform only lists a workroom's
    extensions to a caller scoped into it.

    Raises:
        ValueError: when no instance is visible yet (still provisioning) —
            transient, callers may retry.
        AmbiguousExtensionError: when more than one matches (a workroom should
            hold one) — deterministic, callers must not retry.
    """
    prefix = f"{extension_name}-"
    matches = [
        ext
        for ext in client.extensions.list_extensions()
        if str(getattr(ext, "workroom_id", "")) == str(workroom_id)
        and ext.name.startswith(prefix)
    ]
    if not matches:
        raise ValueError(
            f"No '{extension_name}' extension found in workroom '{workroom_id}' yet; "
            "wait for it to become ready or pass base_url explicitly."
        )
    if len(matches) > 1:
        names = ", ".join(sorted(m.name for m in matches))
        raise AmbiguousExtensionError(
            f"Multiple '{extension_name}' extensions in workroom '{workroom_id}' "
            f"({names}); cannot pick one unambiguously."
        )
    return matches[0]


def resolve_base_url(
    client,
    extension_name: str = "kaizen",
    *,
    workroom_id: Optional[Union[str, object]] = None,
    public: bool = False,
) -> str:
    """Look up an extension's ingress root from the platform.

    With ``workroom_id``, resolves a per-workroom extension by base name +
    workroom (the operator suffixes the CR name, so an exact lookup misses it).
    Without it, does an exact ``get_extension`` lookup (cluster-scoped
    extensions). Raises ValueError when no endpoint is published yet (extension
    still provisioning) — callers that already know the URL should pass
    ``base_url`` directly instead.
    """
    if workroom_id is not None:
        extension = _find_workroom_extension(client, extension_name, workroom_id)
    else:
        extension = client.extensions.get_extension(extension_name)
    url = _endpoint_from_extension(extension, public=public)
    if url is None:
        raise ValueError(
            f"Extension '{getattr(extension, 'name', extension_name)}' has no "
            "published endpoint yet; wait for it to become ready or pass "
            "base_url explicitly."
        )
    return url


def wait_for_base_url(
    client,
    extension_name: str = "kaizen",
    *,
    workroom_id: Optional[Union[str, object]] = None,
    public: bool = False,
    timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
) -> str:
    """Poll :func:`resolve_base_url` until the extension publishes its ingress.

    Right after an install the ingress isn't resolvable yet: the extension isn't
    visible yet (``NotFoundError`` / no workroom match) and/or its endpoints
    aren't published (``ValueError``). Both are transient, so retry until one
    resolves or ``timeout_seconds`` elapses. Mirrors
    ``serving.wait_deployment_ready``'s wait contract. A deterministic
    ``AmbiguousExtensionError`` is NOT retried — it propagates immediately rather
    than burning the full timeout on something that will never resolve.

    Args:
        client: An authenticated client (workroom-scoped if the extension is).
        extension_name: Catalog/base name of the extension (e.g. "kaizen").
        workroom_id: When set, resolve the per-workroom instance by base name +
            workroom (see :func:`resolve_base_url`).
        public: Prefer the public-facing endpoint over the ingress URL.
        timeout_seconds: Max time to wait before giving up.
        poll_interval_seconds: Delay between attempts.

    Returns:
        The resolved ingress root (no trailing slash).

    Raises:
        TimeoutError: If no endpoint resolves within ``timeout_seconds``.
    """
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    while True:
        attempts += 1
        try:
            return resolve_base_url(
                client, extension_name, workroom_id=workroom_id, public=public
            )
        except (ValueError, NotFoundError) as exc:
            last_err = exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Extension '{extension_name}' ingress not resolvable after "
                f"{timeout_seconds:g}s ({attempts} attempts): {last_err}"
            )
        # Cap the sleep at the remaining budget so the wait stays bounded even
        # when poll_interval_seconds exceeds the time left.
        time.sleep(min(poll_interval_seconds, remaining))


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
