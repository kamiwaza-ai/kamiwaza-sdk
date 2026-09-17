"""Provider-neutral resource markings and their server-produced presentation."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MarkingsModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Marking(MarkingsModel):
    """Normalized metadata; the selected server provider validates attributes."""

    profile_id: str = Field(min_length=1)
    profile_revision: str = Field(min_length=1)
    level_id: str = Field(min_length=1)
    raw_text: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class MarkingDisplay(MarkingsModel):
    text: str
    background_color: str
    foreground_color: str


class MarkingLevel(MarkingsModel):
    id: str
    name: str
    rank: int
    background_color: str
    foreground_color: str
    assignable: bool = True


class MarkingsConfig(MarkingsModel):
    enabled: bool
    profile_id: str | None = None
    profile_revision: str | None = None
    levels: list[MarkingLevel] = Field(default_factory=list)
    defaults: dict[str, str] = Field(default_factory=dict)


class ParsedMarking(MarkingsModel):
    marking: Marking | None = None
    display: MarkingDisplay | None = None


class ComposedMarkings(MarkingsModel):
    top: MarkingDisplay | None = None
    bottom: MarkingDisplay | None = None
