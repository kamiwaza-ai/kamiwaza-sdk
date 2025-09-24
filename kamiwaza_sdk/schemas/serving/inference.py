# kamiwaza_sdk/schemas/serving/inference.py

from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID

class Message(BaseModel):
    role: str
    content: str

class UnloadModelResponse(BaseModel):
    result: str

class UnloadModelRequest(BaseModel):
    pass

class LoadModelResponse(BaseModel):
    result: str

class GenerateRequest(BaseModel):
    messages: List[Message]
    system_message: Optional[str] = Field(default=None, description="The system message to be included in the prompt")

class GenerateResponse(BaseModel):
    messages: List[Message]

class LoadModelRequest(BaseModel):
    model: int
    weights_file: Optional[int] = None
    config_file: Optional[int] = None

class DeployModelRequest(BaseModel):
    model: int = Field(description="Kamiwaza model id")
    dtype: Optional[str] = Field(default=None, description="Data type for model weights")
    served_model_name: Optional[str] = Field(default=None, description="Name under which the model will be served")
    host: Optional[str] = Field(default=None, description="Host for serving the model")
    port: Optional[int] = Field(default=None, description="Port for serving the model")
    max_model_len: Optional[int] = Field(default=None, description="Maximum model length")
    max_num_seqs: Optional[int] = Field(default=None, description="Maximum number of sequences")
    tensor_parallel_size: Optional[int] = Field(default=None, description="Size of tensor parallelism")
    swap_space: Optional[int] = Field(default=None, description="Swap space size in GB")
    gpu_memory_utilization: Optional[float] = Field(default=None, description="GPU memory utilization ratio")
    enforce_eager: bool = Field(default=False, description="Enforce eager execution")
    disable_log_requests: bool = Field(default=False, description="Disable logging of requests")
    m_config: Optional[UUID] = Field(default=None, description="Reference to DBModelConfig")

    def __str__(self):
        return (
            f"DeployModelRequest:\n"
            f"Model ID: {self.model}\n"
            f"Config ID: {self.m_config}\n"
            f"Copies: {self.starting_copies}"
        )
    
    def __repr__(self):
        return self.__str__()

    def all_attributes(self):
        return "\n".join(f"{key}: {value}" for key, value in self.model_dump().items())

model_config = {
    "from_attributes": True
}

