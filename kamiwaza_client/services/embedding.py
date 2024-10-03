# kamiwaza_client/services/embedding.py

from typing import List, Optional, Any, Dict
from ..schemas.embedding import EmbeddingInput, EmbeddingOutput
from .base_service import BaseService

class EmbeddingService(BaseService):
    def create_embedding(self, text: str, model: Optional[Any] = None, max_length: int = 382,
                         overlap: int = 32, preamble_text: str = "") -> EmbeddingOutput:
        """Create an embedding for the given text."""
        input_data = EmbeddingInput(
            text=text,
            model=model,
            max_length=max_length,
            overlap=overlap,
            preamble_text=preamble_text
        )
        response = self.client.post("/embedding/embedding/", json=input_data.model_dump())
        return EmbeddingOutput.model_validate(response)

    def get_embedding(self, text: str) -> EmbeddingOutput:
        """Get an embedding for the given text."""
        response = self.client.get(f"/embedding/embedding/{text}")
        return EmbeddingOutput.model_validate(response)

    def chunk_text(self, text: str, max_length: int = 510, overlap: int = 32,
                   preamble_text: str = "") -> List[str]:
        """Chunk the given text into smaller pieces."""
        params = {
            "text": text,
            "max_length": max_length,
            "overlap": overlap,
            "preamble_text": preamble_text
        }
        return self.client.post("/embedding/embedding/chunk_text", params=params)

    def embed_chunks(self, text_chunks: List[str], batch_size: int = 64) -> List[List[float]]:
        """Generate embeddings for a list of text chunks."""
        params = {"batch_size": batch_size}
        return self.client.post("/embedding/embedding/embed_chunks", params=params, json=text_chunks)

    def reset_model(self) -> Dict[str, str]:
        """Reset the embedding model."""
        return self.client.post("/embedding/embedding/reset_model")

    def call(self, batch: Dict[str, List[Any]], model_name: Optional[str] = None) -> Dict[str, List[Any]]:
        """Generate embeddings for a batch of inputs."""
        params = {"model_name": model_name} if model_name else None
        return self.client.post("/embedding/embedding/call", params=params, json=batch)