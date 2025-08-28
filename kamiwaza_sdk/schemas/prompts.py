# kamiwaza_sdk/schemas/prompts.py

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from uuid import UUID

class TagBase(BaseModel):
    name: str

class Tag(TagBase):
    id: UUID

class TaggableBase(BaseModel):
    tags: List[str] = Field(default_factory=list)

class PromptRoleBase(TaggableBase):
    name: str
    content: str

class PromptRoleCreate(PromptRoleBase):
    pass

class PromptRole(PromptRoleBase):
    id: UUID
    version: int
    created_at: str

class PromptSystemBase(TaggableBase):
    name: str
    content: str

class PromptSystemCreate(PromptSystemBase):
    pass

class PromptSystem(PromptSystemBase):
    id: UUID
    version: int
    created_at: datetime

class PromptElementBase(TaggableBase):
    name: str
    content: str

class PromptElementCreate(PromptElementBase):
    pass

class PromptElement(PromptElementBase):
    id: UUID
    version: int
    created_at: str

class PromptTemplateBase(TaggableBase):
    name: str
    content: str

class PromptTemplateCreate(PromptTemplateBase):
    pass

class PromptTemplate(PromptTemplateBase):
    id: UUID
    version: int
    created_at: str

class PromptStatBase(BaseModel):
    template_id: UUID
    version: int
    m_name: str
    temperature: float
    usage_count: int = 0
    success_count: int = 0
    fail_count: int = 0

class PromptStatCreate(PromptStatBase):
    pass

class PromptStat(PromptStatBase):
    id: UUID
    version: int
    created_at: str

class PromptFeedbackBase(BaseModel):
    prompt_id: UUID
    user_id: UUID
    feedback_text: Optional[str] = None
    feedback_rating: int

class PromptFeedbackCreate(PromptFeedbackBase):
    pass

class PromptFeedback(PromptFeedbackBase):
    id: UUID
    version: int
    created_at: str

class PromptFeedbackStatBase(BaseModel):
    prompt_id: UUID
    average_rating: float
    total_feedbacks: int

class PromptFeedbackStatCreate(PromptFeedbackStatBase):
    pass

class PromptFeedbackStat(PromptFeedbackStatBase):
    id: UUID
    version: int
    created_at: str

model_config = {
    "from_attributes": True
}