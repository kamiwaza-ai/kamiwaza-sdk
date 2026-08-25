# kamiwaza_sdk/schemas/kaizen.py

"""Pydantic models for the Kaizen agent extension API.

Kaizen is a per-workroom extension (one deployment per workroom, pinned via
``KAMIWAZA_WORKROOM_ID``), reached at its own ingress rather than the platform
API root. These models mirror the extension's request/response shapes for the
agent + conversation create paths.

Two agent-create contracts ship side by side and are **not** interchangeable:

* Canonical Kaizen (catalog identity ``kaizen``) takes an :class:`AgentDefinition`
  wrapped in a ``content`` envelope. It has no per-agent model binding at all —
  a model is bound instance-wide through the ops chat-model setting, or picked
  per turn by the caller.
* Legacy Kaizen (catalog identity ``kaizen-legacy``) takes a flat body carrying
  :class:`LLMConfig` under ``agent_config.llm``.
"""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class LLMConfig(BaseModel):
    """Model binding for an agent (``agent_config.llm``).

    A Kamiwaza-deployed model uses ``provider="kamiwaza"`` + ``endpoint_path``;
    a custom OpenAI-compatible endpoint omits ``provider`` and sets ``base_url``.
    """

    model_config = ConfigDict(extra="allow")

    model: str = Field(..., description="Model name passed to the agent runtime")
    provider: Optional[str] = Field(
        default=None, description="'kamiwaza' for a platform deployment, else omit"
    )
    base_url: Optional[str] = Field(
        default=None, description="Custom OpenAI-compatible endpoint URL"
    )
    endpoint_path: Optional[str] = Field(
        default=None, description="Kamiwaza deployment path (provider=kamiwaza)"
    )
    temperature: Optional[float] = None
    timeout: Optional[int] = None


class AgentDefinition(BaseModel):
    """The canonical Kaizen agent ``content`` body.

    Deliberately carries no model binding: canonical Kaizen resolves a model
    from the instance-wide chat-model setting (see
    ``KaizenOpsService.set_chat_model``) or from the caller's per-turn choice,
    never from the agent definition. Sending ``llm`` / ``llm_api_key`` here is
    rejected by the server, and adding them would cut against that boundary.

    Only ``name`` and ``persona`` are required; the server defaults the rest.
    Unset optional fields are omitted from the wire body so the server's own
    defaults apply rather than a client-side guess at them.

    ``extra="forbid"`` mirrors the server, which forbids extras on this body.
    Allowing them here would let a misspelled field ride onto the wire, where
    the intended field silently takes its default and the server answers 422 —
    the opaque failure this contract split exists to remove, one layer up.
    (Response models stay ``extra="allow"`` for forward compatibility; this is
    a request model, so it mirrors instead.)
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Agent name; must be unique in the instance")
    persona: str = Field(..., description="System persona driving the agent")
    description: Optional[str] = None
    capability_ceiling: Optional[str] = Field(
        default=None, description="Capability tier; server defaults to 'read'"
    )
    mode: Optional[str] = Field(
        default=None, description="Authored surface; server defaults to 'chat'"
    )
    routing: Optional[Dict[str, Any]] = None
    granted_package_ids: Optional[List[str]] = None
    collection_ids: Optional[List[str]] = None
    prompt_ids: Optional[List[str]] = None
    posture: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None

    def to_content(self) -> Dict[str, Any]:
        """The exact ``content`` dict to send, with unset fields omitted."""
        return self.model_dump(exclude_none=True)


class Agent(BaseModel):
    """Agent response from Kaizen.

    Spans both create contracts: canonical Kaizen answers a create with just
    ``{"id", "version"}``, while legacy Kaizen echoes the stored agent. ``id``
    is the one field both return, and is what callers key on.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    # Canonical Kaizen returns an int here. The type stays wide because this
    # model is shared with the legacy create path, and narrowing it would turn
    # a legacy response carrying a non-int version into a ValidationError on a
    # path that works today.
    version: Optional[Union[int, str]] = None
    name: Optional[str] = None
    description: Optional[str] = None
    workroom_id: Optional[str] = None
    agent_config: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None


class CanonicalConversation(BaseModel):
    """Conversation as canonical Kaizen returns it on create.

    Deliberately not :class:`Conversation`: canonical Kaizen returns the id
    alone and binds no agent at create time (the agent is selected per input),
    so reusing the legacy model would fail validation on its required
    ``agent_id``.
    """

    model_config = ConfigDict(extra="allow")

    id: str


class ConversationInputAccepted(BaseModel):
    """Canonical Kaizen's 202 response to an accepted conversation input.

    ``accepted_position`` is the input's position in the conversation journal;
    replaying the event stream from it is what makes reading the reply
    race-free when the agent finishes before the stream is open.
    """

    model_config = ConfigDict(extra="allow")

    input_id: str
    accepted_position: int
    status: Optional[str] = None


class Conversation(BaseModel):
    """Conversation response from Kaizen (sandbox auto-started on create)."""

    model_config = ConfigDict(extra="allow")

    id: str
    agent_id: str
    workroom_id: Optional[str] = None
    title: Optional[str] = None
    execution_status: Optional[str] = None
    container_status: Optional[str] = None
    created_at: Optional[str] = None
