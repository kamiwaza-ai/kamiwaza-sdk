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

import math
import re
import time
from typing import Any, Dict, List, Optional, Union

from ..exceptions import APIError, AuthorizationError, KamiwazaError, NotFoundError
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
    another workroom's instance) and the complete operator name. The suffix is one
    to sixteen lowercase hex characters, depending on the workroom ID.
    Matching the whole shape prevents ``kaizen`` from adopting ``kaizen-next-*``.
    Requires the client
    to be scoped into the workroom — the platform only lists a workroom's
    extensions to a caller scoped into it.

    Raises:
        ValueError: when no instance is visible yet (still provisioning) —
            transient, callers may retry.
        AmbiguousExtensionError: when more than one matches (a workroom should
            hold one) — deterministic, callers must not retry.
    """
    operator_name = re.compile(rf"^{re.escape(extension_name)}-[0-9a-f]{{1,16}}$")
    matches = [
        ext
        for ext in client.extensions.list_extensions(workroom_id=workroom_id)
        if str(getattr(ext, "workroom_id", "")) == str(workroom_id)
        and operator_name.fullmatch(ext.name)
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


# Statuses that are retryable regardless of resolve scope. The full policy
# (no-status, 5xx, scoped 403) lives in _is_transient_resolve_error.
_TRANSIENT_RESOLVE_STATUSES = frozenset({429})


def _is_transient_resolve_error(exc: KamiwazaError, *, workroom_scoped: bool) -> bool:
    """True if an error from ``resolve_base_url`` is a transient startup state.

    Transient: no status (transport error before any response), any 5xx
    (gateway/upstream not ready), 429 (rate limited), and — only when the
    resolve is scoped to a workroom — 403 (the workroom's rebac grant may
    still be propagating on a fresh box). On the unscoped path a 403 is a
    genuine permission denial that can't clear on its own, so it propagates
    instead of burning the timeout into an opaque ``TimeoutError``.

    Accepts any ``KamiwazaError`` because a rebac 403 surfaces as an
    ``AuthorizationError`` subclass rather than ``APIError``; both carry
    ``status_code`` from the response boundary.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        return True
    if 500 <= status <= 599:
        return True
    if status == 403:
        return workroom_scoped
    return status in _TRANSIENT_RESOLVE_STATUSES


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
        except (APIError, AuthorizationError) as exc:
            # resolve_base_url lists the workroom's extensions on the platform
            # API; on a freshly-installed box that call can transiently fail
            # while the cluster settles — a 5xx/no-response from the gateway or a
            # 403 before the workroom's rebac grant lands (ENG-7111 sibling).
            # The 403 arrives either as a plain APIError or, when the body
            # carries a recognized detail.reason, as an AuthorizationError
            # subclass (a SIBLING of APIError — e.g.
            # BrokeredUserNotAllowlistedError while the grant propagates), so
            # both are caught and classified by status code.
            # Treat transient ones as "not ready yet" and keep polling; a
            # non-transient error (401 bad token, 400 bad request) can't clear
            # on its own, so surface it now instead of burning the whole timeout.
            if not _is_transient_resolve_error(
                exc, workroom_scoped=workroom_id is not None
            ):
                raise
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
_DONE_EXECUTION_STATUSES = frozenset({"finished", "completed", "done"})
_ERROR_EXECUTION_STATUSES = frozenset({"error", "failed"})
_READY_CONTAINER_STATUSES = frozenset({"active", "ready", "running", "serving"})
_PENDING_CONTAINER_STATUSES = frozenset(
    {
        "creating",
        "initializing",
        "pending",
        "provisioning",
        "pulling",
        "starting",
        "waiting",
    }
)
_TERMINAL_CONTAINER_STATUSES = frozenset(
    {"deleted", "error", "failed", "stopped", "suspended"}
)


def _normalized_status(value: Optional[str]) -> Optional[str]:
    """Normalize a Kaizen status value for case-insensitive comparisons."""
    if value is None:
        return None
    status = str(value).strip().lower()
    return status or None


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


