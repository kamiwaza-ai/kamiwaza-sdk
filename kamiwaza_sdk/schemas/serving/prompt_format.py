# kamiwaza_sdk/schemas/serving/prompt_format.py

from pydantic import BaseModel, Field
from typing import Optional

class PromptFormat(BaseModel):
    prompt_format: str = Field(default="{preamble}{system_header}{newline_after_system_header}{system_message}{newlines_after_system}{user_header}{newline_after_user_header}{prompt}{newlines_after_user}", description="The format of the prompt")
    preamble: Optional[str] = Field(default='', description="The preamble of the prompt")
    system_header: Optional[str] = Field(default='### System Message', description="The system header of the prompt")
    newline_after_system_header: Optional[str] = Field(default='\n', description="Newline after system header")
    newlines_after_system: Optional[str] = Field(default='\n\n', description="Newlines after system message")
    user_header: Optional[str] = Field(default='### User Message', description="The user header of the prompt")
    newline_after_user_header: Optional[str] = Field(default='\n', description="Newline after user header")
    newlines_after_user: Optional[str] = Field(default='\n\n', description="Newlines after user message")
    assistant_header: Optional[str] = Field(default='### Assistant', description="The assistant header of the prompt")
    newline_after_assistant_header: Optional[str] = Field(default='\n', description="Newline after assistant header")
    newlines_after_assistant: Optional[str] = Field(default='\n\n', description="Newlines after assistant message")