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

from ..exceptions import APIError, KamiwazaError, NotFoundError
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


class ConversationError(KamiwazaError):
    """The agent surfaced an error event while processing a chat message.

    A terminal failure for that turn (the agent reported it can't proceed), so
    callers must NOT keep polling for a reply — distinct from the transient
    "no reply yet" state that resolves on a later poll.
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
        for ext in client.extensions.list_extensions(workroom_id=workroom_id)
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


def _is_serving(client, base_url: str, *, workroom_id) -> bool:
    """Probe the extension backend to confirm it answers, not just that it routes.

    A published ingress only means envoy has a route — right after install the
    backend pod can still be starting, so the gateway returns a 5xx (``503 no
    healthy upstream``, or a ``502``/``504`` while the upstream is coming up) or
    refuses the connection outright, and a real call against the URL would fail
    too (ENG-7111). Treat any 5xx and transport errors (no response at all) as
    "not ready yet"; a 2xx — or even a 4xx — means the backend answered at the
    app layer, which is all a caller needs before using the URL.

    In this client a 5xx and a connection failure both surface as ``APIError``
    (the 5xx carries its ``status_code``; a transport error carries
    ``status_code=None``). Other ``KamiwazaError`` subclasses (auth/validation)
    mean the backend returned a structured response, i.e. it is serving.
    """
    try:
        client._request(
            "GET",
            _AGENTS_PATH,
            base_url=base_url,
            headers=_workroom_headers(workroom_id),
        )
    except APIError as exc:
        status = getattr(exc, "status_code", None)
        # No status = transport error before any response; any 5xx = the gateway
        # or backend isn't ready yet. Both are transient startup states — keep
        # polling. A 4xx means the backend answered, i.e. it is serving.
        return status is not None and not 500 <= status <= 599
    except KamiwazaError:
        return True
    return True


def wait_for_base_url(
    client,
    extension_name: str = "kaizen",
    *,
    workroom_id: Optional[Union[str, object]] = None,
    public: bool = False,
    timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 5.0,
) -> str:
    """Poll until the extension is resolvable AND actually serving requests.

    Two startup stages have to clear before the URL is usable, and both are
    transient — retry until one resolves or ``timeout_seconds`` elapses:

    1. **Ingress resolves.** Right after an install the extension isn't visible
       yet (``NotFoundError`` / no workroom match) and/or its endpoints aren't
       published (``ValueError``).
    2. **Backend serves.** Once the ingress is published, envoy has a route but
       the backend pod may still be starting — a call would get ``503 no healthy
       upstream`` (ENG-7111). :func:`_is_serving` probes the backend and we keep
       polling until it answers, so the returned URL is immediately usable.

    Mirrors ``serving.wait_deployment_ready``'s wait contract. A deterministic
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
        The resolved ingress root (no trailing slash), confirmed serving.

    Raises:
        TimeoutError: If the extension isn't serving within ``timeout_seconds``.
    """
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_err: object = "not resolvable yet"
    while True:
        attempts += 1
        try:
            url = resolve_base_url(
                client, extension_name, workroom_id=workroom_id, public=public
            )
            # The readiness probe rides the credentialed platform client, which
            # refuses off-host URLs (it won't leak the bearer). The public URL
            # may be off-host (browser-facing), so probe the same-host ingress
            # endpoint — the route the backend actually serves on — and return
            # whichever URL the caller asked for.
            probe_url = (
                url
                if not public
                else resolve_base_url(
                    client, extension_name, workroom_id=workroom_id, public=False
                )
            )
            if _is_serving(client, probe_url, workroom_id=workroom_id):
                return url
            last_err = "ingress published but backend not serving yet (503)"
        except (ValueError, NotFoundError) as exc:
            last_err = exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Extension '{extension_name}' not serving after "
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


# Event ``kind`` values that mark a failed turn. The Kaizen backend serializes
# the agent error as ``AgentErrorEvent``; ``ConversationErrorEvent`` is accepted
# defensively so a backend that names it differently still surfaces a clear
# error instead of degrading to a generic timeout.
_ERROR_EVENT_KINDS = frozenset({"AgentErrorEvent", "ConversationErrorEvent"})

# ``execution_status`` is the authoritative "turn has settled" signal. An agent
# that replies with a plain assistant message and never calls the ``finish`` tool
# emits no FinishAction, so events alone can't tell us the run is done — the
# status can. Reading the reply only at a DONE status keeps interim narration
# (emitted while ``running``) from being taken as the final answer.
_RUN_DONE_STATUSES = frozenset({"finished", "completed", "done"})
# ``stuck`` is Kaizen's stuck-loop-detection status: terminal but not a success,
# so fail fast on it rather than polling out the whole timeout.
_RUN_FAILED_STATUSES = frozenset({"error", "errored", "failed", "stuck"})

# A conversation's sandbox container (``container_status``) must be ``active``
# before the agent can process a message. On a fresh box that container can take
# a while to provision (``initializing``/``pending``/``provisioning``), so
# sending a message before it's up races the sandbox; :meth:`wait_until_ready`
# polls until it's active. ``suspended``/``stopped``/``error``/``deleted`` mean
# it won't come up — fail rather than wait out the timeout.
_CONTAINER_READY_STATUS = "active"
_CONTAINER_FAILED_STATUSES = frozenset(
    {"error", "errored", "failed", "stopped", "suspended", "deleted", "terminated"}
)


def _agent_error_from_events(events: List[Dict[str, Any]]) -> Optional[str]:
    """Return an agent error message from a list of event payloads, or None.

    A failed turn is a terminal signal that the agent can't answer, so
    :meth:`ConversationService.chat` stops waiting on it. The message is read
    from the event's ``error`` or ``message`` field.
    """
    for event in events:
        if event.get("kind") in _ERROR_EVENT_KINDS:
            return str(event.get("error") or event.get("message") or "agent error")
    return None


def _has_finish_action(events: List[Dict[str, Any]]) -> bool:
    """True if the agent emitted its terminal ``finish`` action this turn.

    The run is done once a ``FinishAction`` lands, so its presence with no usable
    reply text means the agent finished empty — a terminal state, not "not yet".
    """
    return any(
        event.get("kind") == "ActionEvent"
        and (event.get("action") or {}).get("kind") == "FinishAction"
        for event in events
    )


def _reply_from_events(events: List[Dict[str, Any]]) -> Optional[str]:
    """Return the latest non-empty agent reply text, or None.

    The agent signals "done" two ways, and we accept either (latest wins):

    * **The ``finish`` tool** — an ``ActionEvent`` whose ``action.kind`` is
      ``FinishAction``, carrying the final answer in ``action.message``. This is
      the canonical completion signal for the agent runtime, so it's the usual
      source of the reply.
    * **A plain assistant turn** — a ``MessageEvent`` whose ``llm_message.role``
      is ``assistant``; its text is the concatenation of the message's ``text``
      content blocks (image blocks ignored). Kept as a fallback for replies that
      don't route through ``finish``.
    """
    reply: Optional[str] = None
    for event in events:
        kind = event.get("kind")
        if kind == "ActionEvent":
            action = event.get("action") or {}
            if action.get("kind") == "FinishAction":
                message = action.get("message")
                if isinstance(message, str) and message.strip():
                    reply = message.strip()
            continue
        if kind != "MessageEvent":
            continue
        message_obj = event.get("llm_message") or {}
        if message_obj.get("role") != "assistant":
            continue
        text = "".join(
            block.get("text", "")
            for block in (message_obj.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        if text:
            reply = text
    return reply


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
        role/content), ``ActionEvent`` (incl. the terminal ``FinishAction``),
        ``AgentErrorEvent``, tool events, etc.
        """
        return self.client._request(
            "GET",
            f"api/conversations/{conversation_id}/events",
            base_url=base_url,
            params={"offset": offset, "limit": limit},
            headers=_workroom_headers(workroom_id),
        )

    def get(
        self,
        conversation_id: str,
        *,
        base_url: str,
        workroom_id: Optional[Union[str, object]] = None,
    ) -> Conversation:
        """Fetch a conversation, including its ``execution_status``.

        ``execution_status`` reports whether the agent's run is still working
        (``running``) or has settled (``finished``/``error``); :meth:`chat` polls
        it to know when a reply is ready.
        """
        response = self.client._request(
            "GET",
            f"api/conversations/{conversation_id}",
            base_url=base_url,
            headers=_workroom_headers(workroom_id),
        )
        return Conversation.model_validate(response)

    def wait_until_ready(
        self,
        conversation_id: str,
        *,
        base_url: str,
        workroom_id: Optional[Union[str, object]] = None,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 5.0,
    ) -> Conversation:
        """Poll until the conversation's sandbox container is ``active``.

        :meth:`create` provisions a per-conversation sandbox container, but on a
        fresh box that can take a while to come up; sending a message before it's
        ready races the sandbox. This waits for ``container_status == "active"``
        (mirrors :func:`wait_for_base_url` for the extension ingress). Returns the
        ready :class:`Conversation`.

        Raises:
            ValueError: ``timeout_seconds`` is negative.
            ConversationError: the container reached a terminal non-serving status
                (``suspended``/``stopped``/``error``/…) — it won't become ready.
            TimeoutError: the container was not ``active`` within the budget.
        """
        if timeout_seconds < 0:
            raise ValueError(
                "timeout_seconds must be zero or a positive number of seconds."
            )
        deadline = time.monotonic() + timeout_seconds
        while True:
            conversation = self.get(
                conversation_id, base_url=base_url, workroom_id=workroom_id
            )
            status = (conversation.container_status or "").strip().lower()
            if status == _CONTAINER_READY_STATUS:
                return conversation
            if status in _CONTAINER_FAILED_STATUSES:
                raise ConversationError(
                    f"Sandbox container for conversation {conversation_id} "
                    f"reported status {status!r}; it will not become ready."
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Sandbox container for conversation {conversation_id} not "
                    f"ready after {timeout_seconds:g}s (last status {status!r})."
                )
            time.sleep(max(0.0, min(poll_interval_seconds, remaining)))

    def chat(
        self,
        conversation_id: str,
        message: str,
        *,
        base_url: str,
        workroom_id: Optional[Union[str, object]] = None,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 3.0,
    ) -> Optional[str]:
        """Send a message, run the agent, and return its reply.

        Wraps :meth:`send_message` + :meth:`run`, then polls :meth:`get` (run
        status) and :meth:`get_events` until the agent's reply lands. Used to
        exercise an agent end to end (e.g. proving a freshly seeded agent
        actually responds).

        ``timeout_seconds`` is the wait budget: a positive value (the default)
        waits up to that long and returns the reply text; ``0`` is fire-and-forget
        — the message is sent and the run triggered, but the method returns
        ``None`` immediately without waiting.

        The reply is taken only once the run is **terminal**, established three
        ways: the ``finish`` tool (``FinishAction``); an error (an error event or
        an ``error``/``failed`` ``execution_status``) → raises; or the run
        reaching a done ``execution_status`` (``finished``) with a reply present.
        Gating on a done status means an interim assistant message emitted while
        the run is still working — or a run that stalls (``waiting_for_confirmation``)
        — is never mistaken for the final answer; a stalled run simply times out.
        This matters for agents that answer with a plain assistant ``MessageEvent``
        and never call ``finish`` (e.g. a Bedrock-backed agent), which would
        otherwise wait forever for a FinishAction that never comes. Only this
        turn's events are considered (the pre-run event count is the read offset),
        and only the first page of them (``get_events`` default ``limit``); a
        verification turn is a handful of events, so this is not paginated.

        Args:
            conversation_id: The conversation to post to.
            message: The prompt to send.
            base_url: The Kaizen instance API root.
            workroom_id: Workroom scope (X-Workroom-Id header).
            timeout_seconds: Max seconds to wait for the reply; ``0`` = don't wait.
            poll_interval_seconds: Delay between event polls.

        Returns:
            The agent's reply text; ``None`` when ``timeout_seconds`` is 0
            (fire-and-forget) or the agent finished without any reply text.

        Raises:
            ValueError: ``timeout_seconds`` is negative.
            ConversationError: The agent emitted an error event for this turn.
            TimeoutError: The agent did not finish within ``timeout_seconds``.
        """
        if timeout_seconds < 0:
            raise ValueError(
                "timeout_seconds must be zero or a positive number of seconds."
            )
        self.send_message(
            conversation_id, message, base_url=base_url, workroom_id=workroom_id
        )
        if not timeout_seconds:
            self.run(conversation_id, base_url=base_url, workroom_id=workroom_id)
            return None

        # run() only schedules the background run, so the first poll can still
        # report the conversation's PRIOR state. Capture the pre-run status so a
        # stale terminal status (a reused conversation's last turn, or this turn's
        # initial 'finished') isn't read as this turn's outcome — it counts only
        # once it has changed from this value (which still catches a real fresh
        # failure: a new conversation's 'finished' → 'error' is a change).
        pre_run_status = (
            (
                self.get(
                    conversation_id, base_url=base_url, workroom_id=workroom_id
                ).execution_status
                or ""
            )
            .strip()
            .lower()
        )
        # Event count before the run, so polling reads only this turn's events
        # (the just-queued user message is excluded — it's counted here).
        baseline = self.get_events(
            conversation_id, base_url=base_url, workroom_id=workroom_id, limit=1
        ).get("total", 0)
        self.run(conversation_id, base_url=base_url, workroom_id=workroom_id)

        deadline = time.monotonic() + timeout_seconds
        # True once the STATUS shows this turn's run is underway; gates both
        # terminal branches so a stale status isn't read as this run's outcome.
        saw_run_status = False
        while True:
            # Read status before events: if the run reports done, the events
            # fetched next already include the reply it settled with.
            status = (
                (
                    self.get(
                        conversation_id, base_url=base_url, workroom_id=workroom_id
                    ).execution_status
                    or ""
                )
                .strip()
                .lower()
            )
            new_events = self.get_events(
                conversation_id,
                base_url=base_url,
                workroom_id=workroom_id,
                offset=baseline,
            ).get("events", [])
            # The run is underway — so a terminal status is THIS turn's — once the
            # status goes non-terminal or differs from the pre-run value. NOT keyed
            # on new_events: an interim assistant message is "events" but doesn't
            # mean the run finished, so it must not unlock the terminal branches.
            if status != pre_run_status or (
                status
                and status not in _RUN_DONE_STATUSES
                and status not in _RUN_FAILED_STATUSES
            ):
                saw_run_status = True
            error = _agent_error_from_events(new_events)
            if error is not None:
                raise ConversationError(
                    f"Agent errored on conversation {conversation_id}: {error}"
                )
            if saw_run_status and status in _RUN_FAILED_STATUSES:
                raise ConversationError(
                    f"Agent run on conversation {conversation_id} reported "
                    f"status {status!r}."
                )
            if _has_finish_action(new_events):
                # A FinishAction this turn is unambiguously terminal (it's a new
                # event past the baseline), so take the reply now regardless of
                # status — the finish message, the last assistant text if finish
                # carried none, or None. Don't accept interim narration before it.
                return _reply_from_events(new_events)
            if saw_run_status and status in _RUN_DONE_STATUSES:
                # Run settled without a finish tool (e.g. a plain assistant
                # reply). Gated on saw_run_status so a stale 'finished' before
                # the run starts can't return an interim message; return the
                # reply once present (a genuinely empty run never produces one
                # and times out). Trade-off: a run that starts AND finishes
                # within one poll, never leaving 'finished', times out rather
                # than returns — failing safe is preferred over false-passing a
                # turn, and a real agent turn spans several polls.
                reply = _reply_from_events(new_events)
                if reply is not None:
                    return reply
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"No agent reply on conversation {conversation_id} after "
                    f"{timeout_seconds:g}s."
                )
            # max(0, …) so a non-positive poll interval degrades to a busy-wait
            # bounded by the timeout rather than raising from time.sleep().
            time.sleep(max(0.0, min(poll_interval_seconds, remaining)))
