# kamiwaza_sdk/schemas/kaizen.py

"""Pydantic models for the Kaizen agent extension API.

Kaizen is a per-workroom extension (one deployment per workroom, pinned via
``KAMIWAZA_WORKROOM_ID``), reached at its own ingress rather than the platform
API root. These models mirror ``kamiwaza-extensions-kaizen`` request/response
shapes for the agent + conversation create paths.
"""

from typing import Any, Dict, Optional

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


class Agent(BaseModel):
    """Agent response from Kaizen."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: Optional[str] = None
    description: Optional[str] = None
    workroom_id: Optional[str] = None
    agent_config: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None


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
