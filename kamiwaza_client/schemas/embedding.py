# kamiwaza_client/schemas/embedding.py

from pydantic import BaseModel, Field
from typing import Any, List, Optional

class EmbeddingInput(BaseModel):
    text: str = Field(description="The text to generate embedding for")
    model: Optional[Any] = Field(default=None, description="The model to use for generating the embedding")
    max_length: int = Field(default=382, description="Maximum token count of each chunk")
    overlap: int = Field(default=32, description="Number of tokens to overlap between chunks when chunking")
    preamble_text: str = Field(default="", description="Text to prepend to each chunk")

class EmbeddingOutput(BaseModel):
    embedding: List[float] = Field(description="The generated embedding")

    model_config = {
        "from_attributes": True
    }