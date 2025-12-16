# kamiwaza_sdk/schemas/embedding.py


from pydantic import BaseModel, Field, ConfigDict
from typing import Any, List, Optional
from uuid import UUID, uuid4

class EmbeddingInput(BaseModel):
    id: Optional[UUID] = None
    text: str = Field(description="The text to generate embedding for")
    model: Optional[Any] = Field(default=None, description="The model to use for generating the embedding")
    max_length: int = Field(default=382, description="Maximum token count of each chunk")
    overlap: int = Field(default=32, description="Number of tokens to overlap between chunks when chunking")
    preamble_text: str = Field(default="", description="Text to prepend to each chunk")

    model_config = ConfigDict(
        json_serialization={'uuid_mode': 'str'}
    )

    def model_dump(self):
        data = super().model_dump()
        if data['id']:
            data['id'] = str(data['id'])
        return data

class EmbeddingOutput(BaseModel):
    embedding: List[float]
    offset: Optional[int] = None
class EmbeddingConfig(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    provider_type: str
    model: str
    device: Optional[str] = None
    additional_config: Optional[dict] = {}

    model_config = ConfigDict(
        json_serialization={'uuid_mode': 'str'}  # Convert UUID to string during serialization
    )
        
    def model_dump(self):
        # Override model_dump to ensure UUID is converted to string
        data = super().model_dump()
        data['id'] = str(data['id'])
        return data
    

class ChunkResponse(BaseModel):
    chunks: List[str]
    offsets: Optional[List[int]] = None
    token_counts: Optional[List[int]] = None
    metadata: Optional[List[dict]] = None
