# kamiwaza_client/services/embedding.py

from typing import List, Optional, Any, Dict, Union
from uuid import UUID
from ..schemas.embedding import EmbeddingInput, EmbeddingOutput, EmbeddingConfig
from .base_service import BaseService

class EmbeddingProvider:
    """Provider class for handling embedder-specific operations"""
    
    def __init__(self, service: 'EmbeddingService', embedder_id: Union[str, UUID]):
        self._service = service
        self.embedder_id = str(embedder_id)

    def create_embedding(self, text: str, max_length: int = 382,
                        overlap: int = 32, preamble_text: str = "") -> EmbeddingOutput:
        """Create an embedding for the given text."""
        input_data = EmbeddingInput(
            id=self.embedder_id,
            text=text,
            max_length=max_length,
            overlap=overlap,
            preamble_text=preamble_text
        )
        response = self._service.client.post("/embedding/generate", json=input_data.model_dump())
        return EmbeddingOutput.model_validate(response)

    def get_embedding(self, text: str) -> EmbeddingOutput:
        """Get an embedding for the given text."""
        response = self._service.client.get(
            f"/embedding/generate/{text}",
            params={"embedder_id": self.embedder_id}
        )
        return EmbeddingOutput.model_validate(response)

    def chunk_text(self, text: str, max_length: int = 510, overlap: int = 32,
                   preamble_text: str = "") -> List[str]:
        """Chunk the given text into smaller pieces."""
        params = {
            "text": text,
            "max_length": max_length,
            "overlap": overlap,
            "preamble_text": preamble_text,
            "embedder_id": self.embedder_id
        }
        return self._service.client.post("/embedding/chunk", params=params)

    def embed_chunks(self, text_chunks: List[str], batch_size: int = 64) -> List[List[float]]:
        """Generate embeddings for a list of text chunks."""
        params = {
            "batch_size": batch_size,
            "embedder_id": self.embedder_id
        }
        return self._service.client.post("/embedding/batch", 
                                       params=params, 
                                       json=text_chunks)

    def reset_model(self) -> Dict[str, str]:
        """Reset the embedding model."""
        return self._service.client.post(
            "/embedding/reset",
            params={"embedder_id": self.embedder_id}
        )

    def call(self, batch: Dict[str, List[Any]], model_name: Optional[str] = None) -> Dict[str, List[Any]]:
        """Generate embeddings for a batch of inputs."""
        params = {
            "embedder_id": self.embedder_id,
            "model_name": model_name
        }
        return self._service.client.post("/embedding/embedding/call", 
                                       params=params, 
                                       json=batch)

    def __del__(self):
        """Cleanup when provider is destroyed"""
        try:
            self._service.client.delete(f"/embedding/{self.embedder_id}")
        except:
            pass

class EmbeddingService(BaseService):
    """Main service class for managing embedding operations"""

    def initialize_provider(
        self, 
        provider_type: str, 
        model: str, 
        device: Optional[str] = None,
        **kwargs
    ) -> EmbeddingProvider:
        """Initialize a new embedding provider"""
        config = EmbeddingConfig(
            provider_type=provider_type,
            model=model,
            device=device,
            **kwargs
        )
        config_data = config.model_dump()
        response = self.client.post("/embedding/initialize", json=config_data)
        return EmbeddingProvider(self, response["id"])

    def SentenceTransformerEmbedding(
        self,
        model: str = 'BAAI/bge-large-en-v1.5',
        device: Optional[str] = None,
        **kwargs
    ) -> EmbeddingProvider:
        """Convenience method for creating SentenceTransformer embedder"""
        return self.initialize_provider(
            provider_type="sentence_transformers",
            model=model,
            device=device,
            **kwargs
        )

    def get_providers(self) -> List[str]:
        """Get list of available embedding providers"""
        return self.client.get("/embedding/providers")

    # Legacy methods for backward compatibility
    def create_embedding(self, text: str, **kwargs) -> EmbeddingOutput:
        """Legacy method - uses default embedder"""
        return self.client.post("/embedding/generate", 
                              json={"text": text, **kwargs})

    def get_embedding(self, text: str) -> EmbeddingOutput:
        """Legacy method - uses default embedder"""
        return self.client.get(f"/embedding/generate/{text}")

    def chunk_text(self, text: str, **kwargs) -> List[str]:
        """Legacy method - uses default embedder"""
        return self.client.post("/embedding/chunk", 
                              params={"text": text, **kwargs})

    def embed_chunks(self, text_chunks: List[str], **kwargs) -> List[List[float]]:
        """Legacy method - uses default embedder"""
        return self.client.post("/embedding/batch", 
                              json=text_chunks, 
                              params=kwargs)

    def reset_model(self) -> Dict[str, str]:
        """Legacy method - uses default embedder"""
        return self.client.post("/embedding/reset")

    def call(self, batch: Dict[str, List[Any]], **kwargs) -> Dict[str, List[Any]]:
        """Legacy method - uses default embedder"""
        return self.client.post("/embedding/embedding/call", 
                              json=batch, 
                              params=kwargs)