def _assistant_text(message_obj: Dict[str, Any]) -> Optional[str]:
    """Return the joined text blocks from an assistant message, or None."""
    text = "".join(
        block.get("text", "")
        for block in (message_obj.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
    return text or None


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
        if message_obj.get("tool_calls") or event.get("tool_calls"):
            continue
        text = _assistant_text(message_obj)
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

    def get(
        self,
        conversation_id: str,
        *,
        base_url: str,
        workroom_id: Optional[Union[str, object]] = None,
    ) -> Conversation:
        """Fetch a conversation, including execution/container status."""
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
        poll_interval_seconds: float = 3.0,
    ) -> Conversation:
        """Wait until the conversation sandbox is active before messaging it.

        Conversation creation can return while a cold sandbox is still coming
        up. Poll the conversation's ``container_status`` and fail before sending
        a message if the sandbox reaches a known terminal non-serving state.
        Older Kaizen builds may omit this optional field; in that case, proceed
        optimistically to preserve the pre-readiness-check behavior.
        """
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError(
                "timeout_seconds must be a finite zero or positive number of seconds."
            )

        deadline = time.monotonic() + timeout_seconds
        last_status: Optional[str] = None
        while True:
            conversation = self.get(
                conversation_id, base_url=base_url, workroom_id=workroom_id
            )
            last_status = _normalized_status(conversation.container_status)
            if last_status in _READY_CONTAINER_STATUSES:
                return conversation
            if last_status in _TERMINAL_CONTAINER_STATUSES:
                raise ConversationError(
                    f"Conversation {conversation_id} sandbox is not ready "
                    f"(container_status={last_status})."
                )
            if last_status is None or last_status not in _PENDING_CONTAINER_STATUSES:
                return conversation

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Conversation {conversation_id} sandbox not ready after "
                    f"{timeout_seconds:g}s (container_status={last_status or 'unknown'})."
                )
            time.sleep(max(0.0, min(poll_interval_seconds, remaining)))

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

        Wraps :meth:`send_message` + :meth:`run`, then polls :meth:`get_events`
        until the agent's reply lands. Used to exercise an agent end to end
        (e.g. proving a freshly seeded agent actually responds).

        ``timeout_seconds`` is the wait budget: a positive value (the default)
        waits up to that long and returns the reply text; ``0`` is fire-and-forget
        — the message is sent and the run triggered, but the method returns
        ``None`` immediately without waiting. The reply is taken only once the
        agent's run is terminal (a ``finish`` action, an error event, or a
        completed conversation status), so an interim assistant narration before
        the final answer is never mistaken for the reply. Only this turn's
        events are considered (the pre-run event count is the read offset), and
        only the first page of them (``get_events`` default ``limit``); a
        verification turn is a handful of events, so this is not paginated.
        When reusing a conversation, a terminal status that existed before this
        run is treated as stale until the status changes, becomes non-terminal,
        or a same-terminal plain reply is confirmed across consecutive polls.
        Error statuses from a previous turn are not trusted by repetition alone;
        same-status failures need an error event or a real status transition.

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
            ValueError: ``timeout_seconds`` or ``poll_interval_seconds`` is invalid.
            ConversationError: The agent emitted an error event for this turn.
            TimeoutError: The agent did not finish within ``timeout_seconds``.
        """
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError(
                "timeout_seconds must be a finite zero or positive number of seconds."
            )
        if not math.isfinite(poll_interval_seconds) or poll_interval_seconds < 0:
            raise ValueError(
                "poll_interval_seconds must be a finite zero or positive number "
                "of seconds."
            )
        self.send_message(
            conversation_id, message, base_url=base_url, workroom_id=workroom_id
        )
        if not timeout_seconds:
            self.run(conversation_id, base_url=base_url, workroom_id=workroom_id)
            return None

        # Read this turn's output only: events appended from here on. Captured
        # after send (the just-queued user message is excluded) and before run.
        baseline = self.get_events(
            conversation_id, base_url=base_url, workroom_id=workroom_id, limit=1
        ).get("total", 0)
        # This extra status read separates a stale terminal state from this run.
        pre_run_status = _normalized_status(
            self.get(
                conversation_id, base_url=base_url, workroom_id=workroom_id
            ).execution_status
        )
        self.run(conversation_id, base_url=base_url, workroom_id=workroom_id)

        terminal_statuses = _DONE_EXECUTION_STATUSES | _ERROR_EXECUTION_STATUSES
        status_applies_to_this_turn = pre_run_status not in terminal_statuses

        def status_marks_current_turn(execution_status: Optional[str]) -> bool:
            return execution_status is not None and (
                execution_status != pre_run_status
                or execution_status not in terminal_statuses
            )

        deadline = time.monotonic() + timeout_seconds
        saw_done_without_reply = False
        same_terminal_reply_seen: Optional[tuple[str, str]] = None

        def update_status_freshness(execution_status: Optional[str]) -> None:
            nonlocal status_applies_to_this_turn
            nonlocal same_terminal_reply_seen

            if status_marks_current_turn(execution_status):
                status_applies_to_this_turn = True
                same_terminal_reply_seen = None
                return

        def terminal_status_reply(
            execution_status: Optional[str], reply: Optional[str]
        ) -> tuple[bool, Optional[str]]:
            if not status_applies_to_this_turn:
                return False, None
            if execution_status in _ERROR_EXECUTION_STATUSES:
                raise ConversationError(
                    f"Agent errored on conversation {conversation_id} "
                    f"(execution_status={execution_status})."
                )
            if execution_status in _DONE_EXECUTION_STATUSES:
                return True, reply
            return False, None

        while True:
            conversation = self.get(
                conversation_id, base_url=base_url, workroom_id=workroom_id
            )
            execution_status = _normalized_status(conversation.execution_status)
            update_status_freshness(execution_status)

            new_events = self.get_events(
                conversation_id,
                base_url=base_url,
                workroom_id=workroom_id,
                offset=baseline,
            ).get("events", [])
            error = _agent_error_from_events(new_events)
            if error is not None:
                raise ConversationError(
                    f"Agent errored on conversation {conversation_id}: {error}"
                )
            if _has_finish_action(new_events):
                # The run is terminal — take the reply now (the finish message,
                # or the last assistant text if finish carried none, or None).
                # Don't accept interim assistant narration before this point.
                return _reply_from_events(new_events)
            reply = _reply_from_events(new_events)
            is_terminal_status, terminal_reply = terminal_status_reply(
                execution_status, reply
            )
            if is_terminal_status:
                if terminal_reply is not None:
                    return terminal_reply
                if saw_done_without_reply:
                    return None
                # Done can beat reply persistence; give Kaizen one immediate
                # re-read before accepting "done with no reply" as terminal.
                saw_done_without_reply = True
                continue
            saw_done_without_reply = False
            if reply:
                conversation = self.get(
                    conversation_id, base_url=base_url, workroom_id=workroom_id
                )
                execution_status = _normalized_status(conversation.execution_status)
                update_status_freshness(execution_status)
                is_terminal_status, terminal_reply = terminal_status_reply(
                    execution_status, reply
                )
                if is_terminal_status:
                    return terminal_reply
                if (
                    execution_status == pre_run_status
                    and execution_status in _DONE_EXECUTION_STATUSES
                ):
                    # Fast reused runs may remain `finished` the whole time.
                    # Require stability so a single stale/interim reply is not
                    # accepted just because the previous turn was already done.
                    same_terminal_reply = (execution_status, reply)
                    if same_terminal_reply_seen == same_terminal_reply:
                        return reply
                    same_terminal_reply_seen = same_terminal_reply
                    # Fall through to the deadline/sleep below rather than
                    # looping immediately: a reused conversation stuck terminal
                    # with a *changing* reply would otherwise never match here,
                    # busy-waiting forever and bypassing the timeout contract.
            else:
                same_terminal_reply_seen = None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"No agent reply on conversation {conversation_id} after "
                    f"{timeout_seconds:g}s."
                )
            # max(0, …) keeps zero poll intervals bounded by the timeout rather
            # than raising from time.sleep().
            time.sleep(max(0.0, min(poll_interval_seconds, remaining)))
